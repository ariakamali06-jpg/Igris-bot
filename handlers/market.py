"""بورس — asset board, buy/sell and next-hour bets.

English aliases avoid ``market``/``stocks`` on purpose: ``market`` is already
registered by the shop router, and dispatch order would silently swallow one
of the two.
"""

from __future__ import annotations

import logging

from aiogram import Router
from aiogram.filters import CommandObject
from aiogram.types import Message

from config import settings
from handlers.common import CommandOrText, answer_error, extract_args, hydrate
from services import economy, market
from services.game import GameError

logger = logging.getLogger(__name__)
router = Router(name="market")

MARKET_WORDS = {"بورس"}

_BUY_ACTIONS = {"خرید", "buy"}
_SELL_ACTIONS = {"فروش", "sell"}
_BET_ACTIONS = {"شرط", "bet", "شارت"}

_USAGE = (
    "📊 <b>دستورات بورس</b>\n"
    "• <code>بورس</code> — تابلوی قیمت‌ها و سبد دارایی تو\n"
    "• <code>بورس خرید [دارایی] [مبلغ]</code> — خرید "
    "(مثال: بورس خرید طلا 500)\n"
    "• <code>بورس فروش [دارایی]</code> — فروش کل سبد یک دارایی\n"
    "• <code>بورس شرط [مبلغ]</code> — شرط روی بالا رفتن شاخص ساعت بعد"
)


async def _run_action(user_id: int, player, args: list[str]) -> dict:
    action = args[0].casefold()

    if action in _BET_ACTIONS:
        economy.require_ready(user_id, market.ACTION_BET, settings.cooldown_market_bet)
        if len(args) < 2 or not args[1].isdigit():
            raise GameError(_USAGE)
        return await market.place_bet(player, int(args[1]))

    economy.require_ready(
        user_id, market.ACTION_TRADE, settings.cooldown_market_trade
    )

    if action in _BUY_ACTIONS:
        if len(args) < 3 or not args[-1].isdigit():
            raise GameError(_USAGE)
        amount = int(args[-1])
        return await market.buy(player, " ".join(args[1:-1]), amount)

    if action in _SELL_ACTIONS:
        if len(args) < 2:
            raise GameError(_USAGE)
        return await market.sell(player, " ".join(args[1:]))

    raise GameError(_USAGE)


@router.message(CommandOrText(["exchange", "stocks"], words=MARKET_WORDS))
async def cmd_market(message: Message, command: CommandObject | None = None) -> None:
    user = message.from_user
    if user is None:
        return
    try:
        player = await hydrate(user.id, user.full_name, user.username)
        args = extract_args(message, command)
        if not args:
            res = await market.overview(player)
        else:
            res = await _run_action(user.id, player, args)
        await message.reply(res["message"])
    except Exception as exc:  # noqa: BLE001
        await answer_error(exc, message=message)
