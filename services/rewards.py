"""Rewards: lottery (قرعه) and gift codes (هدیه).

The lottery is a single-row pot plus per-round ticket counts.  The draw is
*lazy*: every قرعه command first checks whether ``next_draw_at`` has passed,
picks a winner weighted by tickets (``secrets`` so nobody can predict the
slot), pays through :func:`services.economy.mutate` and rolls the state into
the next round.  A round with no tickets simply rolls the pot over.

Gift codes are admin-authored strings with a use budget and an expiry; the
``gift_redemptions`` table makes "each code once per player" a database
invariant rather than a check that a race can slip past.
"""

from __future__ import annotations

import re
import secrets
import time
from typing import Any

from config import settings
from database.connection import db
from models.enums import ActivityKind
from models.player import Player
from services import economy
from services.game import GameError

# Cooldown action names (``economy.require_ready`` keys).
ACTION_LOTTERY = "lottery"
ACTION_GIFT = "gift"

_CODE_RE = re.compile(r"^[\w\-]+$", re.UNICODE)


def _normalise_code(code: str) -> str:
    return code.strip().casefold()


# ---------------------------------------------------------------------------
# Lottery (قرعه)
# ---------------------------------------------------------------------------


async def _ensure_state() -> dict[str, Any]:
    """Materialise the singleton lottery row and return it."""
    current = int(time.time())
    async with db.write() as conn:
        await conn.execute(
            "INSERT OR IGNORE INTO lottery_state (id, pot, next_draw_at, round) "
            "VALUES (1, ?, ?, 0)",
            (settings.lottery_initial_pot, current + settings.lottery_interval_seconds),
        )
        cursor = await conn.execute(
            "SELECT pot, next_draw_at, round, last_winner_id, last_draw_at "
            "FROM lottery_state WHERE id = 1"
        )
        row = await cursor.fetchone()
        await cursor.close()
    if row is None:
        return {
            "pot": settings.lottery_initial_pot,
            "next_draw_at": current + settings.lottery_interval_seconds,
            "round": 0,
            "last_winner_id": None,
            "last_draw_at": 0,
        }
    return dict(row)


async def maybe_draw() -> dict[str, Any] | None:
    """Run the draw when the window elapsed; ``None`` when still open."""
    current = int(time.time())
    state = await _ensure_state()
    if current < int(state["next_draw_at"]):
        return None

    round_no = int(state["round"])
    pot = int(state["pot"])
    tickets = await db.fetchall(
        "SELECT user_id, tickets FROM lottery_tickets "
        "WHERE round = ? AND tickets > 0",
        (round_no,),
    )

    winner_id: int | None = None
    if tickets:
        total = sum(int(row["tickets"]) for row in tickets)
        pick = secrets.randbelow(total)
        for row in tickets:
            pick -= int(row["tickets"])
            if pick < 0:
                winner_id = int(row["user_id"])
                break

    next_draw = current + settings.lottery_interval_seconds
    async with db.write() as conn:
        if winner_id is not None:
            await economy.mutate(
                conn,
                winner_id,
                credits=pot,
                kind=ActivityKind.LOTTERY,
                ref=f"lottery:{round_no}",
            )
        await conn.execute(
            "UPDATE lottery_state SET pot = ?, next_draw_at = ?, round = round + 1, "
            "last_winner_id = ?, last_draw_at = ? WHERE id = 1",
            (
                pot if winner_id is None else settings.lottery_initial_pot,
                next_draw,
                winner_id,
                current,
            ),
        )
        await conn.execute("DELETE FROM lottery_tickets WHERE round = ?", (round_no,))

    return {
        "round": round_no,
        "pot": pot,
        "winner_id": winner_id,
        "next_draw_at": next_draw,
    }


async def _tickets_for(user_id: int, round_no: int) -> int:
    row = await db.fetchone(
        "SELECT tickets FROM lottery_tickets WHERE user_id = ? AND round = ?",
        (user_id, round_no),
    )
    return int(row["tickets"]) if row else 0


