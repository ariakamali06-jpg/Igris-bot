"""بورس — asset board, buy/sell and next-hour bets with interactive inline buttons.

English aliases avoid ``market``/``stocks`` on purpose: ``market`` is already
registered by the shop router, and dispatch order would silently swallow one
of the two.
"""

from __future__ import annotations

import contextlib
import logging

from aiogram import F, Router
from aiogram.filters import CommandObject
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from config import settings
from handlers.common import (
    CommandOrText,
    answer_error,
    editable_message,
    extract_args,
    hydrate,
)
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


def _market_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🪙 خرید طلا", callback_data="mkt:qbuy:طلا"),
                InlineKeyboardButton(text="🛢 خرید نفت", callback_data="mkt:qbuy:نفت"),
            ],
            [
                InlineKeyboardButton(text="💎 خرید الماس", callback_data="mkt:qbuy:الماس"),
                InlineKeyboardButton(text="₿ خرید بیت‌کوین", callback_data="mkt:qbuy:بیت‌کوین"),
            ],
            [
                InlineKeyboardButton(text="📈 شرط روی شاخص کل", callback_data="mkt:qbet:menu"),
                InlineKeyboardButton(text="💰 فروش دارایی‌ها", callback_data="mkt:qsell:menu"),
            ],
            [
                InlineKeyboardButton(text="🔄 به‌روزرسانی تابلو", callback_data="mkt:refresh"),
                InlineKeyboardButton(text="🚪 بستن", callback_data="btq:close"),
            ],
        ]
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
            await message.reply(res["message"], reply_markup=_market_markup())
        else:
            res = await _run_action(user.id, player, args)
            await message.reply(res["message"])
    except Exception as exc:  # noqa: BLE001
        await answer_error(exc, message=message)


@router.callback_query(F.data.in_({"mkt:refresh", "mkt:home"}))
async def cb_market_refresh(call: CallbackQuery) -> None:
    user = call.from_user
    if user is None:
        return
    player = await hydrate(user.id, user.full_name, user.username)
    res = await market.overview(player)
    msg = editable_message(call)
    if msg:
        with contextlib.suppress(Exception):
            await msg.edit_text(res["message"], reply_markup=_market_markup())
    await call.answer("🔄 تابلوی بورس به‌روزرسانی شد.")


@router.callback_query(F.data.startswith("mkt:qbuy:"))
async def cb_market_qbuy_menu(call: CallbackQuery) -> None:
    user = call.from_user
    if user is None or call.data is None:
        return
    asset = call.data.split(":")[2]
    text = (
        f"🛒 <b>خرید سهم یا دارایی «{asset}»:</b>\n\n"
        "مبلغ مورد نظر برای خرید را انتخاب کنید یا دستور زیر را بنویسید:\n"
        f"<code>بورس خرید {asset} [مبلغ]</code>"
    )
    markup = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="💵 500 رکسار", callback_data=f"mkt:do_buy:{asset}:500"
                ),
                InlineKeyboardButton(
                    text="💵 1,000 رکسار", callback_data=f"mkt:do_buy:{asset}:1000"
                ),
                InlineKeyboardButton(
                    text="💵 5,000 رکسار", callback_data=f"mkt:do_buy:{asset}:5000"
                ),
            ],
            [
                InlineKeyboardButton(text="🔙 بازگشت به بورس", callback_data="mkt:home"),
            ],
        ]
    )
    msg = editable_message(call)
    if msg:
        await msg.edit_text(text, reply_markup=markup)


@router.callback_query(F.data.startswith("mkt:do_buy:"))
async def cb_market_do_buy(call: CallbackQuery) -> None:
    user = call.from_user
    if user is None or call.data is None:
        return
    parts = call.data.split(":")
    if len(parts) < 4:
        return
    asset = parts[2]
    amount = int(parts[3])
    player = await hydrate(user.id, user.full_name, user.username)

    try:
        economy.require_ready(user.id, market.ACTION_TRADE, settings.cooldown_market_trade)
        await market.buy(player, asset, amount)
        await call.answer("✅ خرید با موفقیت انجام شد!", show_alert=True)
        overview_res = await market.overview(player)
        msg = editable_message(call)
        if msg:
            with contextlib.suppress(Exception):
                await msg.edit_text(overview_res["message"], reply_markup=_market_markup())
    except Exception as exc:  # noqa: BLE001
        await call.answer(f"❌ {exc}", show_alert=True)


