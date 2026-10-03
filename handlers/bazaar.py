"""بازارچه — player-to-player listing commands (خرید / فروش / لغو)."""

from __future__ import annotations

import logging

from aiogram import Router
from aiogram.filters import CommandObject
from aiogram.types import Message

from config import settings
from handlers.common import CommandOrText, answer_error, extract_args
from services import bazaar, economy
from services.game import GameError

logger = logging.getLogger(__name__)
router = Router(name="bazaar")

BAZAAR_WORDS = {"بازارچه"}

_LIST_ACTIONS = {"list", "show", "نمایش", "لیست"}
_BUY_ACTIONS = {"buy", "خرید"}
_SELL_ACTIONS = {"sell", "فروش"}
_CANCEL_ACTIONS = {"cancel", "لغو"}

_USAGE = (
    "🧺 <b>بازارچه کوچه</b>\n"
    "• <code>بازارچه</code> — فهرست آگهی‌های فعال\n"
    "• <code>بازارچه خرید [ردیف]</code>\n"
    "• <code>بازارچه فروش [نام کالا] [قیمت]</code>\n"
    "• <code>بازارچه لغو [ردیف]</code>"
)


@router.message(CommandOrText(["bazaar"], words=BAZAAR_WORDS))
async def cmd_bazaar(message: Message, command: CommandObject | None = None) -> None:
    user = message.from_user
    if user is None:
        return
    try:
        args = extract_args(message, command)
        res = await _dispatch(user.id, args)
        await message.reply(res["message"])
    except Exception as exc:  # noqa: BLE001
        await answer_error(exc, message=message)


async def _dispatch(user_id: int, args: list[str]) -> dict:
    if not args or args[0].casefold() in _LIST_ACTIONS:
        return await bazaar.browse()

    action = args[0].casefold()
    if action in _BUY_ACTIONS:
        if len(args) < 2 or not args[1].isdigit():
            raise GameError("ردیف آگهی رو بنویس: <code>بازارچه خرید [ردیف]</code>")
        economy.require_ready(user_id, "bazaar_buy", settings.cooldown_bazaar)
        return await bazaar.buy_listing(user_id, int(args[1]))

    if action in _SELL_ACTIONS:
        tokens = args[1:]
        if len(tokens) < 2 or not tokens[-1].isdigit():
            raise GameError(
                "نام کالا و قیمت رو بنویس: <code>بازارچه فروش [کالا] [قیمت]</code>"
            )
        economy.require_ready(user_id, "bazaar_sell", settings.cooldown_bazaar)
        price = int(tokens[-1])
        query = " ".join(tokens[:-1])
        return await bazaar.list_item(user_id, query, price)

    if action in _CANCEL_ACTIONS:
        if len(args) < 2 or not args[1].isdigit():
            raise GameError("ردیف آگهی رو بنویس: <code>بازارچه لغو [ردیف]</code>")
        economy.require_ready(user_id, "bazaar_cancel", settings.cooldown_bazaar)
        return await bazaar.cancel(user_id, int(args[1]))

    raise GameError(_USAGE)
