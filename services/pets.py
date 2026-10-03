"""حیوان — pets and pet battles (buy / upgrade / challenge / settle).

Stakes move only through :mod:`services.economy`; the challenger's bet is
escrowed when the challenge is posted and the pot settles (or refunds)
inside a single ``db.write()`` transaction. Every number lives in
``config.settings``.
"""

from __future__ import annotations

import logging
import random
from typing import Any

from config import settings
from database.connection import db
from models import ActivityKind
from models.player import Player
from services import economy
from services.game import GameError, now

logger = logging.getLogger(__name__)


def upgrade_cost(level: int) -> int:
    """Cost to go ``level -> level + 1`` (growth curve from config)."""
    return int(
        settings.pet_upgrade_cost_base
        * (settings.pet_upgrade_cost_growth ** max(0, level - 1))
    )


def battle_score(pet_level: int, roll: float) -> float:
    """Deterministic strength: raw roll + per-level edge from config."""
    return roll + pet_level * settings.pet_level_bonus


async def status(player: Player) -> dict[str, Any]:
    """Pet sheet + the battle rules."""
    level = player.pet_level
    if level <= 0:
        owned = f"نداری · خرید: <b>{settings.pet_base_cost:,}</b> سکه"
    elif level >= settings.pet_max_level:
        owned = f"لول <b>{level}</b> — سقف مهارتش رو زدی 🔝"
    else:
        owned = (
            f"لول <b>{level}</b> · ارتقا: "
            f"<b>{upgrade_cost(level):,}</b> سکه"
        )
    return {
        "success": True,
        "message": (
            "🐺 <b>حیوان خیابان</b>\n"
            f"وضعیت: {owned}\n"
            f"نبرد: <code>حیوان نبرد [مبلغ]</code> (ریپلای حریف)\n"
            f"محدوده مبلغ: {settings.pet_battle_min:,} تا "
            f"{settings.pet_battle_max:,} سکه · سهم خانه "
            f"{settings.pet_battle_rake * 100:g}٪\n"
            "قبول: ریپلای روی چالش، بعد <code>حیوان نبرد</code> · "
            "رد: <code>حیوان نبرد رد</code> · "
            "لغو (چالشگر): <code>حیوان نبرد لغو</code>\n"
            f"هر لول {settings.pet_level_bonus:g}+ امتیاز به نبرد می‌ده."
        ),
    }


async def buy(player: Player) -> dict[str, Any]:
    """First pet. One animal per player, forever."""
    if player.pet_level > 0:
        raise GameError("از قبل حیوان داری؛ <code>حیوان ارتقا</code> بزن.")
    cost = settings.pet_base_cost
    async with db.write() as conn:
        cursor = await conn.execute(
            "SELECT pet_level FROM players WHERE user_id = ?",
            (player.user_id,),
        )
        row = await cursor.fetchone()
        await cursor.close()
        if row and int(row["pet_level"]) > 0:
            raise GameError("از قبل حیوان داری؛ <code>حیوان ارتقا</code> بزن.")
        await economy.mutate(
            conn,
            player.user_id,
            credits=-cost,
            kind=ActivityKind.PET,
            ref="pet:buy",
        )
        await conn.execute(
            "UPDATE players SET pet_level = 1 WHERE user_id = ?",
            (player.user_id,),
        )
    player.pet_level = 1
    return {
        "success": True,
        "level": 1,
        "message": (
            f"🐺 سگ ولگرد خیابان افتاد توی بساطت — لول ۱! "
            f"<b>{cost:,}</b> سکه هم رفت خریدش."
        ),
    }


async def upgrade(player: Player) -> dict[str, Any]:
    """Level the pet up by one, at the config growth price."""
    if player.pet_level <= 0:
        raise GameError("اول یه حیوان بخر: <code>حیوان خرید</code>")
    if player.pet_level >= settings.pet_max_level:
        raise GameError(
            f"حیوانت به سقف لول {settings.pet_max_level} رسیده."
        )
    cost = upgrade_cost(player.pet_level)
    async with db.write() as conn:
        cursor = await conn.execute(
            "SELECT pet_level FROM players WHERE user_id = ?",
            (player.user_id,),
        )
        row = await cursor.fetchone()
        await cursor.close()
        current = int(row["pet_level"]) if row else 0
        if current <= 0:
            raise GameError("اول یه حیوان بخر: <code>حیوان خرید</code>")
        if current >= settings.pet_max_level:
            raise GameError(
                f"حیوانت به سقف لول {settings.pet_max_level} رسیده."
            )
        await economy.mutate(
            conn,
            player.user_id,
            credits=-cost,
            kind=ActivityKind.PET,
            ref="pet:upgrade",
        )
        await conn.execute(
            "UPDATE players SET pet_level = ? WHERE user_id = ?",
            (current + 1, player.user_id),
        )
        new_level = current + 1
    player.pet_level = new_level
    return {
        "success": True,
        "level": new_level,
        "message": (
            f"🐺 حیوانت رفت لول <b>{new_level}</b> — "
            f"<b>{cost:,}</b> سکه خرج تعلیمش شد."
        ),
    }


