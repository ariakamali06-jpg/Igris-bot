"""بازارچه — player-to-player listing commands with glass-button UI."""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import CommandObject
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from config import settings
from handlers.common import CommandOrText, answer_error, hydrate
from services import bazaar, economy
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


@router.message(CommandOrText(["/bazaar", "bazaar"], words=BAZAAR_WORDS))
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


@router.callback_query(F.data.startswith("bzr:"))
async def cb_bazaar(call: CallbackQuery) -> None:
    """مدیریت callback های بازارچه."""
    user = call.from_user
    if user is None or call.data is None:
        return

    try:
        parts = call.data.split(":")
        action = parts[1] if len(parts) > 1 else "refresh"

        if action == "refresh":
            text, markup = await _render_bazaar_view(user.id)
            await call.message.edit_text(text, reply_markup=markup)
            await call.answer("🔄 لیست به‌روز شد")

        elif action == "buy":
            if len(parts) < 3:
                await call.answer("❌ شناسه آگهی نامعتبر است", show_alert=True)
                return

            listing_id = int(parts[2])
            economy.require_ready(user.id, "bazaar_buy", settings.cooldown_bazaar)
            result = await bazaar.buy_listing(user.id, listing_id)

            # به‌روزرسانی لیست
            text, markup = await _render_bazaar_view(user.id)
            await call.message.edit_text(text, reply_markup=markup)
            await call.answer(f"✅ {result.get('message', 'خرید موفق!')}", show_alert=True)

        elif action == "cancel":
            if len(parts) < 3:
                await call.answer("❌ شناسه آگهی نامعتبر است", show_alert=True)
                return

            listing_id = int(parts[2])
            economy.require_ready(user.id, "bazaar_cancel", settings.cooldown_bazaar)
            result = await bazaar.cancel(user.id, listing_id)

            # به‌روزرسانی لیست
            text, markup = await _render_bazaar_view(user.id)
            await call.message.edit_text(text, reply_markup=markup)
            await call.answer(f"✅ {result.get('message', 'آگهی لغو شد')}", show_alert=True)

        elif action == "sell_menu":
            help_text = (
                "➕ <b>ثبت آگهی فروش در بازارچه</b>\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                "برای فروش یک آیتم از کمد خود، از دستور زیر استفاده کنید:\n\n"
                "<code>بازارچه فروش [نام آیتم] [قیمت]</code>\n\n"
                "مثال:\n"
                "<code>بازارچه فروش پیراهن_آبی 5000</code>\n\n"
                f"💰 حداقل قیمت: {settings.bazaar_price_min:,} رکسار\n"
                f"💰 حداکثر قیمت: {settings.bazaar_price_max:,} رکسار\n"
                f"📋 حداکثر آگهی همزمان: {settings.bazaar_max_active}"
            )
            back_markup = InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="🔙 بازگشت به بازارچه", callback_data="bzr:refresh")]
                ]
            )
            await call.message.edit_text(help_text, reply_markup=back_markup)
            await call.answer()

    except GameError as ge:
        await call.answer(f"❌ {ge}", show_alert=True)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Bazaar callback error")
        await call.answer(f"❌ خطا: {exc}", show_alert=True)


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
