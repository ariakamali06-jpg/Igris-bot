"""ریسک — the crash round: bet, watch the wire climb, cash out before it snaps.

A round escrows its stake into ``risk_rounds`` at ``started_at`` with a
*hidden* ``crash_point`` drawn from the configured exponential distribution
(``risk_crash_floor`` / ``risk_crash_scale`` / ``risk_house_edge``).  The
visible multiplier is a pure function of elapsed time
(``1 + risk_growth_rate * seconds``), so tests control the clock by moving
``started_at`` (or the row itself) instead of sleeping.

Resolution rules, checked in this order:
1. the multiplier reached ``crash_point`` → the wire snapped (loss);
2. ``risk_round_seconds`` elapsed → the round expired (loss, per spec);
3. otherwise a cash-out pays ``stake * multiplier``.

Every movement goes through :func:`economy.mutate` inside one transaction.
"""

from __future__ import annotations

import math
import random
from typing import Any

from config import settings
from database.connection import db
from models.enums import ActivityKind
from services import economy
from services.casino_games import check_bet
from services.game import GameError, now

# Cooldown action name (``economy.require_ready`` key).
ACTION_RISK = "risk"

_STATUS_FA = {"cashed": "برداشت شد", "crashed": "منفجر شد", "expired": "منقضی شد"}


def current_multiplier(elapsed_seconds: float) -> float:
    """Deterministic wire value at ``elapsed_seconds`` after the start."""
    return round(1.0 + settings.risk_growth_rate * max(0.0, elapsed_seconds), 2)


def draw_crash_point(u: float | None = None) -> float:
    """Exponential crash point with the house edge folded into its scale."""
    sample = random.random() if u is None else min(max(u, 1e-12), 1.0)
    raw = -(1.0 - settings.risk_house_edge) * settings.risk_crash_scale * math.log(sample)
    return round(max(settings.risk_crash_floor, raw), 2)


async def _active_round(user_id: int) -> dict[str, Any] | None:
    row = await db.fetchone(
        "SELECT * FROM risk_rounds WHERE user_id = ? AND status = 'active' "
        "ORDER BY id DESC LIMIT 1",
        (user_id,),
    )
    return dict(row) if row else None


async def start(user_id: int, stake: int, *, chat_id: int = 0,
                crash_point: float | None = None) -> dict[str, Any]:
    """Escrow the stake and open a round with a fresh hidden crash point."""
    check_bet(stake)
    if await _active_round(user_id) is not None:
        raise GameError(
            "یه ریسکِ باز داری — اول توش رو بکش بیرون یا بذار منفجر بشه.\n"
            "وضعیت: <code>ریسک</code> · برداشت: <code>ریسک برداشت</code>"
        )
    point = crash_point if crash_point is not None else draw_crash_point()

    async with db.write() as conn:
        await economy.mutate(
            conn, user_id, credits=-stake, kind=ActivityKind.RISK, ref="risk:bet"
        )
        await conn.execute(
            """
            INSERT INTO risk_rounds (user_id, chat_id, stake, crash_point,
                                     status, started_at)
            VALUES (?, ?, ?, ?, 'active', ?)
            """,
            (user_id, chat_id, stake, point, now()),
        )
    return {
        "success": True,
        "message": (
            f"⚡ ریسک روی میز — {stake:,} سکه روی سیمِ برقِ کوچه.\n"
            f"ضریب از <b>1.00</b> شروع شد و هر ثیقه بالا می‌ره؛ هر وقت بخوای "
            f"<code>ریسک برداشت</code> بزنی، همون‌جا قیمت رو می‌بندی.\n"
            f"سیم تا <b>{settings.risk_round_seconds}</b> ثیقه دووم میاره؛ دیر‌تر بِکِشی، "
            "همه‌چی می‌سوزه. ⏳"
        ),
        "stake": stake,
        "crash_point": point,
    }


