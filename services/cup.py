"""جام — the group football cup: open rounds, escrowed entries, goal rolls.

One round per chat. Joining escrows the entry fee (ledger ``cup:entry``);
when the window closes, every entrant rolls a goal score and the top score
splits the pot minus the house rake. Under-attended rounds cancel and
refund everyone, so no escrow can strand.
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


def goal_score(level: int, roll: float) -> float:
    """Goal roll + per-level edge from config (higher wins the cup)."""
    return roll + level * settings.cup_level_bonus


async def _open_round(conn, chat_id: int) -> dict[str, Any] | None:
    cursor = await conn.execute(
        "SELECT * FROM cup_rounds WHERE chat_id = ? AND status = 'open' "
        "ORDER BY id DESC LIMIT 1",
        (chat_id,),
    )
    row = await cursor.fetchone()
    await cursor.close()
    return dict(row) if row else None


async def _entries(conn, round_id: int) -> list[dict[str, Any]]:
    cursor = await conn.execute(
        "SELECT e.user_id, e.created_at, p.display_name, p.level "
        "FROM cup_entries e JOIN players p ON p.user_id = e.user_id "
        "WHERE e.round_id = ? ORDER BY e.id",
        (round_id,),
    )
    rows = [dict(row) for row in await cursor.fetchall()]
    await cursor.close()
    return rows


async def _settle_conn(conn, round: dict[str, Any]) -> dict[str, Any]:
    """Close a finished round: refund the small crowd or pay the winner."""
    round_id = int(round["id"])
    fee = int(round["entry_fee"])
    entries = await _entries(conn, round_id)

    if len(entries) < settings.cup_min_players:
        for entry in entries:
            await economy.mutate(
                conn,
                int(entry["user_id"]),
                credits=fee,
                kind=ActivityKind.CUP,
                ref="cup:refund",
            )
        await conn.execute(
            "UPDATE cup_rounds SET status = 'cancelled', settled_at = ? "
            "WHERE id = ?",
            (now(), round_id),
        )
        return {
            "success": True,
            "settled": True,
            "cancelled": True,
            "round_id": round_id,
            "message": (
                f"🏟 دور جام بی‌مخاطب موند ({len(entries)}/"
                f"{settings.cup_min_players} نفر) — لغو شد و ورودیه "
                f"هر کسی برگشت."
            ),
        }

    scores: dict[int, float] = {}
    for entry in entries:
        scores[int(entry["user_id"])] = goal_score(
            int(entry["level"]), random.random()
        )
    best = max(scores.values())
    winners = [uid for uid, score in scores.items() if score == best]

    pot = fee * len(entries)
    rake = int(pot * settings.cup_rake)
    pool = pot - rake
    share = pool // len(winners)
    remainder = pool - share * len(winners)
    for index, winner_id in enumerate(winners):
        payout = share + (remainder if index == 0 else 0)
        await economy.mutate(
            conn,
            winner_id,
            credits=payout,
            kind=ActivityKind.CUP,
            ref="cup:win",
        )
    await conn.execute(
        "UPDATE cup_rounds SET status = 'settled', settled_at = ? WHERE id = ?",
        (now(), round_id),
    )

    names = {int(e["user_id"]): str(e["display_name"]) for e in entries}
    table = sorted(
        (
            f"⚽ {names[uid]} — <b>{score:.2f}</b> گل"
            + (" 🏆" if uid in winners else "")
            for uid, score in scores.items()
        ),
        key=lambda line: line,
        reverse=True,
    )
    winner_names = "، ".join(names[uid] for uid in winners)
    return {
        "success": True,
        "settled": True,
        "cancelled": False,
        "round_id": round_id,
        "winner_ids": winners,
        "message": (
            f"🏆 <b>جام تموم شد — {winner_names} قهرمان شد!</b>\n"
            + "\n".join(table)
            + f"\nجایزه هر برنده: <b>{share + remainder if winners else 0:,}</b>"
            f" سکه از پات {pot:,} (سهم خانه {rake:,} سوخت)."
        ),
    }


async def status(chat_id: int, viewer: Player | None = None) -> dict[str, Any]:
    """Cup sheet for the chat; lazily settles a finished open round."""
    async with db.write() as conn:
        rnd = await _open_round(conn, chat_id)
        if rnd is None:
            return {
                "success": True,
                "open": False,
                "message": (
                    "🏟 <b>جامی برپا نیست.</b>\n"
                    f"با <code>جام ثبت</code> دور جدید رو با ورودیه "
                    f"{settings.cup_entry_fee:,} سکه شروع کن "
                    f"(حداقل {settings.cup_min_players} نفر برای برگزاری)."
                ),
            }
        if int(rnd["closes_at"]) <= now():
            return await _settle_conn(conn, rnd)

        entries = await _entries(conn, int(rnd["id"]))
        joined = viewer is not None and any(
            int(e["user_id"]) == viewer.user_id for e in entries
        )
        left = max(0, int(rnd["closes_at"]) - now())
        roster = "\n".join(
            f"⚽ {e['display_name']}" for e in entries
        ) or "هنوز کسی ثبت‌نام نکرده."
        return {
            "success": True,
            "open": True,
            "round_id": int(rnd["id"]),
            "entries": len(entries),
            "message": (
                "🏟 <b>جام فوتبال خیابان — دور باز</b>\n"
                f"ورودیه: <b>{int(rnd['entry_fee']):,}</b> سکه · "
                f"شرکت‌کنندگان: <b>{len(entries)}</b> · "
                f"بسته‌شدن تا {left // 60} دقیقه دیگه\n"
                f"شرکت‌کنندگان:\n{roster}\n"
                + ("✅ تو هم ثبت‌نامی." if joined else "👉 با <code>جام ثبت</code> بپا.")
            ),
        }


async def join(player: Player, chat_id: int) -> dict[str, Any]:
    """Enter the chat's cup, opening a round if none is running."""
    async with db.write() as conn:
        rnd = await _open_round(conn, chat_id)
        note = ""
        if rnd is not None and int(rnd["closes_at"]) <= now():
            settled = await _settle_conn(conn, rnd)
            note = "\n" + settled["message"]
            rnd = None

        if rnd is None:
            cursor = await conn.execute(
                "INSERT INTO cup_rounds (chat_id, status, entry_fee, "
                "created_at, closes_at) VALUES (?, 'open', ?, ?, ?)",
                (
                    chat_id,
                    settings.cup_entry_fee,
                    now(),
                    now() + settings.cup_round_seconds,
                ),
            )
            round_id = int(cursor.lastrowid or 0)
            await cursor.close()
        else:
            round_id = int(rnd["id"])

        cursor = await conn.execute(
            "SELECT 1 FROM cup_entries WHERE round_id = ? AND user_id = ?",
            (round_id, player.user_id),
        )
        already = await cursor.fetchone() is not None
        await cursor.close()
        if already:
            raise GameError("تو که توی دور جاری ثبت‌نام کردی!")

        await economy.mutate(
            conn,
            player.user_id,
            credits=-settings.cup_entry_fee,
            kind=ActivityKind.CUP,
            ref="cup:entry",
        )
        await conn.execute(
            "INSERT INTO cup_entries (round_id, user_id, created_at) "
            "VALUES (?, ?, ?)",
            (round_id, player.user_id, now()),
        )

    return {
        "success": True,
        "round_id": round_id,
        "message": (
            f"⚽ ثبت‌نام شدی توی جام #{round_id} — "
            f"ورودیه <b>{settings.cup_entry_fee:,}</b> سکه از کیفت رفت صندوق.\n"
            f"تا {settings.cup_round_seconds // 60} دقیقه دیگه دروازه‌ها بازه؛ "
            f"بیشترین گل، کل پات رو می‌بره."
            + note
        ),
    }
