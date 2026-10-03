"""کاسب — black-market shelf commands (بازار سیاه)."""

from __future__ import annotations

import logging

from aiogram import Router
from aiogram.filters import CommandObject
from aiogram.types import Message

from config import settings
from handlers.common import CommandOrText, answer_error, extract_args, hydrate
from services import blackmarket, economy
from services.game import GameError

logger = logging.getLogger(__name__)
router = Router(name="blackmarket")

BLACKMARKET_WORDS = {"کاسب"}

_BUY_ACTIONS = {"خرید", "buy", "purchase"}

_USAGE = (
    "🕳 <b>کاسب زیر پل</b>\n"
    "• <code>کاسب</code> — فهرست بساط امروز\n"
    "• <code>کاسب خرید [نام]</code> — خرید از بازار سیاه"
)


@router.message(CommandOrText(["blackmarket", "fence"], words=BLACKMARKET_WORDS))
async def cmd_blackmarket(message: Message, command: CommandObject | None = None) -> None:
    user = message.from_user
    if user is None:
        return
    try:
        args = extract_args(message, command)
        if args and args[0].casefold() in _BUY_ACTIONS:
            if len(args) < 2:
                raise GameError(_USAGE)
            economy.require_ready(
                user.id, "blackmarket", settings.cooldown_blackmarket
            )
            player = await hydrate(user.id, user.full_name, user.username)
            res = await blackmarket.buy(user.id, " ".join(args[1:]), player.drip)
        else:
            res = await blackmarket.shelf()
        await message.reply(res["message"])
    except Exception as exc:  # noqa: BLE001
        await answer_error(exc, message=message)
