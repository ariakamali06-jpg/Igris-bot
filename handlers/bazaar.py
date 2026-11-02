"""بازارچه — player-to-player listing commands with glass-button UI."""

from __future__ import annotations

import logging
import time

from aiogram import F, Router
from aiogram.filters import CommandObject
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from config import settings
from database.connection import db
from handlers.common import CommandOrText, answer_error, hydrate
from models.enums import ActivityKind
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
            # مرحله ۱: جمع‌آوری آیتم‌های قابل فروش (هم کوله‌پشتی و هم کمد لباس)
            inv_rows = await game.list_inventory(user.id)
            wdr_items = await avatar.list_owned_items(user.id)

            sellable: list[dict[str, str]] = []
            # ۱. آیتم‌های غیرمجهز کوله‌پشتی
            for row in inv_rows:
                if not row["equipped"]:
                    sellable.append({
                        "id": f"rpg:{row['id']}",
                        "name": f"🎒 {row['name']}",
                        "cat": "تجهیزات",
                    })

            # ۲. آیتم‌های غیرمجهز کمد لباس
            for it in wdr_items:
                if not it.get("is_equipped"):
                    sellable.append({
                        "id": f"wdr:{it['var_id']}",
                        "name": f"👗 {it['name']}",
                        "cat": it.get("category", "پوشاک"),
                    })

            if not sellable:
                await call.answer(
                    "❌ هیچ آیتم آزادی در کیفت یا کمدت نداری! (آیتم‌های بر تن قابل فروش نیستند)",
                    show_alert=True,
                )
                return

            text = (
                "➕ <b>ثبت آگهی فروش در بازارچه</b>\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                f"📦 شما {len(sellable)} آیتم آزاد و قابل فروش دارید.\n\n"
                "کالای مورد نظر برای فروش را انتخاب کنید:"
            )

            rows: list[list[InlineKeyboardButton]] = []
            for item in sellable[:10]:
                code = item["id"]
                name = item["name"][:25]
                rows.append([
                    InlineKeyboardButton(
                        text=f"{name}",
                        callback_data=f"bzr:sel:{code}",
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

            markup = InlineKeyboardMarkup(inline_keyboard=rows)
            await call.message.edit_text(text, reply_markup=markup)
            await call.answer()

        elif action in ("sel", "sel_item"):
            # مرحله ۲: کالا انتخاب شد، حالا قیمت بپرس
            if len(parts) < 3:
                await call.answer("❌ خطا در انتخاب کالا", show_alert=True)
                return

            item_code = ":".join(parts[2:])
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

            # پیشنهاد قیمت‌های از پیش تعیین شده
            suggested_prices = [500, 1000, 2500, 5000, 10000, 25000]
            rows: list[list[InlineKeyboardButton]] = []

            for i in range(0, len(suggested_prices), 2):
                row = []
                for j in range(2):
                    if i + j < len(suggested_prices):
                        price = suggested_prices[i + j]
                        if settings.bazaar_price_min <= price <= settings.bazaar_price_max:
                            row.append(
                                InlineKeyboardButton(
                                    text=f"💰 {price:,} رکسار",
                                    callback_data=f"bzr:set_price:{price}:{item_code}",
                                )
                            )
                if row:
                    rows.append(row)

            rows.append([
                InlineKeyboardButton(
                    text="✏️ قیمت دلخواه (متنی)",
                    callback_data=f"bzr:custom_price:{item_code}",
                )
            ])
            rows.append([
                InlineKeyboardButton(text="🔙 انتخاب آیتم دیگر", callback_data="bzr:sell_menu")
            ])

            markup = InlineKeyboardMarkup(inline_keyboard=rows)
            await call.message.edit_text(text, reply_markup=markup)
            await call.answer()

        elif action in ("custom_price", "cprc"):
            # راهنمای ورود قیمت دستی
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
            await call.message.edit_text(text, reply_markup=markup)
            await call.answer()

        elif action == "set_price":
            # مرحله ۳: ثبت آگهی با کسر کارمزد
            if len(parts) < 4:
                await call.answer("❌ خطا در تعیین قیمت", show_alert=True)
                return

            price = int(parts[2])
            item_code = ":".join(parts[3:])
            clean_query = item_code.replace("rpg:", "")

            # بررسی موجودی برای کارمزد
            async with db.read() as conn:
                cursor = await conn.execute(
                    "SELECT credits FROM wallets WHERE user_id = ?",
                    (user.id,),
                )
                row = await cursor.fetchone()
                await cursor.close()
                current_credits = int(row["credits"]) if row else 0

            listing_fee = settings.bazaar_listing_fee

            if current_credits < listing_fee:
                await call.answer(
                    f"❌ موجودی شما برای پرداخت کارمزد ({listing_fee:,} رکسار) کافی نیست!",
                    show_alert=True,
                )
                return

            try:
                economy.require_ready(user.id, "bazaar_sell", settings.cooldown_bazaar)

                # ثبت آگهی
                result = await bazaar.list_item(user.id, clean_query, price)

                # کسر کارمزد
                async with db.write() as conn:
                    await conn.execute(
                        "UPDATE wallets SET credits = credits - ? WHERE user_id = ?",
                        (listing_fee, user.id),
                    )
                    await conn.execute(
                        """INSERT INTO activity_log
                           (user_id, kind, details, amount, created_at)
                           VALUES (?, ?, ?, ?, ?)""",
                        (
                            user.id,
                            ActivityKind.BAZAAR_FEE.value,
                            f"کارمزد ثبت آگهی #{result.get('listing_id', 0)}",
                            -listing_fee,
                            int(time.time()),
                        ),
                    )

                # بازگشت به لیست اصلی بازارچه
                text, markup = await _render_bazaar_view(user.id)
                await call.message.edit_text(text, reply_markup=markup)
                await call.answer(
                    f"✅ آگهی ثبت شد! ({listing_fee:,} رکسار کارمزد کسر شد)",
                    show_alert=True,
                )
            except GameError as ge:
                await call.answer(f"❌ {ge}", show_alert=True)

        elif action == "noop":
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
