"""Street security: anti-theft cover (سپر) and bank robbery (دستبرد).

``سپر`` is a paid 24h insurance: while ``players.shield_until`` is in the
future, :func:`services.tiramix.attempt_theft` short-circuits into a clean
miss — no loot changes hands and the thief is neither jailed nor fined.

``دستبرد`` is the heavyweight cousin of street theft: the stake is escrowed
first (so a losing roll cannot dodge the debit), a win pays
``stake * bank_heist_payout_multiplier``, and a loss costs either
``stake * bank_heist_fine_multiplier`` when the player can afford it — or jail
time when they cannot.  ``roll`` is injectable purely so tests can exercise
both branches deterministically; production callers omit it.
"""

from __future__ import annotations

import secrets
import time
from typing import Any

from config import settings
from database.connection import db
from models.enums import ActivityKind
from models.player import Player
from services import economy
from services.game import GameError, grant_exp_conn, spend_energy_conn

# Cooldown action names (``economy.require_ready`` keys).
ACTION_SHIELD = "shield"
ACTION_BANK_HEIST = "bank_heist"


def shield_cost(cash: int) -> int:
    """Price of a 24h cover: a percentage of cash, clamped by config."""
    raw = int(cash * settings.shield_cost_pct)
    return max(settings.shield_cost_min, min(settings.shield_cost_max, raw))


def has_active_shield(player: Player, current: int | None = None) -> bool:
    current = int(time.time()) if current is None else current
    return player.shield_until > current


def bank_heist_success_chance(drip: int, amount: int) -> float:
    """Base chance nudged by drip luck, penalised for oversized stakes."""
    size_penalty = settings.bank_heist_size_penalty * (
        amount / max(1, settings.bank_heist_stake_max)
    )
    chance = (
        settings.bank_heist_base_success
        + drip * settings.bank_heist_drip_bonus
        - size_penalty
    )
    return min(
        settings.bank_heist_max_success,
        max(settings.bank_heist_min_success, chance),
    )


async def _owns_tool(user_id: int) -> bool:
    """Phase-3 «ابزار سرقت» bonus hook: +success chance when in inventory."""
    row = await db.fetchone(
        "SELECT 1 AS ok FROM inventory WHERE user_id = ? AND item_id = ?",
        (user_id, settings.bank_heist_tool_item),
    )
    return row is not None


async def shield_status(player: Player) -> dict[str, Any]:
    """Current cover state + the price of buying/renewing it."""
    current = int(time.time())
    cash, _ = await economy.balances(player.user_id)
    cost = shield_cost(cash)
    active = has_active_shield(player, current)
    lines = ["🛡️ <b>سپر ضدسرقت تیرامیکس</b>", ""]
    if active:
        remaining = player.shield_until - current
        hours, remainder = divmod(remaining, 3600)
        minutes = remainder // 60
        lines.append(
            f"وضعیت: <b>فعال</b> ✅ — تا {hours} ساعت و {minutes} دقیقه دیگر"
        )
        lines.append("تا وقتی فعال است، هیچ دزدی نمی‌تواند از تو چیزی بردارد.")
    else:
        lines.append("وضعیت: <b>غیرفعال</b> ❌ — جیبت برای دزدها باز است!")
    lines.append("")
    lines.append(f"💵 موجودی نقد: <b>{cash:,}</b> سکه")
    hours_cover = settings.shield_duration_seconds // 3600
    lines.append(f"💰 هزینه پوشش {hours_cover} ساعته: <b>{cost:,}</b> سکه")
    lines.append("")
    lines.append("برای فعال‌سازی: <code>سپر خرید</code>")
    return {
        "success": True,
        "active": active,
        "cost": cost,
        "message": "\n".join(lines),
    }


async def buy_shield(player: Player) -> dict[str, Any]:
    """Buy (or renew) the anti-theft cover for the next 24 hours."""
    current = int(time.time())
    cash, _ = await economy.balances(player.user_id)
    cost = shield_cost(cash)

    async with db.write() as conn:
        await economy.mutate(
            conn,
            player.user_id,
            credits=-cost,
            kind=ActivityKind.SHIELD,
            ref="shield:buy",
        )
        base = max(player.shield_until, current)
        until = base + settings.shield_duration_seconds
        await conn.execute(
            "UPDATE players SET shield_until = ? WHERE user_id = ?",
            (until, player.user_id),
        )

    hours = settings.shield_duration_seconds // 3600
    return {
        "success": True,
        "cost": cost,
        "until": until,
        "message": (
            f"🛡️ <b>سپر ضدسرقت فعال شد!</b>\n\n"
            f"💵 هزینه: <b>{cost:,}</b> سکه از موجودی نقد کم شد\n"
            f"⏱ پوشش برای {hours} ساعت آینده فعال است\n"
            f"🥷 هر دزدی که سراغت بیاید دست‌خالی برمی‌گردد — "
            f"بدون غنیمت، بدون زندان، بدون جریمه."
        ),
    }


