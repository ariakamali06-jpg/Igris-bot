"""بازارچه — player-to-player listing commands with glass-button UI."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from aiogram import F, Router
from aiogram.filters import CommandObject
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    User,
)

from config import settings
from handlers.common import CommandOrText, answer_error, editable_message, hydrate
from services import avatar, bazaar, economy, game
from services.game import GameError

logger = logging.getLogger(__name__)
router = Router(name="bazaar")

BAZAAR_WORDS = {"بازارچه", "bazaar", "marketplace"}

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


def _bazaar_markup(listings: list[dict] | None = None, user_id: int = 0) -> InlineKeyboardMarkup:
    """ساخت دکمه‌های شیشه‌ای بازارچه برای خرید و مدیریت آگهی‌ها."""
    rows: list[list[InlineKeyboardButton]] = []

    if listings:
        for item in listings[:10]:  # نمایش حداکثر ۱۰ آگهی
            listing_id = item["id"]
            item_name = item.get("item_name", item["item_id"])[:20]
            price = item["price"]
            item.get("seller", "ناشناس")[:15]

            # اگر آگهی مال خود کاربر است، دکمه لغو نشان بده
            if item.get("seller_id") == user_id:
                btn_text = f"🗑 لغو #{listing_id} • {item_name}"
                callback = f"bzr:cancel:{listing_id}"
            else:
                btn_text = f"🛒 خرید #{listing_id} • {item_name} ({price:,} رکسار)"
                callback = f"bzr:buy:{listing_id}"

            rows.append([InlineKeyboardButton(text=btn_text, callback_data=callback)])

    # دکمه‌های اصلی
    rows.append([
        InlineKeyboardButton(text="➕ ثبت آگهی فروش", callback_data="bzr:sell_menu"),
        InlineKeyboardButton(text="🔄 تازه‌سازی", callback_data="bzr:refresh"),
    ])
    rows.append([
        InlineKeyboardButton(text="🏠 صفحه اصلی", callback_data="act:me"),
        InlineKeyboardButton(text="🚪 بستن", callback_data="btq:close"),
    ])

    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _render_bazaar_view(user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    """رندر نمای اصلی بازارچه با لیست آگهی‌های فعال."""
    result = await bazaar.browse(limit=20)
    listings = result.get("listings", [])

    if not listings:
        text = (
            "🧺 <b>بازارچه کوچه تیرامیکس</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "هیچ آگهی فعالی در حال حاضر وجود ندارد.\n\n"
            "💡 برای فروش یک آیتم از کمد خود، روی دکمه «➕ ثبت آگهی فروش» کلیک کنید."
        )
    else:
        lines = [
            "🧺 <b>بازارچه کوچه تیرامیکس</b>",
            "━━━━━━━━━━━━━━━━━━━━",
            f"📋 {len(listings)} آگهی فعال:",
            "",
        ]
        for idx, item in enumerate(listings[:10], 1):
            item_name = bazaar._item_name(item["item_id"])
            price = f"{item['price']:,}"
            seller = item["seller"]
            lines.append(f"{idx}. <b>{item_name}</b> — {price} رکسار (فروشنده: {seller})")

        lines.append("")
        lines.append("💡 روی هر آیتم کلیک کنید تا آن را خریداری کنید.")
        text = "\n".join(lines)

    # اضافه کردن item_name به هر listing
    for item in listings:
        item["item_name"] = bazaar._item_name(item["item_id"])

    markup = _bazaar_markup(listings, user_id)
    return text, markup


@router.message(
    CommandOrText(
        ["bazaar", "marketplace"],
        words=BAZAAR_WORDS,
        arg_words=_LIST_ACTIONS | _BUY_ACTIONS | _SELL_ACTIONS | _CANCEL_ACTIONS,
        name_tail=True,
    )
)
async def cmd_bazaar(message: Message, command: CommandObject | None = None) -> None:
    """نمایش بازارچه با UI شیشه‌ای."""
    user = message.from_user
    if user is None:
        return
    try:
        await hydrate(user.id, user.full_name, user.username)
        text, markup = await _render_bazaar_view(user.id)
        await message.reply(text, reply_markup=markup)
    except Exception as exc:  # noqa: BLE001
        await answer_error(exc, message=message)


async def _sell_menu_view(user_id: int) -> tuple[str, InlineKeyboardMarkup] | None:
    """مرحله ۱ فروش: آیتم‌های آزاد (کوله‌پشتی + کمد) — ``None`` یعنی چیزی برای فروش نیست."""
    inv_rows = await game.list_inventory(user_id)
    wdr_items = await avatar.list_owned_items(user_id)

    sellable: list[dict[str, str]] = []
    for row in inv_rows:
        if not row["equipped"]:
            sellable.append({
                "id": f"rpg:{row['id']}",
                "name": f"🎒 {row['name']}",
                "cat": "تجهیزات",
            })
    for it in wdr_items:
        if not it.get("is_equipped"):
            sellable.append({
                "id": f"wdr:{it['var_id']}",
                "name": f"👗 {it['name']}",
                "cat": it.get("category", "پوشاک"),
            })

    if not sellable:
        return None

    text = (
        "➕ <b>ثبت آگهی فروش در بازارچه</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"📦 شما {len(sellable)} آیتم آزاد و قابل فروش دارید.\n\n"
        "کالای مورد نظر برای فروش را انتخاب کنید:"
    )
    rows: list[list[InlineKeyboardButton]] = []
    for item in sellable[:10]:
        rows.append([
            InlineKeyboardButton(
                text=f"{item['name'][:25]}",
                callback_data=f"bzr:sel:{item['id']}",
            )
        ])
    if len(sellable) > 10:
        rows.append([
            InlineKeyboardButton(
                text=f"⚠️ ۱۰ مورد اول نمایش داده شد ({len(sellable)} کل)",
                callback_data="bzr:noop",
            )
        ])
    rows.append([
        InlineKeyboardButton(text="🔙 بازگشت به بازارچه", callback_data="bzr:refresh")
    ])
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


def _price_picker_view(item_code: str) -> tuple[str, InlineKeyboardMarkup]:
    """مرحله ۲ فروش: پیشنهاد قیمت‌های آماده یا ورود قیمت دلخواه."""
    clean_query = item_code.replace("rpg:", "")
    item_name = bazaar._item_name(clean_query)
    text = (
        "💰 <b>تعیین قیمت فروش</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"📦 کالای انتخابی: <b>{item_name}</b>\n\n"
        "قیمت مورد نظر خود را انتخاب کنید:\n"
        f"💵 حداقل: {settings.bazaar_price_min:,} رکسار\n"
        f"💵 حداکثر: {settings.bazaar_price_max:,} رکسار\n\n"
        f"⚠️ کارمزد ثبت آگهی: {settings.bazaar_listing_fee:,} رکسار"
    )

    suggested_prices = [500, 1000, 2500, 5000, 10000, 25000]
    kb_rows: list[list[InlineKeyboardButton]] = []
    for i in range(0, len(suggested_prices), 2):
        kb_row = []
        for j in range(2):
            if i + j < len(suggested_prices):
                price = suggested_prices[i + j]
                if settings.bazaar_price_min <= price <= settings.bazaar_price_max:
                    kb_row.append(
                        InlineKeyboardButton(
                            text=f"💰 {price:,} رکسار",
                            callback_data=f"bzr:set_price:{price}:{item_code}",
                        )
                    )
        if kb_row:
            kb_rows.append(kb_row)

    kb_rows.append([
        InlineKeyboardButton(
            text="✏️ قیمت دلخواه (متنی)",
            callback_data=f"bzr:custom_price:{item_code}",
        )
    ])
    kb_rows.append([
        InlineKeyboardButton(text="🔙 انتخاب آیتم دیگر", callback_data="bzr:sell_menu")
    ])
    return text, InlineKeyboardMarkup(inline_keyboard=kb_rows)


async def _bzr_refresh(
    call: CallbackQuery, message: Message, user: User, parts: list[str]
) -> None:
    text, markup = await _render_bazaar_view(user.id)
    await message.edit_text(text, reply_markup=markup)
    await call.answer("🔄 لیست به‌روز شد")


async def _bzr_buy(
    call: CallbackQuery, message: Message, user: User, parts: list[str]
) -> None:
    if len(parts) < 3:
        await call.answer("❌ شناسه آگهی نامعتبر است", show_alert=True)
        return
    listing_id = int(parts[2])
    economy.require_ready(user.id, "bazaar_buy", settings.cooldown_bazaar)
    result = await bazaar.buy_listing(user.id, listing_id)
    text, markup = await _render_bazaar_view(user.id)
    await message.edit_text(text, reply_markup=markup)
    await call.answer(f"✅ {result.get('message', 'خرید موفق!')}", show_alert=True)


async def _bzr_cancel(
    call: CallbackQuery, message: Message, user: User, parts: list[str]
) -> None:
    if len(parts) < 3:
        await call.answer("❌ شناسه آگهی نامعتبر است", show_alert=True)
        return
    listing_id = int(parts[2])
    economy.require_ready(user.id, "bazaar_cancel", settings.cooldown_bazaar)
    result = await bazaar.cancel(user.id, listing_id)
    text, markup = await _render_bazaar_view(user.id)
    await message.edit_text(text, reply_markup=markup)
    await call.answer(f"✅ {result.get('message', 'آگهی لغو شد')}", show_alert=True)


async def _bzr_sell_menu(
    call: CallbackQuery, message: Message, user: User, parts: list[str]
) -> None:
    view = await _sell_menu_view(user.id)
    if view is None:
        await call.answer(
            "❌ هیچ آیتم آزادی در کیفت یا کمدت نداری! (آیتم‌های بر تن قابل فروش نیستند)",
            show_alert=True,
        )
        return
    text, markup = view
    await message.edit_text(text, reply_markup=markup)
    await call.answer()


async def _bzr_select(
    call: CallbackQuery, message: Message, user: User, parts: list[str]
) -> None:
    if len(parts) < 3:
        await call.answer("❌ خطا در انتخاب کالا", show_alert=True)
        return
    item_code = ":".join(parts[2:])
    text, markup = _price_picker_view(item_code)
    await message.edit_text(text, reply_markup=markup)
    await call.answer()


async def _bzr_custom_price(
    call: CallbackQuery, message: Message, user: User, parts: list[str]
) -> None:
    if len(parts) < 3:
        await call.answer("❌ خطا", show_alert=True)
        return
    item_code = ":".join(parts[2:])
    clean_query = item_code.replace("rpg:", "")
    item_name = bazaar._item_name(clean_query)
    text = (
        "✏️ <b>ورود قیمت دلخواه</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"📦 کالا: <b>{item_name}</b>\n\n"
        "برای تعیین قیمت دلخواه، از دستور زیر در چت استفاده کنید:\n\n"
        f"<code>بازارچه فروش {clean_query} [قیمت]</code>\n\n"
        f"💵 مثال: <code>بازارچه فروش {clean_query} 15000</code>\n\n"
        f"⚠️ کارمزد ثبت: {settings.bazaar_listing_fee:,} رکسار"
    )
    markup = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔙 بازگشت", callback_data=f"bzr:sel:{item_code}")]
        ]
    )
    await message.edit_text(text, reply_markup=markup)
    await call.answer()


async def _bzr_set_price(
    call: CallbackQuery, message: Message, user: User, parts: list[str]
) -> None:
    """مرحله ۳: ثبت آگهی + کسر کارمزد در یک تراکنش اتمیک (خطا به هندلر مرکزی می‌رسد)."""
    if len(parts) < 4:
        await call.answer("❌ خطا در تعیین قیمت", show_alert=True)
        return
    price = int(parts[2])
    item_code = ":".join(parts[3:])
    clean_query = item_code.replace("rpg:", "")
    economy.require_ready(user.id, "bazaar_sell", settings.cooldown_bazaar)
    await bazaar.list_item(user.id, clean_query, price)
    text, markup = await _render_bazaar_view(user.id)
    await message.edit_text(text, reply_markup=markup)
    await call.answer(
        f"✅ آگهی ثبت شد! ({settings.bazaar_listing_fee:,} رکسار کارمزد کسر شد)",
        show_alert=True,
    )


async def _bzr_noop(
    call: CallbackQuery, message: Message, user: User, parts: list[str]
) -> None:
    await call.answer()


_BZR_ACTIONS: dict[str, Callable[[CallbackQuery, Message, User, list[str]], Awaitable[None]]] = {
    "refresh": _bzr_refresh,
    "buy": _bzr_buy,
    "cancel": _bzr_cancel,
    "sell_menu": _bzr_sell_menu,
    "sel": _bzr_select,
    "sel_item": _bzr_select,
    "custom_price": _bzr_custom_price,
    "cprc": _bzr_custom_price,
    "set_price": _bzr_set_price,
    "noop": _bzr_noop,
}


@router.callback_query(F.data.startswith("bzr:"))
async def cb_bazaar(call: CallbackQuery) -> None:
    """مدیریت callback های بازارچه — دیسپچ به هندلرهای کوچک با جدول."""
    message = editable_message(call)
    if message is None:
        await call.answer()
        return
    user = call.from_user
    if user is None or call.data is None:
        return

    try:
        parts = call.data.split(":")
        action = parts[1] if len(parts) > 1 else "refresh"
        handler = _BZR_ACTIONS.get(action)
        if handler is not None:
            await handler(call, message, user, parts)
    except GameError as ge:
        await call.answer(f"❌ {ge}", show_alert=True)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Bazaar callback error: %s", exc)
        await call.answer("⛔ خطای غیرمنتظره‌ای رخ داد. دوباره تلاش کن.", show_alert=True)


async def _dispatch(user_id: int, args: list[str]) -> dict:
    """Legacy text command dispatcher (kept for backward compatibility)."""
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
