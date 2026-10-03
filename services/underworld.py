"""سایه و اخاذی — hired shadows and street extortion.

All three attacks share one guard ladder: self-targeting and an already
jailed killer-target are validation errors (nothing charged), while an
active سپر or a ``نگهبان`` item in the victim's inventory cancels the job
AFTER the hiring fee is burned — the guard pays for itself.
"""

from __future__ import annotations

import logging
import random
from typing import Any

import aiosqlite

from config import settings
from database.connection import db
from models import ActivityKind
from models.player import Player
from services import economy
from services.game import GameError, now

logger = logging.getLogger(__name__)


async def _owns_item(
    conn: aiosqlite.Connection, user_id: int, item_id: str
) -> bool:
    cursor = await conn.execute(
        "SELECT 1 FROM inventory WHERE user_id = ? AND item_id = ?",
        (user_id, item_id),
    )
    found = await cursor.fetchone() is not None
    await cursor.close()
    return found


async def _guard_block(conn: aiosqlite.Connection, victim: Player) -> str | None:
    """What cancels a job against this victim: 'guard', 'shield' or None."""
    if await _owns_item(conn, victim.user_id, settings.guard_item_id):
        return "guard"
    if victim.shield_until > now():
        return "shield"
    return None


def _blocked_message(victim: Player, reason: str, fee: int) -> str:
    if reason == "guard":
        return (
            f"🛡 <b>نگهبان شخصی {victim.display_name} جلوت رو گرفت!</b>\n"
            f"سایه بی‌دست‌مزد برگشت و <b>{fee:,}</b> سکه سوخت."
        )
    return (
        f"🛡 <b>سپر ضدسرقت {victim.display_name} کار کرد!</b>\n"
        f"نقشه لو رفت و <b>{fee:,}</b> سکه‌ای که برای سایه دادی سوخت."
    )


def _extort_blocked_message(victim: Player, reason: str) -> str:
    if reason == "guard":
        return (
            f"🛡 <b>نگهبان شخصی {victim.display_name} جلوت رو گرفت!</b>\n"
            "داد و بیداد بی‌فایده بود؛ دست خالی برگشتی."
        )
    return (
        f"🛡 <b>سپر ضدسرقت {victim.display_name} کار کرد!</b>\n"
        "سرت کلاه رفت و هیچی هم نصیبت نشد."
    )


def _steal_amount(victim_cash: int, pct: float, low: int, high: int) -> int:
    raw = int(victim_cash * pct)
    return min(max(low, raw), high, victim_cash)


async def shadow_hack(
    attacker: Player, victim: Player, *, roll: float | None = None
) -> dict[str, Any]:
    """سایه هکر — burn the fee, then maybe drain a capped slice of the wallet."""
    if victim.user_id == attacker.user_id:
        raise GameError("خودت رو که سایه نمی‌گیری؛ ریپلای یکی دیگه رو بکن.")
    economy.require_ready(attacker.user_id, "shadow", settings.cooldown_shadow)

    fee = settings.shadow_hacker_fee
    async with db.write() as conn:
        await economy.mutate(
            conn,
            attacker.user_id,
            credits=-fee,
            kind=ActivityKind.SHADOW,
            ref="shadow:hacker:fee",
        )

        block = await _guard_block(conn, victim)
        if block is not None:
            return {
                "success": False,
                "blocked": block,
                "message": _blocked_message(victim, block, fee),
            }

        cursor = await conn.execute(
            "SELECT credits FROM wallets WHERE user_id = ?", (victim.user_id,)
        )
        wallet = await cursor.fetchone()
        await cursor.close()
        victim_cash = int(wallet["credits"]) if wallet else 0

        outcome = random.random() if roll is None else roll
        if outcome < settings.shadow_hacker_success and victim_cash > 0:
            steal = _steal_amount(
                victim_cash,
                settings.shadow_hacker_steal_pct,
                settings.shadow_hacker_steal_min,
                settings.shadow_hacker_steal_max,
            )
            await economy.mutate(
                conn,
                victim.user_id,
                credits=-steal,
                kind=ActivityKind.SHADOW,
                ref="shadow:hacker:loss",
            )
            await economy.mutate(
                conn,
                attacker.user_id,
                credits=steal,
                kind=ActivityKind.SHADOW,
                ref="shadow:hacker:win",
            )
            return {
                "success": True,
                "stolen": steal,
                "message": (
                    f"💻 <b>سایه‌ها خوابیدن روی حساب {victim.display_name}!</b>\n"
                    f"<b>{steal:,}</b> سکه نقل‌مکان کرد جیب تو. "
                    f"کارمزد سایه از قبل سوخته ({fee:,} سکه)."
                ),
            }

        cursor = await conn.execute(
            "SELECT credits FROM wallets WHERE user_id = ?", (attacker.user_id,)
        )
        wallet = await cursor.fetchone()
        await cursor.close()
        fine = min(settings.shadow_hacker_fail_fine, int(wallet["credits"])) if wallet else 0
        if fine > 0:
            await economy.mutate(
                conn,
                attacker.user_id,
                credits=-fine,
                kind=ActivityKind.SHADOW,
                ref="shadow:hacker:fail",
            )
        return {
            "success": False,
            "fine": fine,
            "message": (
                f"❌ <b>هک لو رفت!</b> {victim.display_name} زودتر از سایه‌ها بیدار شد.\n"
                f"کارمزد {fee:,} سکه سوخت"
                + (f" و {fine:,} سکه جریمه هم دادی." if fine else ".")
            ),
        }