async def status(user_id: int) -> dict[str, Any]:
    """Live multiplier for the open round (and lazy loss resolution)."""
    rnd = await _active_round(user_id)
    if rnd is None:
        return {
            "success": False,
            "message": (
                "🕸 ریسکی در جریان نیست. سیم رو بکش: <code>ریسک [مبلغ]</code>\n"
                "قبلش چند ثیقه حساب کن؛ سیم همون‌قدر که صبر کنی بالا می‌ره، "
                "و همون‌قدر هم می‌تونه بترکه."
            ),
        }
    elapsed = now() - int(rnd["started_at"])
    multiplier = current_multiplier(elapsed)
    crash = float(rnd["crash_point"])
    if multiplier >= crash:
        await _resolve(rnd, "crashed")
        return {
            "success": False,
            "message": (
                f"💥 سیم تا <b>{crash:.2f}</b> بالا رفت و ترکید!\n"
                f"{int(rnd['stake']):,} سکه روی میز سوخت. دفعه بعد زود‌تر بکش بیرون."
            ),
            "multiplier": crash,
        }
    if elapsed >= settings.risk_round_seconds:
        await _resolve(rnd, "expired")
        return {
            "success": False,
            "message": (
                f"⏳ ریسک {settings.risk_round_seconds} ثیقه طول کشید و بی‌صدا خاموش شد؛ "
                f"{int(rnd['stake']):,} سکه سوخت."
            ),
            "multiplier": multiplier,
        }
    left = settings.risk_round_seconds - elapsed
    return {
        "success": True,
        "message": (
            f"⚡ ریسک باز — ضریب فعلی: <b>{multiplier:.2f}x</b>\n"
            f"شرط: {int(rnd['stake']):,} سکه · تا انفجار فاصله داری، تا خاموشی "
            f"{left} ثیقه مونده.\n"
            "برداشت: <code>ریسک برداشت</code>"
        ),
        "multiplier": multiplier,
    }


async def cashout(user_id: int) -> dict[str, Any]:
    """Lock the current multiplier — or eat the loss if the wire already snapped."""
    rnd = await _active_round(user_id)
    if rnd is None:
        raise GameError("سیمی برای کشیدن نیست. اول شروع کن: <code>ریسک [مبلغ]</code>")
    elapsed = now() - int(rnd["started_at"])
    multiplier = current_multiplier(elapsed)
    crash = float(rnd["crash_point"])

    if multiplier >= crash:
        await _resolve(rnd, "crashed")
        return {
            "success": False,
            "message": (
                f"💥 دیر زدی! سیم روی <b>{crash:.2f}</b> ترکید و {int(rnd['stake']):,} "
                "سکه خاکستر شد."
            ),
            "multiplier": crash,
            "payout": 0,
        }
    if elapsed >= settings.risk_round_seconds:
        await _resolve(rnd, "expired")
        return {
            "success": False,
            "message": (
                f"⏳ وقت ریسک تموم شد؛ {int(rnd['stake']):,} سکه روی میز موند و سوخت."
            ),
            "multiplier": multiplier,
            "payout": 0,
        }

    payout = int(int(rnd["stake"]) * multiplier)
    async with db.write() as conn:
        await economy.mutate(
            conn, user_id, credits=payout, kind=ActivityKind.RISK, ref="risk:cashout"
        )
        await conn.execute(
            "UPDATE risk_rounds SET status = 'cashed', payout = ?, resolved_at = ? "
            "WHERE id = ?",
            (payout, now(), rnd["id"]),
        )
    return {
        "success": True,
        "message": (
            f"🙌 دست کشیدی روی <b>{multiplier:.2f}x</b> — {int(rnd['stake']):,} سکه "
            f"تبدیل شد به <b>{payout:,}</b> سکه! (سود: +{payout - int(rnd['stake']):,})"
        ),
        "multiplier": multiplier,
        "payout": payout,
        "delta": payout - int(rnd["stake"]),
    }


async def _resolve(rnd: dict[str, Any], status: str) -> None:
    """Book a lost round: the escrow stays with the house, the row closes."""
    if status not in ("crashed", "expired"):
        raise GameError("وضعیت نامعتبر برای خاتمه ریسک")
    async with db.write() as conn:
        cursor = await conn.execute(
            "UPDATE risk_rounds SET status = ?, resolved_at = ? WHERE id = ? AND status = 'active'",
            (status, now(), rnd["id"]),
        )
        await cursor.close()
    rnd["status"] = status


async def recent(user_id: int) -> dict[str, Any]:
    """Last resolved round — fuels the 'واژه‌های از دست رفته' feel."""
    row = await db.fetchone(
        "SELECT * FROM risk_rounds WHERE user_id = ? AND status != 'active' "
        "ORDER BY id DESC LIMIT 1",
        (user_id,),
    )
    if row is None:
        return {"success": False, "message": "هنوز ریسکی تموم نشده."}
    return {
        "success": True,
        "status": _STATUS_FA.get(str(row["status"]), str(row["status"])),
        "payout": int(row["payout"]),
        "stake": int(row["stake"]),
    }