async def challenge(
    challenger: Player, target: Player, amount: int, chat_id: int = 0
) -> dict[str, Any]:
    """Post a fight: the challenger's stake leaves their wallet NOW."""
    if challenger.user_id == target.user_id:
        raise GameError("با خودت که نبرد نمی‌کنی؛ ریپلای یکی دیگه رو بکن.")
    if challenger.pet_level <= 0:
        raise GameError("حیوان نداری که بفرستی میدون: <code>حیوان خرید</code>")
    if not settings.pet_battle_min <= amount <= settings.pet_battle_max:
        raise GameError(
            f"مبلغ باید بین {settings.pet_battle_min:,} و "
            f"{settings.pet_battle_max:,} سکه باشه."
        )

    async with db.write() as conn:
        # Re-read the challenger inside the txn: pet + balance checks.
        cursor = await conn.execute(
            "SELECT pet_level FROM players WHERE user_id = ?",
            (challenger.user_id,),
        )
        row = await cursor.fetchone()
        await cursor.close()
        if not row or int(row["pet_level"]) <= 0:
            raise GameError(
                "حیوان نداری که بفرستی میدون: <code>حیوان خرید</code>"
            )

        await economy.mutate(
            conn,
            challenger.user_id,
            credits=-amount,
            kind=ActivityKind.PET,
            ref="pet:stake",
        )
        expires = now() + settings.pet_challenge_ttl
        cursor = await conn.execute(
            "INSERT INTO pet_challenges (challenger_id, target_id, amount, "
            "chat_id, status, created_at, expires_at) "
            "VALUES (?, ?, ?, ?, 'pending', ?, ?)",
            (challenger.user_id, target.user_id, amount, chat_id, now(), expires),
        )
        challenge_id = int(cursor.lastrowid or 0)
        await cursor.close()

    return {
        "success": True,
        "challenge_id": challenge_id,
        "amount": amount,
        "message": (
            f"⚔️ <b>{challenger.display_name} چالش نبرد فرستاد</b>\n"
            f"حریف: {target.display_name} · مبلغ: <b>{amount:,}</b> سکه "
            f"(از کیف توک گروته و پیش من امانته)\n"
            f"برای قبول: ریپلای روی همین پیام و <code>حیوان نبرد</code> · "
            f"رد: <code>حیوان نبرد رد</code> · "
            f"لغو: <code>حیوان نبرد لغو</code>\n"
            f"تا {settings.pet_challenge_ttl // 60} دقیقه دیگه معتبره."
        ),
    }


async def _pending(conn, challenger_id: int, target_id: int) -> dict[str, Any] | None:
    cursor = await conn.execute(
        "SELECT * FROM pet_challenges WHERE challenger_id = ? AND target_id = ? "
        "AND status = 'pending' ORDER BY id DESC LIMIT 1",
        (challenger_id, target_id),
    )
    row = await cursor.fetchone()
    await cursor.close()
    return dict(row) if row else None


async def _refund(conn, row: dict[str, Any]) -> None:
    await economy.mutate(
        conn,
        int(row["challenger_id"]),
        credits=int(row["amount"]),
        kind=ActivityKind.PET,
        ref="pet:refund",
    )


