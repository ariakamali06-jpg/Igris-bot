"""جام — group football cup commands (status / join / results)."""

from __future__ import annotations

import logging

from aiogram import Router
from aiogram.filters import CommandObject
from aiogram.types import Message

from config import settings
from handlers.common import CommandOrText, answer_error, extract_args, hydrate
from services import cup, economy
from services.game import GameError

logger = logging.getLogger(__name__)
router = Router(name="cup")

CUP_WORDS = {"جام"}

_JOIN_ACTIONS = {"ثبت", "join", "شرکت"}
_RESULTS_ACTIONS = {"نتایج", "نتیجه", "results", "result"}

_USAGE = (
    "🏆 <b>جام فوتبال خیابان</b>\n"
    "• <code>جام</code> — وضعیت دور جاری (بعد از پایان، تسویه خودکار)\n"
    "• <code>جام ثبت</code> — شرکت با ورودیه "
    f"{settings.cup_entry_fee:,} سکه\n"
    "• <code>جام نتایج</code> — تسویه و نتایج دور پایان‌یافته"
)


@router.message(CommandOrText(["cup", "tournament"], words=CUP_WORDS))
async def cmd_cup(message: Message, command: CommandObject | None = None) -> None:
    user = message.from_user
    if user is None:
        return
    try:
        args = extract_args(message, command)
        chat_id = message.chat.id if message.chat else 0
        player = await hydrate(user.id, user.full_name, user.username)
        if not args:
            res = await cup.status(chat_id, player)
        else:
            action = args[0].casefold()
            if action in _JOIN_ACTIONS:
                economy.require_ready(user.id, "cup", settings.cooldown_cup)
                res = await cup.join(player, chat_id)
            elif action in _RESULTS_ACTIONS:
                res = await cup.status(chat_id, player)
            else:
                raise GameError(_USAGE)
        await message.reply(res["message"])
    except Exception as exc:  # noqa: BLE001
        await answer_error(exc, message=message)