async def bank_heist(
    player: Player, amount: int, roll: float | None = None
) -> dict[str, Any]:
    """Rob the central bank: ``1.6x`` win, ``5x`` fine or jail on failure."""
    current = int(time.time())

    if player.is_in_jail(current):
        remaining = int((player.is_jailed_until - current) // 60)
        return {
            "success": False,
            "error": "jail",
            "message": (
                f"🚨 پشت میله‌های بازداشتگاه نمی‌توانی دستبرد بزنی! "
                f"تا {remaining} دقیقه دیگر آزاد می‌شوی."
            ),
        }

    if player.level < settings.bank_heist_min_level:
        return {
            "success": False,
            "error": "level",
            "message": (
                f"⛔ برای دستبرد باید حداقل لول "
                f"<b>{settings.bank_heist_min_level}</b> باشی "
                f"(لول فعلی: {player.level}). اول توی خیابان بزرگ شو!"
            ),
        }

    if not settings.bank_heist_stake_min <= amount <= settings.bank_heist_stake_max:
        raise GameError(
            f"مبلغ دستبرد باید بین {settings.bank_heist_stake_min:,} تا "
            f"{settings.bank_heist_stake_max:,} سکه باشد.\n"
            f"نحوه استفاده: <code>دستبرد [مبلغ]</code>"
        )

    chance = bank_heist_success_chance(player.drip, amount)
    if await _owns_tool(player.user_id):
        chance = min(
            settings.bank_heist_max_success,
            chance + settings.bank_heist_tool_bonus,
        )
    outcome = roll if roll is not None else secrets.randbelow(10_000) / 10_000.0
    success = outcome < chance

    async with db.write() as conn:
        await spend_energy_conn(conn, player.user_id, settings.bank_heist_energy_cost)
        # Escrow first: the stake is gone before the roll, win or lose.
        await economy.mutate(
            conn,
            player.user_id,
            credits=-amount,
            kind=ActivityKind.BANK_ROBBERY,
            ref="bankrob:stake",
        )

        if success:
            payout = int(amount * settings.bank_heist_payout_multiplier)
            await economy.mutate(
                conn,
                player.user_id,
                credits=payout,
                kind=ActivityKind.BANK_ROBBERY,
                ref="bankrob:win",
            )
            await grant_exp_conn(conn, player.user_id, settings.bank_heist_exp)
            return {
                "success": True,
                "amount": amount,
                "payout": payout,
                "chance": chance,
                "message": (
                    f"💰 <b>دستبرد بانک مرکزی موفقیت‌آمیز بود!</b>\n\n"
                    f"💵 مبلغ سرقتی: <b>{amount:,}</b> سکه\n"
                    f"💸 بازگشتی با ضریب "
                    f"{settings.bank_heist_payout_multiplier:g}×: "
                    f"<b>+{payout:,}</b> سکه\n"
                    f"✨ خالص سود: <b>+{payout - amount:,}</b> سکه "
                    f"و +{settings.bank_heist_exp} EXP"
                ),
            }

        fine = int(amount * settings.bank_heist_fine_multiplier)
        cursor = await conn.execute(
            "SELECT credits FROM wallets WHERE user_id = ?", (player.user_id,)
        )
        wallet = await cursor.fetchone()
        await cursor.close()
        cash = int(wallet["credits"]) if wallet else 0

        if cash >= fine:
            await economy.mutate(
                conn,
                player.user_id,
                credits=-fine,
                kind=ActivityKind.BANK_ROBBERY,
                ref="bankrob:fine",
            )
            return {
                "success": False,
                "error": "fine",
                "fine": fine,
                "message": (
                    f"🚨 <b>زنگ خطر بانک خوابید رویت!</b>\n\n"
                    f"⚖️ حکم دادگاه: جریمه "
                    f"{settings.bank_heist_fine_multiplier:g} برابر مبلغ "
                    f"= <b>{fine:,}</b> سکه.\n"
                    f"مبلغ سرقتی {amount:,} سکه هم سوخت."
                ),
            }

        jail_until = current + settings.bank_heist_jail_seconds
        await conn.execute(
            "UPDATE players SET is_jailed_until = ? WHERE user_id = ?",
            (jail_until, player.user_id),
        )
        jail_minutes = settings.bank_heist_jail_seconds // 60
        return {
            "success": False,
            "error": "jail",
            "jail_seconds": settings.bank_heist_jail_seconds,
            "message": (
                f"🔒 <b>دستگیر شدی و رفتی بازداشتگاه!</b>\n\n"
                f"جریمه {fine:,} سکه‌ای را نداشتی، پس حکم زندان صادر شد: "
                f"{jail_minutes} دقیقه قفل تمام دستورات.\n"
                f"مبلغ سرقتی {amount:,} سکه هم از کف رفت."
            ),
        }