async def status(player: Player) -> dict[str, Any]:
    """Prize, ticket price, next draw time and the player's ticket count."""
    draw = await maybe_draw()
    state = await _ensure_state()
    tickets = await _tickets_for(player.user_id, int(state["round"]))

    remaining = max(0, int(state["next_draw_at"]) - int(time.time()))
    hours, remainder = divmod(remaining, 3600)
    minutes = remainder // 60

    lines = [
        "🎟️ <b>قرعه‌کشی بزرگ تیرامیکس</b>",
        "",
        f"🎁 جایزه این دوره: <b>{int(state['pot']):,}</b> سکه",
        f"💰 قیمت هر بلیت: <b>{settings.lottery_ticket_price:,}</b> سکه",
        f"⏳ قرعه بعدی: {hours} ساعت و {minutes:02d} دقیقه دیگر",
        f"🎫 بلیت‌های تو در این دوره: <b>{tickets}</b>",
    ]
    if draw is not None:
        lines.append("")
        if draw["winner_id"] is not None:
            marker = (
                "خودت بودی 🎉"
                if draw["winner_id"] == player.user_id
                else "یکی دیگه!"
            )
            lines.append(f"🔔 دوره قبل بسته شد: {draw['pot']:,} سکه → {marker}")
        else:
            lines.append(
                f"🔔 دوره قبل بدون بلیت بسته شد؛ {draw['pot']:,} سکه به دوره بعد رفت."
            )
    lines.append("")
    lines.append("برای خرید بلیت: <code>قرعه خرید</code>")
    return {
        "success": True,
        "state": state,
        "tickets": tickets,
        "message": "\n".join(lines),
    }


async def buy_ticket(player: Player) -> dict[str, Any]:
    """One ticket: price debited, most of it feeding the pot."""
    await maybe_draw()
    state = await _ensure_state()
    price = settings.lottery_ticket_price
    pot_add = int(price * settings.lottery_pot_share)
    round_no = int(state["round"])

    async with db.write() as conn:
        await economy.mutate(
            conn,
            player.user_id,
            credits=-price,
            kind=ActivityKind.LOTTERY,
            ref="lottery:ticket",
        )
        await conn.execute(
            "INSERT INTO lottery_tickets (user_id, round, tickets) VALUES (?, ?, 1) "
            "ON CONFLICT (user_id, round) DO UPDATE SET tickets = tickets + 1",
            (player.user_id, round_no),
        )
        await conn.execute(
            "UPDATE lottery_state SET pot = pot + ? WHERE id = 1", (pot_add,)
        )

    tickets = await _tickets_for(player.user_id, round_no)
    pot = int(state["pot"]) + pot_add
    return {
        "success": True,
        "price": price,
        "tickets": tickets,
        "pot": pot,
        "message": (
            f"🎟️ <b>بلیت قرعه خریداری شد!</b>\n\n"
            f"💵 پرداخت: <b>{price:,}</b> سکه\n"
            f"🎫 بلیت‌های تو: <b>{tickets}</b>\n"
            f"🎁 جایزه فعلی: <b>{pot:,}</b> سکه\n"
            f"هر ۳ روز یک برنده از بین خریداران انتخاب می‌شود."
        ),
    }


# ---------------------------------------------------------------------------
# Gift codes (هدیه)
# ---------------------------------------------------------------------------


def validate_code(code: str) -> str:
    """Normalise and range-check a gift code, returning its stored form."""
    key = _normalise_code(code)
    if not settings.gift_code_min_len <= len(key) <= settings.gift_code_max_len:
        raise GameError(
            f"کد هدیه باید بین {settings.gift_code_min_len} تا "
            f"{settings.gift_code_max_len} کاراکتر باشد."
        )
    if not _CODE_RE.match(key):
        raise GameError(
            "کد هدیه فقط می‌تواند حروف، عدد، خط تیره و زیرخط داشته باشد."
        )
    return key


