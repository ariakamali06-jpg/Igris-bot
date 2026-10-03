"""ریسک — the crash round command: start, watch the wire, cash out in time.

``ریسک [مبلغ]`` opens a round (stake escrowed, crash point hidden),
``ریسک برداشت`` locks the live multiplier, and a bare ``ریسک`` reports the
current state — resolving a snapped/expired round lazily on read.
"""

from __future__ import annotations

import logging

from aiogram import Router
from aiogram.filters import CommandObject
from aiogram.types import Message

from config import settings
from handlers.common import CommandOrText, answer_error, extract_args, hydrate
from services import economy, risk_game
from services.game import GameError

logger = logging.getLogger(__name__)
router = Router(name="risk")

RISK_WORDS = {"ریسک"}
_CASHOUT_ACTIONS = {"برداشت", "cashout", "cash"}

_USAGE = (
    "⚡ <b>ریسک — بازی سیمِ برقِ تیرامیکس</b>\n"
    "• <code>ریسک [مبلغ]</code> — شروع دور (شرط گرو می‌ره، ضریب از ۱ صفر شروع می‌شه)\n"
    "• <code>ریسک برداشت</code> — بستن ضریب فعلی و گرفتن سود\n"
    "• <code>ریسک</code> — وضعیت دورِ باز\n"
    f"مبلغ: {settings.casino_min_bet:,} تا {settings.casino_max_bet:,} سکه · "
    f"هر ثیقه +{settings.risk_growth_rate:g} ضریب · دور "
    f"{settings.risk_round_seconds} ثیقه‌ای، دیر بکشی می‌سوزه."
)


async def _status_with_history(user_id: int) -> dict:
    res = await risk_game.status(user_id)
    if not res.get("success"):
        last = await risk_game.recent(user_id)
        if last.get("success"):
            res = {
                **res,
                "message": (
                    f"{res['message']}\n"
                    f"— آخرین دور: {last['status']} · شرط {last['stake']:,} · "
                    f"برداشت {last['payout']:,} سکه"
                ),
            }
    return res


@router.message(CommandOrText(["risk"], words=RISK_WORDS))
async def cmd_risk(message: Message, command: CommandObject | None = None) -> None:
    user = message.from_user
    if user is None:
        return
    try:
        args = extract_args(message, command)
        player = await hydrate(user.id, user.full_name, user.username)

        if not args:
            res = await _status_with_history(player.user_id)
        elif args[0].casefold() in _CASHOUT_ACTIONS:
            res = await risk_game.cashout(player.user_id)
        elif args[0].isdigit():
            economy.require_ready(user.id, risk_game.ACTION_RISK, settings.cooldown_risk)
            res = await risk_game.start(
                player.user_id, int(args[0]), chat_id=message.chat.id
            )
        else:
            raise GameError(_USAGE)
        await message.reply(res["message"])
    except Exception as exc:  # noqa: BLE001
        await answer_error(exc, message=message)