@router.callback_query(F.data == "mkt:qsell:menu")
async def cb_market_qsell_menu(call: CallbackQuery) -> None:
    user = call.from_user
    if user is None:
        return
    text = (
        "💰 <b>فروش سبد دارایی‌های بورس:</b>\n\n"
        "برای نقد کردن کل موجودی یک دارایی به رکسار، گزینه مورد نظر را انتخاب کنید:"
    )
    markup = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🪙 فروش طلا", callback_data="mkt:do_sell:طلا"),
                InlineKeyboardButton(text="🛢 فروش نفت", callback_data="mkt:do_sell:نفت"),
            ],
            [
                InlineKeyboardButton(text="💎 فروش الماس", callback_data="mkt:do_sell:الماس"),
                InlineKeyboardButton(text="₿ فروش بیت‌کوین", callback_data="mkt:do_sell:بیت‌کوین"),
            ],
            [
                InlineKeyboardButton(text="🔙 بازگشت به بورس", callback_data="mkt:home"),
            ],
        ]
    )
    msg = editable_message(call)
    if msg:
        await msg.edit_text(text, reply_markup=markup)


@router.callback_query(F.data.startswith("mkt:do_sell:"))
async def cb_market_do_sell(call: CallbackQuery) -> None:
    user = call.from_user
    if user is None or call.data is None:
        return
    asset = call.data.split(":")[2]
    player = await hydrate(user.id, user.full_name, user.username)

    try:
        economy.require_ready(user.id, market.ACTION_TRADE, settings.cooldown_market_trade)
        res = await market.sell(player, asset)
        clean_msg = res["message"].replace("<b>", "").replace("</b>", "").replace("💵", "").strip()
        await call.answer(clean_msg[:200], show_alert=True)
        overview_res = await market.overview(player)
        msg = editable_message(call)
        if msg:
            with contextlib.suppress(Exception):
                await msg.edit_text(overview_res["message"], reply_markup=_market_markup())
    except Exception as exc:  # noqa: BLE001
        await call.answer(f"❌ {exc}", show_alert=True)


@router.callback_query(F.data == "mkt:qbet:menu")
async def cb_market_qbet_menu(call: CallbackQuery) -> None:
    user = call.from_user
    if user is None:
        return
    text = (
        "📈 <b>شرط روی افزایش شاخص کل بورس (ساعت آینده):</b>\n\n"
        "مبلغ شرط را انتخاب کنید یا دستور زیر را بنویسید:\n"
        "<code>بورس شرط [مبلغ]</code>"
    )
    markup = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🎲 100 رکسار", callback_data="mkt:do_bet:100"),
                InlineKeyboardButton(text="🎲 500 رکسار", callback_data="mkt:do_bet:500"),
                InlineKeyboardButton(text="🎲 1,000 رکسار", callback_data="mkt:do_bet:1000"),
            ],
            [
                InlineKeyboardButton(text="🔙 بازگشت به بورس", callback_data="mkt:home"),
            ],
        ]
    )
    msg = editable_message(call)
    if msg:
        await msg.edit_text(text, reply_markup=markup)


@router.callback_query(F.data.startswith("mkt:do_bet:"))
async def cb_market_do_bet(call: CallbackQuery) -> None:
    user = call.from_user
    if user is None or call.data is None:
        return
    amount = int(call.data.split(":")[2])
    player = await hydrate(user.id, user.full_name, user.username)

    try:
        economy.require_ready(user.id, market.ACTION_BET, settings.cooldown_market_bet)
        await market.place_bet(player, amount)
        await call.answer("🎲 شرط شما روی شاخص ثبت شد!", show_alert=True)
        overview_res = await market.overview(player)
        msg = editable_message(call)
        if msg:
            with contextlib.suppress(Exception):
                await msg.edit_text(overview_res["message"], reply_markup=_market_markup())
    except Exception as exc:  # noqa: BLE001
        await call.answer(f"❌ {exc}", show_alert=True)
