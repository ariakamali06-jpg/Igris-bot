"""رستوران و کافه مرکزی شهر تیرامیکس — خرید غذا و بازیابی انرژی."""

from __future__ import annotations

import contextlib
import logging

from aiogram import F, Router
from aiogram.filters import CommandObject
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from config import FOOD_CATALOG
from handlers.common import (
    CommandOrText,
    answer_error,
    editable_message,
    energy_bar,
    extract_args,
    hydrate,
)
from models import Player
from services import economy, food

logger = logging.getLogger(__name__)
router = Router(name="food")

FOOD_WORDS = {
    "غذا",
    "رستوران",
    "کافه",
    "خوردن",
    "نوشیدنی",
    "خوراک",
    "ناهار",
    "شام",
    "صبحانه",
}


def _food_markup() -> InlineKeyboardMarkup:
    """چیدمان دکمه‌های شیشه‌ای رستوران تیرامیکس برای خرید و مصرف فوری غذا."""
    buttons: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(text="☕️ اسپرسو (+25⚡️) | 250", callback_data="food:buy:espresso"),
            InlineKeyboardButton(
                text="⚡️ انرژی‌زا (+40⚡️) | 450", callback_data="food:buy:energy_drink"
            ),
        ],
        [
            InlineKeyboardButton(
                text="🥪 ساندویچ (+55⚡️) | 700", callback_data="food:buy:sandwich"
            ),
            InlineKeyboardButton(
                text="🍔 برگر دوبل (+75⚡️) | 1,100", callback_data="food:buy:burger"
            ),
        ],
        [
            InlineKeyboardButton(text="🍕 پیتزا (+85⚡️) | 1,500", callback_data="food:buy:pizza"),
            InlineKeyboardButton(
                text="🥩 چلوکباب (+100⚡️) | 2,200", callback_data="food:buy:chelokabab"
            ),
        ],
        [
            InlineKeyboardButton(text="💼 رفتن سر کار", callback_data="act:work"),
            InlineKeyboardButton(text="🔄 به‌روزرسانی", callback_data="food:menu"),
            InlineKeyboardButton(text="🚪 خروج", callback_data="food:close"),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


async def _render_food_view(player: Player) -> tuple[str, InlineKeyboardMarkup]:
    credits_bal, _ = await economy.balances(player.user_id)
    bar = energy_bar(player.energy, 100)
    lines = [
        "🍽 <b>رستوران و کافه مرکزی شهر تیرامیکس</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        f"👤 <b>شهروند:</b> {player.display_name}",
        f"⚡️ <b>سطح انرژی شما:</b> <b>{player.energy}/100</b> {bar}",
        f"💰 <b>موجودی کیف پول:</b> <b>{credits_bal:,}</b> رکسار",
        "",
        "📌 <b>منوی سرآشپز تیرامیکس:</b>",
    ]
    for it in FOOD_CATALOG:
        pr = f"{it['price']:,}"
        en = f"+{it['energy']}"
        lines.append(
            f"• <b>{it['name']}</b> — <b>{pr}</b> رکسار · ⚡️ <b>{en} انرژی</b>\n"
            f"  <i>{it['desc']}</i>"
        )
    lines.append("")
    lines.append("💡 <i>برای سفارش و میل کردن، دکمه غذای مورد نظرتان را لمس کنید!</i>")
    return "\n".join(lines), _food_markup()


@router.message(CommandOrText(["food", "restaurant", "cafe", "eat"], words=FOOD_WORDS))
async def cmd_food(message: Message, command: CommandObject | None = None) -> None:
    """مشاهده منوی رستوران و کافه یا خرید مستقیم غذا با متن."""
    user = message.from_user
    if user is None:
        return
    try:
        player = await hydrate(user.id, user.full_name, user.username)
        args = extract_args(message, command)
        if not args:
            text, markup = await _render_food_view(player)
            await message.reply(text, reply_markup=markup)
            return

        target_food = " ".join(args).strip()
        res = await food.buy_and_eat_food(player, target_food)
        await message.reply(res["message"])
    except Exception as exc:  # noqa: BLE001
        await answer_error(exc, message=message)


@router.callback_query(F.data.startswith("food:"))
async def cb_food_action(call: CallbackQuery) -> None:
    """پردازش خرید غذا و نوشیدنی با دکمه‌های شیشه‌ای رستوران."""
    user = call.from_user
    if user is None or not call.data:
        return

    parts = call.data.split(":")
    act = parts[1] if len(parts) > 1 else "menu"

    if act == "close":
        if call.message:
            with contextlib.suppress(Exception):
                await call.message.delete()
        await call.answer("با آرزوی اوقات خوش در شهر تیرامیکس! ✨")
        return

    player = await hydrate(user.id, user.full_name, user.username)

    if act == "buy":
        food_id = parts[2] if len(parts) > 2 else "sandwich"
        res = await food.buy_and_eat_food(player, food_id)
        alert_txt = (
            res["message"]
            .replace("<b>", "")
            .replace("</b>", "")
            .replace("<code>", "")
            .replace("</code>", "")
        )
        await call.answer(alert_txt[:200], show_alert=True)
    else:
        await call.answer()

    fresh = await hydrate(user.id, user.full_name, user.username)
    text, markup = await _render_food_view(fresh)
    msg = editable_message(call)
    if msg:
        with contextlib.suppress(Exception):
            await msg.edit_text(text, reply_markup=markup, parse_mode="HTML")