async def create_code(
    code: str, credits: int, max_uses: int, creator_id: int
) -> dict[str, Any]:
    """Author a gift code (callers enforce the admin gate)."""
    key = validate_code(code)
    if credits <= 0 or credits > settings.gift_max_credits:
        raise GameError(
            f"مبلغ هدیه باید بین ۱ تا {settings.gift_max_credits:,} سکه باشد."
        )
    if max_uses < 1 or max_uses > settings.gift_max_uses:
        raise GameError(
            f"ظرفیت کد باید بین ۱ تا {settings.gift_max_uses:,} نفر باشد."
        )

    current = int(time.time())
    expires = current + settings.gift_ttl_days * 86_400
    async with db.write() as conn:
        cursor = await conn.execute(
            "SELECT code FROM gift_codes WHERE code = ?", (key,)
        )
        existing = await cursor.fetchone()
        await cursor.close()
        if existing is not None:
            return {
                "success": False,
                "message": f"❌ کد «{code}» قبلاً ساخته شده است.",
            }
        await conn.execute(
            "INSERT INTO gift_codes (code, credits, max_uses, used, expires_at, "
            "created_by, created_at) VALUES (?, ?, ?, 0, ?, ?, ?)",
            (key, credits, max_uses, expires, creator_id, current),
        )

    return {
        "success": True,
        "code": key,
        "credits": credits,
        "max_uses": max_uses,
        "expires_at": expires,
        "message": (
            f"🎁 <b>کد هدیه ساخته شد!</b>\n\n"
            f"🔑 کد: <code>{key}</code>\n"
            f"💵 مبلغ: <b>{credits:,}</b> سکه برای هر نفر\n"
            f"👥 ظرفیت: {max_uses} نفر\n"
            f"⏳ اعتبار: {settings.gift_ttl_days} روز\n\n"
            f"بازیکنان با <code>هدیه {key}</code> آن را دریافت می‌کنند."
        ),
    }


async def redeem(player: Player, code: str) -> dict[str, Any]:
    """Claim a gift code once per player."""
    key = _normalise_code(code)
    current = int(time.time())
    if not key:
        raise GameError(
            "کد هدیه را بنویسید. مثال: <code>هدیه TIRAMIX2026</code>"
        )

    async with db.write() as conn:
        cursor = await conn.execute(
            "SELECT credits, max_uses, used, expires_at FROM gift_codes "
            "WHERE code = ?",
            (key,),
        )
        row = await cursor.fetchone()
        await cursor.close()
        if row is None:
            return {"success": False, "message": f"❌ کد «{code}» معتبر نیست."}
        if int(row["expires_at"]) and int(row["expires_at"]) < current:
            return {"success": False, "message": "⌛ این کد هدیه منقضی شده است."}
        if int(row["used"]) >= int(row["max_uses"]):
            return {
                "success": False,
                "message": "🛑 ظرفیت این کد هدیه تمام شده است.",
            }

        cursor = await conn.execute(
            "SELECT 1 AS ok FROM gift_redemptions WHERE code = ? AND user_id = ?",
            (key, player.user_id),
        )
        claimed = await cursor.fetchone()
        await cursor.close()
        if claimed is not None:
            return {
                "success": False,
                "message": (
                    "🙅 قبلاً این کد را گرفته‌ای؛ هر کد برای هر نفر فقط یک‌بار."
                ),
            }

        await conn.execute(
            "INSERT INTO gift_redemptions (code, user_id, claimed_at) VALUES (?, ?, ?)",
            (key, player.user_id, current),
        )
        await conn.execute(
            "UPDATE gift_codes SET used = used + 1 WHERE code = ?", (key,)
        )
        credits = int(row["credits"])
        await economy.mutate(
            conn,
            player.user_id,
            credits=credits,
            kind=ActivityKind.GIFT,
            ref=f"gift:{key}",
        )

    return {
        "success": True,
        "code": key,
        "credits": credits,
        "message": (
            f"🎁 <b>کد هدیه قبول شد!</b>\n\n"
            f"💵 <b>+{credits:,}</b> سکه به کیف پولت اضافه شد.\n"
            f"شهر به شهروندانش احترام می‌گذارد — خوش گذشت!"
        ),
    }