async def accept(
    target: Player,
    challenger: Player,
    *,
    roll_target: float | None = None,
    roll_challenger: float | None = None,
) -> dict[str, Any]:
    """Settle the newest pending fight between this pair (target accepts)."""
    if target.pet_level <= 0:
        raise GameError("حیوان نداری که بجنگی: <code>حیوان خرید</code>")

    async with db.write() as conn:
        row = await _pending(conn, challenger.user_id, target.user_id)
        if row is None:
            raise GameError("چالش نبرد معتبری بین شما نیست.")
        if int(row["expires_at"]) < now():
            # Commit the refund first — raising inside the txn would roll it back,
            # so the expired path returns normally instead of raising.
            await _refund(conn, row)
            await conn.execute(
                "UPDATE pet_challenges SET status = 'expired', resolved_at = ? "
                "WHERE id = ?",
                (now(), int(row["id"])),
            )
            return {
                "success": False,
                "expired": True,
                "message": (
                    "⌛ چالش نبرد منقضی شده بود؛ پول چالشگر برگشت "
                    f"({int(row['amount']):,} سکه)."
                ),
            }

        cursor = await conn.execute(
            "SELECT user_id, pet_level FROM players WHERE user_id IN (?, ?)",
            (challenger.user_id, target.user_id),
        )
        pet_levels = {
            int(r["user_id"]): int(r["pet_level"])
            for r in await cursor.fetchall()
        }
        await cursor.close()
        if pet_levels.get(challenger.user_id, 0) <= 0:
            raise GameError(
                "حریف دیگه حیوان نداره؛ چالش رو لغو کن: <code>حیوان نبرد لغو</code>"
            )
        if pet_levels.get(target.user_id, 0) <= 0:
            raise GameError(
                "حیوان نداری که بجنگی: <code>حیوان خرید</code>"
            )

        amount = int(row["amount"])
        pot = amount * 2
        rake = int(pot * settings.pet_battle_rake)
        score_c = battle_score(
            pet_levels[challenger.user_id],
            random.random() if roll_challenger is None else roll_challenger,
        )
        score_t = battle_score(
            pet_levels[target.user_id],
            random.random() if roll_target is None else roll_target,
        )

        if score_c == score_t:
            # Push: both stakes go back, the house takes nothing.
            await _refund(conn, row)
            await conn.execute(
                "UPDATE pet_challenges SET status = 'declined', resolved_at = ? "
                "WHERE id = ?",
                (now(), int(row["id"])),
            )
            return {
                "success": True,
                "draw": True,
                "message": (
                    f"🤝 نبرد مساوی شد ({score_c:.2f} در برابر {score_t:.2f}) — "
                    f"هر دو پولتون پس اومد."
                ),
            }

        winner = challenger if score_c > score_t else target
        loser = target if score_c > score_t else challenger
        payout = pot - rake
        await economy.mutate(
            conn,
            winner.user_id,
            credits=payout,
            kind=ActivityKind.PET,
            ref="pet:payout",
        )
        await conn.execute(
            "UPDATE pet_challenges SET status = 'resolved', winner_id = ?, "
            "resolved_at = ? WHERE id = ?",
            (winner.user_id, now(), int(row["id"])),
        )

    return {
        "success": True,
        "draw": False,
        "winner_id": winner.user_id,
        "payout": payout,
        "rake": rake,
        "message": (
            f"🏆 <b>{winner.display_name} برد نبرد!</b>\n"
            f"امتیاز: {max(score_c, score_t):.2f} در برابر "
            f"{min(score_c, score_t):.2f} — "
            f"حریف {loser.display_name} شکست خورد.\n"
            f"جایزه: <b>{payout:,}</b> سکه "
            f"(سهم خانه {rake:,} سوخت)."
        ),
    }


async def decline(target: Player, challenger: Player) -> dict[str, Any]:
    """The target turns the fight down; the escrow goes back."""
    async with db.write() as conn:
        row = await _pending(conn, challenger.user_id, target.user_id)
        if row is None:
            raise GameError("چالش نبرد معتبری بین شما نیست.")
        await _refund(conn, row)
        await conn.execute(
            "UPDATE pet_challenges SET status = 'declined', resolved_at = ? "
            "WHERE id = ?",
            (now(), int(row["id"])),
        )
    return {
        "success": True,
        "message": (
            f"🏳️ {target.display_name} نبرد رو رد کرد؛ "
            f"<b>{int(row['amount']):,}</b> سکه چالشگر برگشت."
        ),
    }


async def cancel(challenger: Player, target: Player) -> dict[str, Any]:
    """The challenger withdraws their own pending fight."""
    async with db.write() as conn:
        row = await _pending(conn, challenger.user_id, target.user_id)
        if row is None:
            raise GameError("چالش نبرد معتبری بین شما نیست.")
        await _refund(conn, row)
        await conn.execute(
            "UPDATE pet_challenges SET status = 'cancelled', resolved_at = ? "
            "WHERE id = ?",
            (now(), int(row["id"])),
        )
    return {
        "success": True,
        "message": (
            f"↩️ چالش نبرد لغو شد؛ <b>{int(row['amount']):,}</b> سکه "
            f"برگشت کیف {challenger.display_name}."
        ),
    }