async def shadow_kill(
    attacker: Player, victim: Player, *, roll: float | None = None
) -> dict[str, Any]:
    """سایه قاتل — burn the fee, maybe put the target behind bars."""
    if victim.user_id == attacker.user_id:
        raise GameError("خودت رو که قاتل نمی‌گیری؛ ریپلای یکی دیگه رو بکن.")
    if victim.is_in_jail(now()):
        raise GameError("هدف الان پشت میله‌ست؛ قاتل برای آدم پشت میله کار نمی‌کنه.")
    economy.require_ready(attacker.user_id, "shadow", settings.cooldown_shadow)

    fee = settings.shadow_killer_fee
    async with db.write() as conn:
        await economy.mutate(
            conn,
            attacker.user_id,
            credits=-fee,
            kind=ActivityKind.SHADOW,
            ref="shadow:killer:fee",
        )

        block = await _guard_block(conn, victim)
        if block is not None:
            return {
                "success": False,
                "blocked": block,
                "message": _blocked_message(victim, block, fee),
            }

        outcome = random.random() if roll is None else roll
        if outcome < settings.shadow_killer_success:
            jailed_until = now() + settings.shadow_kill_jail_seconds
            await conn.execute(
                "UPDATE players SET is_jailed_until = ? WHERE user_id = ?",
                (jailed_until, victim.user_id),
            )
            minutes = settings.shadow_kill_jail_seconds // 60
            return {
                "success": True,
                "jailed_seconds": settings.shadow_kill_jail_seconds,
                "message": (
                    f"🔪 <b>سایه قاتل {victim.display_name} رو گرفت!</b>\n"
                    f"دادگاه فوری حکم داد: <b>{minutes} دقیقه</b> بازداشت. "
                    f"کارمزد قاتل ({fee:,} سکه) سوخت."
                ),
            }

        return {
            "success": False,
            "message": (
                f"❌ <b>عملیات قاتل شکست خورد!</b> "
                f"{victim.display_name} از تور در رفت و کارمزد {fee:,} سکه سوخت."
            ),
        }


def extort_chance(attacker: Player, victim: Player) -> float:
    raw = settings.extort_success_base + settings.extort_level_bonus * (
        attacker.level - victim.level
    )
    return max(settings.extort_success_min, min(raw, settings.extort_success_max))


async def extort(
    attacker: Player, victim: Player, *, roll: float | None = None
) -> dict[str, Any]:
    """اخاذی — no hiring fee; lose and you compensate + cool off in jail."""
    if victim.user_id == attacker.user_id:
        raise GameError("خودت رو که نمی‌تونی اخاذی کنی؛ ریپلای یکی دیگه رو بکن.")
    economy.require_ready(attacker.user_id, "extort", settings.cooldown_extort)

    async with db.write() as conn:
        block = await _guard_block(conn, victim)
        if block is not None:
            return {
                "success": False,
                "blocked": block,
                "message": _extort_blocked_message(victim, block),
            }

        cursor = await conn.execute(
            "SELECT credits FROM wallets WHERE user_id = ?", (victim.user_id,)
        )
        wallet = await cursor.fetchone()
        await cursor.close()
        victim_cash = int(wallet["credits"]) if wallet else 0

        chance = extort_chance(attacker, victim)
        outcome = random.random() if roll is None else roll
        if outcome < chance and victim_cash > 0:
            steal = _steal_amount(
                victim_cash,
                settings.extort_steal_pct,
                settings.extort_steal_min,
                settings.extort_steal_max,
            )
            await economy.mutate(
                conn,
                victim.user_id,
                credits=-steal,
                kind=ActivityKind.EXTORT,
                ref="extort:take",
            )
            await economy.mutate(
                conn,
                attacker.user_id,
                credits=steal,
                kind=ActivityKind.EXTORT,
                ref="extort:win",
            )
            return {
                "success": True,
                "stolen": steal,
                "message": (
                    f"😠 <b>داد و بیداد جواب داد!</b> {victim.display_name} "
                    f"ترسید و <b>{steal:,}</b> سکه انداخت جیبت."
                ),
            }

        cursor = await conn.execute(
            "SELECT credits FROM wallets WHERE user_id = ?", (attacker.user_id,)
        )
        wallet = await cursor.fetchone()
        await cursor.close()
        comp = 0
        if wallet:
            comp = min(settings.extort_fail_compensation, int(wallet["credits"]))
        if comp > 0:
            await economy.mutate(
                conn,
                attacker.user_id,
                credits=-comp,
                kind=ActivityKind.EXTORT,
                ref="extort:compensation",
            )
            await economy.mutate(
                conn,
                victim.user_id,
                credits=comp,
                kind=ActivityKind.EXTORT,
                ref="extort:paid",
            )
        jailed_until = now() + settings.extort_fail_jail_seconds
        await conn.execute(
            "UPDATE players SET is_jailed_until = ? WHERE user_id = ?",
            (jailed_until, attacker.user_id),
        )

    minutes = settings.extort_fail_jail_seconds // 60
    return {
        "success": False,
        "compensation": comp,
        "message": (
            f"🚨 <b>اخاذی برگشت خورد!</b> {victim.display_name} کوت نیومد و "
            f"پلیس رسید.\n"
            f"غرامت <b>{comp:,}</b> سکه از جیبت رفت جیب طرف، "
            f"و <b>{minutes} دقیقه</b> هم پشت میله‌ای."
        ),
    }
