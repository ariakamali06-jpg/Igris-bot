"""هندلرهای اقتصاد، مشاغل، بانک، تحصیل و سیستم راهنمای جامع شهر تیرامیکس.

شامل:
- پاداش روزانه و حقوق شهروندی (/daily)
- شیفت کاری و کسب درآمد بر اساس شغل (/work)
- تحصیل و ارتقای مدرک دانشگاهی (/study)
- کاریابی و استخدام در مشاغل تخصصی (/job)
- خدمات بانک مرکزی، سپرده‌گذاری و وام (/bank)
- بررسی موجودی کیف پول و گاوصندوق (/balance)
- کارنامه آمار، انرژی و مشخصات کاراکتر (/stats)
- سرقت‌های برنامه‌ریزی‌شده و بازی‌های شانس (/heist, /dice, /coinflip)
- سیستم راهنمای جامع و منوی کلیدهای میانبر تیرامیکس (/help)
"""

from __future__ import annotations

import contextlib
import logging
from typing import Any

from aiogram import F, Router
from aiogram.filters import CommandObject
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from config import settings
from handlers.common import (
    CommandOrText,
    answer_error,
    editable_message,
    energy_bar,
    esc,
    extract_args,
    hydrate,
)
from models.player import Player
from services import economy
from services.game import GameError, exp_to_next

logger = logging.getLogger(__name__)
router = Router(name="economy")


def _activity_card(result: economy.ActivityResult) -> str:
    """قالب‌بندی کارت نتایج فعالیت‌های اقتصادی و تغییرات موجودی و اکس‌پی."""
    icon = "✅" if result.success else "❌"
    lines = [f"{icon} <b>{esc(result.headline)}</b>"]
    if result.detail:
        lines.append(esc(result.detail))
    bits = []
    if result.credits_delta:
        sign = "+" if result.credits_delta > 0 else ""
        bits.append(f"{sign}{result.credits_delta:,} رکسار")
    if result.exp_gained:
        bits.append(f"+{result.exp_gained} EXP")
    if bits:
        lines.append(" · ".join(bits))
    return "\n".join(lines)


DAILY_WORDS = {"روزانه", "جایزه", "پاداش", "حقوق", "daily", "claim"}
WORK_WORDS = {"کار", "شیفت", "work", "shift"}
BAL_WORDS = {"موجودی", "پول", "سکه", "کیف", "کیف پول", "حساب", "balance", "bal", "wallet", "money"}
STATS_WORDS = {"استاتس", "لول", "وضعیت", "stats", "level"}
HELP_WORDS = {"راهنما", "کمک", "آموزش", "اموزش", "دستورات", "help"}


# ---------------------------------------------------------------------------
# پاداش روزانه و حقوق شهروندی (/daily)
# ---------------------------------------------------------------------------


@router.message(CommandOrText(["daily", "claim"], DAILY_WORDS))
async def cmd_daily(message: Message) -> None:
    """دریافت حقوق و پاداش دوره‌ای ۲۴ ساعته با احتساب بانس استایل (DRIP)."""
    user = message.from_user
    if user is None:
        return
    try:
        economy.require_ready(user.id, "daily", settings.cooldown_daily)
        player = await hydrate(user.id, user.full_name, user.username)
        result = await economy.claim_daily(player.user_id, player.drip)
        if result.success and result.exp_gained:
            from services.game import grant_exp

            await grant_exp(player.user_id, result.exp_gained)
        await message.reply(_activity_card(result))
    except Exception as exc:  # noqa: BLE001
        await answer_error(exc, message=message)


# ---------------------------------------------------------------------------
# شیفت کاری و اشتغال (/work)
# ---------------------------------------------------------------------------


@router.message(CommandOrText(["work", "shift"], WORK_WORDS))
async def cmd_work(message: Message) -> None:
    """حضور در شیفت کاری و دریافت دستمزد ساعتی متناسب با عنوان شغلی بازیکن."""
    user = message.from_user
    if user is None:
        return
    try:
        player = await hydrate(user.id, user.full_name, user.username)
        from services import tiramix

        res = await tiramix.execute_work(player)
        await message.reply(res["message"])
    except Exception as exc:  # noqa: BLE001
        await answer_error(exc, message=message)


# ---------------------------------------------------------------------------
# تحصیل و ارتقای مدرک دانشگاهی (/study)
# ---------------------------------------------------------------------------


@router.message(CommandOrText(["study", "edu"], {"تحصیل", "دانشگاه", "مدرک", "کلاس"}))
async def cmd_study(message: Message) -> None:
    """حضور در کلاس‌های دانشگاه تیرامیکس جهت ارتقای مدرک تحصیلی و دسترسی به مشاغل عالی."""
    user = message.from_user
    if user is None:
        return
    try:
        player = await hydrate(user.id, user.full_name, user.username)
        from services import tiramix

        res = await tiramix.execute_study(player)
        await message.reply(res["message"])
    except Exception as exc:  # noqa: BLE001
        await answer_error(exc, message=message)


# ---------------------------------------------------------------------------
# اداره کار و انتخاب شغل (/job)
# ---------------------------------------------------------------------------


def _job_markup(
    unlocked_jobs: list[dict[str, Any]], current_job: str
) -> InlineKeyboardMarkup:
    """دکمه‌های شیشه‌ای انتخاب و استخدام در فرصت‌های شغلی."""
    buttons: list[list[InlineKeyboardButton]] = []
    for j in unlocked_jobs:
        title = j["title"]
        wage = j["wage"]
        if title == current_job:
            buttons.append([
                InlineKeyboardButton(
                    text=f"✅ {title} (شغل فعلی شما)", callback_data="noop"
                )
            ])
        else:
            buttons.append([
                InlineKeyboardButton(
                    text=f"💼 استخدام: {title} ({wage:,} رکسار/ساعت)",
                    callback_data=f"job:set:{title}",
                )
            ])

    buttons.append([
        InlineKeyboardButton(text="🔨 رفتن به شیفت کاری (کار)", callback_data="act:work"),
        InlineKeyboardButton(text="🚪 بستن", callback_data="btq:close"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def _render_job_view(player: Player) -> tuple[str, InlineKeyboardMarkup]:
    from services import tiramix

    unlocked_jobs = tiramix.list_available_jobs(player.education_level)
    lines = [
        "💼 <b>اداره کار و استخدام مرکزی شهر تیرامیکس</b>\n",
        f"👤 شغل فعلی شما: <b>{player.job}</b>",
        f"🎓 مدرک تحصیلی: <b>{player.education_title}</b>\n",
        "📋 <b>فرصت‌های شغلی متناسب با مدرک شما:</b>",
    ]
    for j in unlocked_jobs:
        lines.append(
            f"• <b>{j['title']}</b>: ساعتی <b>{j['wage']:,}</b> رکسار\n  <i>{j['desc']}</i>"
        )

    lines.append("\n💡 <i>روی دکمه شغل مورد نظر بزنید یا دستور زیر را بنویسید:</i>")
    lines.append("<code>شغل [نام شغل]</code>")

    markup = _job_markup(unlocked_jobs, player.job)
    return "\n".join(lines), markup


@router.message(CommandOrText(["job", "career"], {"شغل", "کارها", "استخدام"}))
async def cmd_job(message: Message, command: CommandObject | None = None) -> None:
    """مشاهده لیست مشاغل باز متناسب با تحصیلات یا استخدام در یک موقعیت کاری جدید."""
    user = message.from_user
    if user is None:
        return
    try:
        player = await hydrate(user.id, user.full_name, user.username)
        from services import tiramix

        args = extract_args(message, command)
        if args:
            wanted_job = " ".join(args)
            res = await tiramix.set_player_job(player, wanted_job)
            await message.reply(res["message"])
            return

        text, markup = _render_job_view(player)
        await message.reply(text, reply_markup=markup)
    except Exception as exc:  # noqa: BLE001
        await answer_error(exc, message=message)


@router.callback_query(F.data.startswith("job:set:"))
async def cb_job_set(call: CallbackQuery) -> None:
    """استخدام فوری با کلیک دکمه‌های شیشه‌ای اداره کار."""
    user = call.from_user
    if user is None or not call.data:
        return
    job_title = call.data[len("job:set:") :].strip()
    try:
        from services import tiramix

        player = await hydrate(user.id, user.full_name, user.username)
        res = await tiramix.set_player_job(player, job_title)
        alert_txt = (
            res["message"]
            .replace("<b>", "")
            .replace("</b>", "")
            .replace("<code>", "")
            .replace("</code>", "")
        )
        await call.answer(alert_txt[:200], show_alert=True)

        fresh = await hydrate(user.id, user.full_name, user.username)
        text, markup = _render_job_view(fresh)
        msg = editable_message(call)
        if msg:
            with contextlib.suppress(Exception):
                await msg.edit_text(text, reply_markup=markup, parse_mode="HTML")
    except Exception as exc:  # noqa: BLE001
        await answer_error(exc, callback=call)


# ---------------------------------------------------------------------------
# بانک مرکزی، گاوصندوق و وام (/bank)
# ---------------------------------------------------------------------------


def _bank_markup() -> InlineKeyboardMarkup:
    """چیدمان دکمه‌های شیشه‌ای بانک مرکزی تیرامیکس."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📥 واریز به بانک", callback_data="bank:ui:deposit"),
                InlineKeyboardButton(text="📤 برداشت از بانک", callback_data="bank:ui:withdraw"),
            ],
            [
                InlineKeyboardButton(text="💳 دریافت وام", callback_data="bank:ui:loan"),
                InlineKeyboardButton(text="🧾 تسویه بدهی وام", callback_data="bank:ui:repay"),
            ],
            [
                InlineKeyboardButton(
                    text="💸 کارت به کارت (انتقال رکسار)", callback_data="bank:ui:transfer"
                ),
            ],
            [
                InlineKeyboardButton(text="🔄 به‌روزرسانی موجودی", callback_data="bank:ui:refresh"),
                InlineKeyboardButton(text="🚪 بستن", callback_data="btq:close"),
            ],
        ]
    )


async def _render_bank_view(player: Player) -> tuple[str, InlineKeyboardMarkup]:
    from services import game
    creds, _ = await economy.balances(player.user_id)
    fresh = await game.load_player(player.user_id) or player
    loan_info = (
        f"بدهی فعال: <b>{fresh.loan_amount:,}</b> رکسار"
        if fresh.loan_amount > 0
        else "بدون بدهی معوق ✅"
    )
    text = (
        "🏦 <b>بانک مرکزی شهر تیرامیکس (سیستم امن ضدسرقت)</b>\n\n"
        f"💵 موجودی نقد در جیب: <b>{creds:,}</b> رکسار\n"
        f"🔒 موجودی در گاوصندوق بانک: <b>{fresh.bank_balance:,}</b> رکسار\n"
        f"📊 وضعیت تسهیلات: {loan_info}\n\n"
        "📌 <b>عملیات سریع بانکی:</b>\n"
        "برای واریز، برداشت، وام یا انتقال وجه از دکمه‌های زیر استفاده کنید،\n"
        "یا دستورات متنی را بنویسید (مثال: <code>بانک واریز 500</code>)."
    )
    return text, _bank_markup()


@router.message(CommandOrText(["bank"], {"بانک", "وام", "سپرده", "صندوق"}))
async def cmd_bank(message: Message, command: CommandObject | None = None) -> None:
    """خدمات مالی بانک مرکزی شامل واریز به گاوصندوق ضدسرقت، برداشت و دریافت وام."""
    user = message.from_user
    if user is None:
        return
    try:
        player = await hydrate(user.id, user.full_name, user.username)
        from services import tiramix

        args = extract_args(message, command)

        if not args:
            text, markup = await _render_bank_view(player)
            await message.reply(text, reply_markup=markup)
            return

        action = args[0].lower()
        if len(args) < 2 or not args[1].isdigit():
            await message.reply(
                "⚠️ لطفاً مبلغ را به عدد وارد کنید.\nمثال: <code>بانک واریز 500</code>"
            )
            return

        amount = int(args[1])
        if action in ("واریز", "deposit"):
            res = await tiramix.deposit_bank(player, amount)
            await message.reply(res["message"])
        elif action in ("برداشت", "withdraw"):
            res = await tiramix.withdraw_bank(player, amount)
            await message.reply(res["message"])
        elif action in ("وام", "loan"):
            res = await tiramix.apply_bank_loan(player, amount)
            await message.reply(res["message"])
        elif action in ("تسویه", "repay"):
            res = await tiramix.repay_bank_loan(player, amount)
            await message.reply(res["message"])
        else:
            await message.reply("⚠️ عملیات نامعتبر. گزینه‌ها: واریز، برداشت، وام، تسویه.")
    except Exception as exc:  # noqa: BLE001
        await answer_error(exc, message=message)


@router.callback_query(F.data.in_({"bank:ui:home", "bank:ui:refresh"}))
async def cb_bank_home(call: CallbackQuery) -> None:
    user = call.from_user
    if user is None:
        return
    player = await hydrate(user.id, user.full_name, user.username)
    text, markup = await _render_bank_view(player)
    msg = editable_message(call)
    if msg:
        with contextlib.suppress(Exception):
            await msg.edit_text(text, reply_markup=markup)
    await call.answer("🔄 اطلاعات بانک به‌روز شد.")


@router.callback_query(F.data == "bank:ui:deposit")
async def cb_bank_deposit_menu(call: CallbackQuery) -> None:
    user = call.from_user
    if user is None:
        return
    player = await hydrate(user.id, user.full_name, user.username)
    creds, _ = await economy.balances(player.user_id)
    text = (
        "📥 <b>واریز به گاوصندوق امن بانک مرکزی:</b>\n\n"
        f"💵 موجودی نقد شما: <b>{creds:,}</b> رکسار\n\n"
        "مبلغ مورد نظر برای واریز را انتخاب کنید یا بنویسید:\n"
        "<code>بانک واریز [مبلغ]</code>"
    )
    markup = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📥 500", callback_data="bank:act:dep:500"),
                InlineKeyboardButton(text="📥 1,000", callback_data="bank:act:dep:1000"),
                InlineKeyboardButton(text="📥 5,000", callback_data="bank:act:dep:5000"),
            ],
            [
                InlineKeyboardButton(
                    text="📥 واریز کل موجودی نقد", callback_data=f"bank:act:dep:{creds}"
                ),
            ],
            [
                InlineKeyboardButton(text="🔙 بازگشت به بانک", callback_data="bank:ui:home"),
            ],
        ]
    )
    msg = editable_message(call)
    if msg:
        await msg.edit_text(text, reply_markup=markup)


@router.callback_query(F.data == "bank:ui:withdraw")
async def cb_bank_withdraw_menu(call: CallbackQuery) -> None:
    user = call.from_user
    if user is None:
        return
    from services import game
    player = await hydrate(user.id, user.full_name, user.username)
    fresh = await game.load_player(player.user_id) or player
    bank_bal = fresh.bank_balance
    text = (
        "📤 <b>برداشت وجه از گاوصندوق بانک:</b>\n\n"
        f"🔒 موجودی حساب بانکی: <b>{bank_bal:,}</b> رکسار\n\n"
        "مبلغ مورد نظر برای برداشت را انتخاب کنید یا بنویسید:\n"
        "<code>بانک برداشت [مبلغ]</code>"
    )
    markup = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📤 500", callback_data="bank:act:wdr:500"),
                InlineKeyboardButton(text="📤 1,000", callback_data="bank:act:wdr:1000"),
                InlineKeyboardButton(text="📤 5,000", callback_data="bank:act:wdr:5000"),
            ],
            [
                InlineKeyboardButton(
                    text="📤 برداشت کل موجودی بانک", callback_data=f"bank:act:wdr:{bank_bal}"
                ),
            ],
            [
                InlineKeyboardButton(text="🔙 بازگشت به بانک", callback_data="bank:ui:home"),
            ],
        ]
    )
    msg = editable_message(call)
    if msg:
        await msg.edit_text(text, reply_markup=markup)


@router.callback_query(F.data == "bank:ui:loan")
async def cb_bank_loan_menu(call: CallbackQuery) -> None:
    user = call.from_user
    if user is None:
        return
    from services import game
    player = await hydrate(user.id, user.full_name, user.username)
    fresh = await game.load_player(player.user_id) or player
    credit_limit = 1000 + (fresh.bank_balance * 2) + (fresh.level * 500)
    text = (
        "💳 <b>میز تسهیلات و وام بانک مرکزی تیرامیکس:</b>\n\n"
        f"📊 سقف مجاز وام برای شما: <b>{credit_limit:,}</b> رکسار\n"
        f"📈 نرخ بهره بانکی: <b>۱۵٪</b> (مهلت بازپرداخت: ۳ روز کاری)\n\n"
        "یک بسته وام را انتخاب کنید یا دستور زیر را بنویسید:\n"
        "<code>بانک وام [مبلغ]</code>"
    )
    markup = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="💳 وام 1,000", callback_data="bank:act:loan:1000"),
                InlineKeyboardButton(text="💳 وام 5,000", callback_data="bank:act:loan:5000"),
            ],
            [
                InlineKeyboardButton(
                    text=f"💳 سقف مجاز ({credit_limit:,} رکسار)",
                    callback_data=f"bank:act:loan:{credit_limit}",
                ),
            ],
            [
                InlineKeyboardButton(text="🔙 بازگشت به بانک", callback_data="bank:ui:home"),
            ],
        ]
    )
    msg = editable_message(call)
    if msg:
        await msg.edit_text(text, reply_markup=markup)


@router.callback_query(F.data == "bank:ui:repay")
async def cb_bank_repay_menu(call: CallbackQuery) -> None:
    user = call.from_user
    if user is None:
        return
    from services import game
    player = await hydrate(user.id, user.full_name, user.username)
    fresh = await game.load_player(player.user_id) or player
    due = fresh.loan_amount
    text = (
        "🧾 <b>تسویه بدهی و اقساط وام بانکی:</b>\n\n"
        f"⚠️ بدهی معوق شما: <b>{due:,}</b> رکسار\n\n"
        "مبلغ بازپرداخت را انتخاب کنید یا بنویسید:\n"
        "<code>بانک تسویه [مبلغ]</code>"
    )
    markup = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🧾 پرداخت 1,000", callback_data="bank:act:repay:1000"),
                InlineKeyboardButton(
                    text=f"🧾 تسویه کامل بدهی ({due:,})", callback_data=f"bank:act:repay:{due}"
                ),
            ],
            [
                InlineKeyboardButton(text="🔙 بازگشت به بانک", callback_data="bank:ui:home"),
            ],
        ]
    )
    msg = editable_message(call)
    if msg:
        await msg.edit_text(text, reply_markup=markup)


@router.callback_query(F.data == "bank:ui:transfer")
async def cb_bank_transfer_menu(call: CallbackQuery) -> None:
    text = (
        "💸 <b>انتقال رکسار (کارت به کارت بین شهروندان)</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "برای جا به جایی و انتقال رکسار به یک کاربر دیگر، می‌توانید:\n\n"
        "۱. <b>روش سریع گروهی:</b> روی پیام کاربر در گروه ریپلای بزنید و بنویسید:\n"
        "<code>انتقال 500</code>\n"
        "یا: <code>کارت به کارت 1000</code>\n\n"
        "۲. <b>روش نام کاربری یا شناسه عددی:</b>\n"
        "<code>انتقال 500 @username</code>\n"
        "<code>انتقال 500 123456789</code>\n\n"
        "✨ بدون کارمزد و با تسویه آنی در بانک مرکزی تیرامیکس!"
    )
    markup = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🔙 بازگشت به بانک", callback_data="bank:ui:home"),
            ],
        ]
    )
    msg = editable_message(call)
    if msg:
        await msg.edit_text(text, reply_markup=markup)


@router.callback_query(F.data.startswith("bank:act:"))
async def cb_bank_action(call: CallbackQuery) -> None:
    user = call.from_user
    if user is None or call.data is None:
        return
    parts = call.data.split(":")
    if len(parts) < 4:
        return
    act_kind, amt_str = parts[2], parts[3]
    if not amt_str.isdigit():
        return
    amount = int(amt_str)
    if amount <= 0:
        await call.answer("مبلغ نامعتبر یا صفر است.", show_alert=True)
        return

    from services import tiramix
    player = await hydrate(user.id, user.full_name, user.username)

    if act_kind == "dep":
        res = await tiramix.deposit_bank(player, amount)
    elif act_kind == "wdr":
        res = await tiramix.withdraw_bank(player, amount)
    elif act_kind == "loan":
        res = await tiramix.apply_bank_loan(player, amount)
    elif act_kind == "repay":
        res = await tiramix.repay_bank_loan(player, amount)
    else:
        return

    # پاک‌سازی تگ‌های HTML برای نمایش آلرت
    alert_txt = (
        res["message"]
        .replace("<b>", "")
        .replace("</b>", "")
        .replace("<code>", "")
        .replace("</code>", "")
    )
    await call.answer(alert_txt[:200], show_alert=not res.get("success", False))

    text, markup = await _render_bank_view(player)
    msg = editable_message(call)
    if msg:
        with contextlib.suppress(Exception):
            await msg.edit_text(text, reply_markup=markup)


@router.message(
    CommandOrText(
        ["transfer", "pay", "sendmoney"],
        {"انتقال", "کارت به کارت", "واریز به", "انتقال وجه"},
    )
)
async def cmd_transfer(message: Message, command: CommandObject | None = None) -> None:
    """انتقال مستقیم رکسار بین دو شهروند (کارت به کارت)."""
    user = message.from_user
    if user is None:
        return
    try:
        from handlers.common import resolve_target_player
        from services import tiramix

        player = await hydrate(user.id, user.full_name, user.username)
        args = extract_args(message, command)
        target_player = None
        amount = None

        if message.reply_to_message and message.reply_to_message.from_user:
            target_user = message.reply_to_message.from_user
            target_player = await hydrate(
                target_user.id, target_user.full_name, target_user.username
            )
            if args and args[0].isdigit():
                amount = int(args[0])
        else:
            if len(args) >= 2:
                if args[0].isdigit():
                    amount = int(args[0])
                    token = args[1]
                elif args[1].isdigit():
                    amount = int(args[1])
                    token = args[0]
                else:
                    token = ""
                if token:
                    target_player = await resolve_target_player(message, token)

        if amount is None or target_player is None:
            await message.reply(
                "📌 <b>نحوه انتقال رکسار (کارت به کارت):</b>\n\n"
                "۱. ریپلای روی پیام شخص و ارسال مبلغ:\n"
                "<code>انتقال 500</code>\n\n"
                "۲. یا با نوشتن یوزرنیم یا شناسه عددی:\n"
                "<code>انتقال 500 @username</code>\n"
                "<code>انتقال 500 123456789</code>"
            )
            return

        res = await tiramix.transfer_credits(player.user_id, target_player.user_id, amount)
        await message.reply(res["message"])
    except Exception as exc:  # noqa: BLE001
        await answer_error(exc, message=message)


# ---------------------------------------------------------------------------
# سرقت سازمان‌یافته (/heist)
# ---------------------------------------------------------------------------


@router.message(CommandOrText(["heist", "rob"], prefix_words=("سرقت", "heist")))
async def cmd_heist(message: Message, command: CommandObject | None = None) -> None:
    """عملیات سرقت پرریسک با پول پیش‌پرداخت، با شانس برد تقویت‌شده بر اساس استایل."""
    user = message.from_user
    if user is None:
        return
    try:
        economy.require_ready(user.id, "heist", settings.cooldown_heist)
        player = await hydrate(user.id, user.full_name, user.username)

        args = extract_args(message, command)
        if not args or not args[0].isdigit():
            raise GameError(
                f"نحوه استفاده: <code>سرقت [مبلغ]</code> یا <code>/heist [مبلغ]</code>\n"
                f"مبلغ بین {settings.heist_stake_min:,} تا "
                f"{settings.heist_stake_max:,} رکسار.\n"
                f"مبلغ همان چیزی است که می‌خواهی بدزدی (لازم نیست داشته باشی!)؛ "
                f"هرچه بزرگ‌تر باشد شانس موفقیت کمتر و احتمال دستگیری بیشتر می‌شود.\n"
                f"استایل (DRIP) شانس موفقیت را افزایش می‌دهد."
            )
        stake = int(args[0])
        result = await economy.do_heist(player.user_id, stake, player.drip)
        await message.reply(_activity_card(result))
    except Exception as exc:  # noqa: BLE001
        await answer_error(exc, message=message)


# ---------------------------------------------------------------------------
# کازینوی مینی‌گیم‌ها (تاس و شیر یا خط)
# ---------------------------------------------------------------------------


@router.message(CommandOrText(["dice"], prefix_words=("تاس", "dice")))
async def cmd_dice(message: Message, command: CommandObject | None = None) -> None:
    """شرط‌بندی روی مجموع تاس‌های دوتایی (بالا ۸+ یا پایین ۶-)."""
    await _casino_command(message, command, game="dice")


@router.message(
    CommandOrText(["coinflip", "flip"], prefix_words=("سکه", "شیرخط", "شیر یا خط", "coinflip"))
)
async def cmd_coinflip(message: Message, command: CommandObject | None = None) -> None:
    """شرط‌بندی روی شیر یا خط سکه ۵۰/۵۰."""
    await _casino_command(message, command, game="coinflip")


async def _casino_command(message: Message, command: CommandObject | None, game: str) -> None:
    """پردازش متمرکز بازی‌های شرط‌بندی سبک با تعهد فوری تراکنش به لجر مالی."""
    user = message.from_user
    if user is None:
        return
    try:
        economy.require_ready(user.id, "casino", settings.cooldown_casino)
        player = await hydrate(user.id, user.full_name, user.username)

        args = extract_args(message, command)
        if game == "dice":
            pick_raw = args[1].lower() if len(args) > 1 else ""
            if pick_raw in ("high", "بالا", "بزرگ"):
                pick = "high"
            elif pick_raw in ("low", "پایین", "کوچک"):
                pick = "low"
            else:
                pick = pick_raw

            if len(args) < 2 or not args[0].isdigit() or pick not in ("high", "low"):
                raise GameError(
                    "نحوه استفاده: <code>تاس [شرط] بالا|پایین</code> یا "
                    "<code>/dice [bet] high|low</code>\n"
                    "بالا (۸ به بالا) · پایین (۶ به پایین) — مجموع ۷ همیشه بازنده است.\n"
                    f"مبلغ شرط: {settings.casino_min_bet:,} تا {settings.casino_max_bet:,} سکه."
                )
            bet = economy.validate_bet(int(args[0]))
            result = await economy.casino_dice(player.user_id, bet, pick)
        else:
            pick_raw = args[1].lower() if len(args) > 1 else ""
            if pick_raw in ("heads", "شیر"):
                pick = "heads"
            elif pick_raw in ("tails", "خط"):
                pick = "tails"
            else:
                pick = pick_raw

            if len(args) < 2 or not args[0].isdigit() or pick not in ("heads", "tails"):
                raise GameError(
                    "نحوه استفاده: <code>سکه [شرط] شیر|خط</code> یا "
                    "<code>/coinflip [bet] heads|tails</code>\n"
                    f"مبلغ شرط: {settings.casino_min_bet:,} تا {settings.casino_max_bet:,} سکه."
                )
            bet = economy.validate_bet(int(args[0]))
            result = await economy.casino_coinflip(player.user_id, bet, pick)

        await message.reply(_activity_card(result))
    except Exception as exc:  # noqa: BLE001
        await answer_error(exc, message=message)


# ---------------------------------------------------------------------------
# کیف پول و کارنامه مشخصات (/balance, /stats)
# ---------------------------------------------------------------------------


@router.message(CommandOrText(["balance", "bal", "wallet", "money"], BAL_WORDS))
async def cmd_balance(message: Message) -> None:
    """مشاهده موجودی نقد، حساب بانکی و وضعیت شغلی شهروند."""
    user = message.from_user
    if user is None:
        return
    try:
        player = await hydrate(user.id, user.full_name, user.username)
        credits, _ = await economy.balances(player.user_id)
        loan_line = (
            f"\n⚠️ بدهی وام بانکی: <b>{player.loan_amount:,}</b> رکسار"
            if player.loan_amount > 0
            else ""
        )
        await message.reply(
            f"💰 <b>وضعیت مالی شهروندی {esc(player.display_tag)}</b>\n\n"
            f"💵 موجودی نقد (کیف پول): <b>{credits:,}</b> رکسار\n"
            f"🏦 سپرده امن بانکی: <b>{player.bank_balance:,}</b> رکسار{loan_line}\n\n"
            f"💼 شغل فعلی: <b>{player.job}</b>\n"
            f"🎓 مدرک تحصیلی: <b>{player.education_title}</b>"
        )
    except Exception as exc:  # noqa: BLE001
        await answer_error(exc, message=message)


@router.message(CommandOrText(["stats", "level"], STATS_WORDS))
async def cmd_stats(message: Message) -> None:
    """نمایش مشخصات فیزیکی، نوار انرژی، قدرت، دفاع، استایل و پیشرفت لول."""
    user = message.from_user
    if user is None:
        return
    try:
        player = await hydrate(user.id, user.full_name, user.username)
        stats = player.stats
        if player.energy > 100:
            energy_text = f"<b>{player.energy}⚡️</b> (سوپرشارژ 🔥)"
            regen_tag = "(تکمیل)"
        elif player.energy == 100:
            energy_text = "<b>100/100</b>"
            regen_tag = "(تکمیل)"
        else:
            energy_text = f"<b>{player.energy}/100</b>"
            regen_tag = "(+۱⚡ هر ۷.۲ دقیقه)"

        await message.reply(
            f"📊 <b>مشخصات {esc(player.display_tag)}</b> — لول <b>{player.level}</b>\n"
            f"⚡ {energy_bar(player.energy, stats.max_energy)} "
            f"{energy_text} {regen_tag}\n"
            f"⚔️ قدرت: <b>{player.atk}</b> · 🛡 دفاع: <b>{player.defense}</b> · "
            f"💎 استایل: <b>{player.drip}</b>\n"
            f"✨ پیشرفت: <b>{player.exp}/{exp_to_next(player.level)}</b> EXP تا لول بعدی"
        )
    except Exception as exc:  # noqa: BLE001
        await answer_error(exc, message=message)


# ---------------------------------------------------------------------------
# کلیدهای اقدام سریع متصل به شناسنامه
# ---------------------------------------------------------------------------


@router.callback_query(F.data == "act:work")
async def cb_work(call: CallbackQuery) -> None:
    """کلیک دکمه سریع کار در زیر کارت پروفایل یا منوی غذا."""
    user = call.from_user
    if user is None:
        return
    try:
        from services import tiramix

        player = await hydrate(user.id, user.full_name, user.username)
        res = await tiramix.execute_work(player)
        alert_text = (
            res["message"]
            .replace("<b>", "")
            .replace("</b>", "")
            .replace("<code>", "")
            .replace("</code>", "")
        )
        await call.answer(alert_text[:200], show_alert=True)
    except Exception as exc:  # noqa: BLE001
        await answer_error(exc, callback=call)


@router.callback_query(F.data == "act:daily")
async def cb_daily(call: CallbackQuery) -> None:
    """کلیک دکمه سریع دریافت جایزه روزانه زیر کارت پروفایل."""
    user = call.from_user
    if user is None:
        return
    try:
        economy.require_ready(user.id, "daily", settings.cooldown_daily)
        player = await hydrate(user.id, user.full_name, user.username)
        result = await economy.claim_daily(player.user_id, player.drip)
        if result.success and result.exp_gained:
            from services.game import grant_exp

            await grant_exp(player.user_id, result.exp_gained)
        await call.answer(f"{result.headline}: {result.detail}", show_alert=True)
    except Exception as exc:  # noqa: BLE001
        await answer_error(exc, callback=call)


def _help_markup() -> InlineKeyboardMarkup:
    """چیدمان دکمه‌های شیشه‌ای صفحه اصلی راهنمای بازی."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📜 لیست تمام دستورات", callback_data="help:cmds"),
                InlineKeyboardButton(text="⚔️ راهنمای دوئل و دزدی", callback_data="help:combat"),
            ],
            [
                InlineKeyboardButton(text="🛍 فروشگاه و بوتیک مد", callback_data="btq:open"),
                InlineKeyboardButton(text="💼 کار کردن", callback_data="act:work"),
            ],
            [
                InlineKeyboardButton(text="🍔 رستوران و سفارش غذا", callback_data="food:menu"),
                InlineKeyboardButton(text="👤 شناسنامه من", callback_data="act:me"),
            ],
            [
                InlineKeyboardButton(text="🚪 بستن", callback_data="btq:close"),
            ],
        ]
    )


_HELP_MAIN_TEXT = (
    "🍁 <b>راهنمای جامع شهر و جهان تیرامیکس (Urban Fantasy RPG)</b>\n\n"
    "تیرامیکس یک جامعه شبیه‌ساز زندگی و نقش‌آفرینی اجتماعی است! در این شهر می‌توانید "
    "کار کنید، تحصیل کنید، درآمد داشته باشید، در بانک سرمایه‌گذاری کرده یا وام بگیرید، "
    "چهره و تیپ خود را شخصی‌سازی کنید، با اعضا ازدواج کرده و خانواده تشکیل دهید، "
    "تشکیل کلن دهید و حتی ریسک دزدی خیابانی و دوئل را بپذیرید!\n\n"
    "🏛 <b>سیستم‌های اصلی شهر:</b>\n"
    "• <b>آموزش و شغل:</b> با ارتقای مدرک تحصیلی، شغل‌های پردرآمدتر در اداره کار باز می‌شوند.\n"
    "• <b>بانک و اقتصاد:</b> نگهداری پول در گاوصندوق امن ضدسرقت، دریافت تسهیلات و تسویه اقساط.\n"
    "• <b>بوتیک و مد:</b> ویترین روزانه تک‌آیتم‌ها (بالاتنه، پایین‌تنه، کفش) بدون مصرف دیتای عکس.\n"
    "• <b>خانواده و جامعه:</b> ازدواج، فرزندآوری، طلاق با قاضی تصادفی از اعضای گروه، و کلن‌ها.\n"
    "• <b>قانون و ریسک:</b> دزدی از سایر شهروندان با ریسک حبس در بازداشتگاه یا جریمه ۵ برابری.\n\n"
    "👇 <i>از دکمه‌های زیر برای دسترسی سریع یا مشاهده لیست تمام دستورات استفاده کنید:</i>"
)


_HELP_CMDS_TEXT = (
    "📜 <b>فهرست کامل دستورات شهر تیرامیکس:</b>\n"
    "━━━━━━━━━━━━━━━━━━━━\n\n"
    "👤 <b>هویت و ظاهر:</b>\n"
    "• <code>پروفایل</code> / <code>/profile</code> — شناسنامه رسمی و کارت گرافیکی\n"
    "• <code>وضعیت</code> / <code>/stats</code> — لول، انرژی، سلامت، اتک، دفاع و استایل\n"
    "• <code>شاپ</code> / <code>/shop</code> — فروشگاه مد و لباس (مینی‌اپ)\n"
    "• <code>بوتیک</code> / <code>/boutique</code> — ویترین لباس با دکمه‌های شیشه‌ای\n"
    "• <code>کمد</code> / <code>/wardrobe</code> — مشاهده و پوشیدن لباس‌های خریداری‌شده\n"
    "• <code>آواتار</code> / <code>/avatar</code> — استایل شخصی، کمد و میانبرهای چهره\n"
    "• <code>/customizer</code> — مینی‌اپ کاتالوگ و شخصی‌سازی کاراکتر\n"
    "• <code>آرایشگاه</code> — مدل مو و رژ لب\n\n"
    "💼 <b>اقتصاد، شغل و دارایی:</b>\n"
    "• <code>کار</code> / <code>/work</code> — شیفت کاری و دریافت درآمد\n"
    "• <code>تحصیل</code> / <code>/study</code> — ارتقای مدرک تحصیلی دانشگاهی\n"
    "• <code>شغل</code> / <code>/job</code> — مشاهده و استخدام در مشاغل\n"
    "• <code>کیف پول</code> / <code>موجودی</code> — موجودی نقد و حساب بانکی\n"
    "• <code>بانک</code> / <code>/bank</code> — واریز، برداشت، تسهیلات و کارت به کارت\n"
    "• <code>انتقال [مبلغ]</code> (ریپلای/آیدی) — کارت به کارت بین شهروندان\n"
    "• <code>روزانه</code> / <code>/daily</code> — پاداش وفاداری ۲۴ ساعته\n"
    "• <code>غذا</code> / <code>/food</code> — رستوران و شارژ فوری انرژی با خوراکی\n"
    "• <code>املاک</code> / <code>/property</code> — خرید و درآمد منفعل املاک\n"
    "• <code>بورس</code> / <code>/exchange</code> — بازار ارز دیجیتال و سهام\n"
    "• <code>قرارداد</code> / <code>/contract</code> — ماموریت‌های روزانه با جایزه\n"
    "• <code>بازارچه</code> / <code>/bazaar</code> — دادوستد مستقیم با سایر بازیکنان\n"
    "• <code>قرعه</code> / <code>/lottery</code> — لاتاری | <code>هدیه</code> — اهدای پول\n"
    "• <code>سپر خرید</code> / <code>/shield</code> — بیمه ضدسرقت اموال\n\n"
    "💍 <b>روابط، خانواده و سندیکا:</b>\n"
    "• <code>ازدواج</code> (ریپلای/آیدی) — پیشنهاد پیوند مشترک\n"
    "• <code>طلاق</code> — جدایی توافقی یا دادگاه با قاضی تصادفی\n"
    "• <code>رابطه</code> / <code>سکس</code> — عشق‌بازی با همسر و فرزندآوری\n"
    "• <code>خیانت</code> (ریپلای) — رابطه پنهانی با ریسک رسوایی\n"
    "• <code>کلینیک</code> / <code>/clinic</code> — زایمان و سلامت خانواده\n"
    "• <code>کلن</code> / <code>/clan</code> — سندیکا و خانواده‌های مافیایی\n\n"
    "⚔️ <b>دوئل، نبرد و دنیای زیرزمینی:</b>\n"
    "• <code>دوئل [مبلغ]</code> (ریپلای/آیدی) — نبرد خیابانی سر پول\n"
    "• <code>دزدی</code> (ریپلای/آیدی) — جیب‌بری با ریسک حبس و جریمه\n"
    "• <code>اخاذی</code> (ریپلای/آیدی) — باج‌گیری خیابانی از شهروندان\n"
    "• <code>سایه هکر</code> / <code>سایه قاتل</code> — اجیر کردن سایه‌های تاریک\n"
    "• <code>کاسب</code> / <code>/blackmarket</code> — بازار سیاه و ابزار سرقت\n"
    "• <code>سرقت بانک</code> / <code>/bankraid</code> — عملیات دستبرد به خزانه\n"
    "• <code>نوچه</code> / <code>/crew</code> — استخدام بادیگارد مسلح\n"
    "• <code>حیوان</code> / <code>/pet</code> — خرید و مبارزات پت‌های جنگی\n"
    "• <code>باس</code> / <code>/boss</code> — حمله گروهی به غول‌های شهر\n\n"
    "🎰 <b>کازینو و بازی‌های شانس:</b>\n"
    "• <code>کازینو [مبلغ]</code> — اسلات شانس ۳تایی\n"
    "• <code>ریسک [مبلغ]</code> — بازی انفجار با کش‌اوت دستی\n"
    "• <code>تاس [مبلغ] بالا|پایین</code> — مجموع جفت‌تاس\n"
    "• <code>سکه [مبلغ] شیر|خط</code> — شیر یا خط ۵۰/۵۰\n"
    "• <code>سنگ کاغذ قیچی [مبلغ]</code> — بازی RPS با بات\n"
    "• <code>حدس [مبلغ]</code> — حدس عدد | <code>گاوصندوق [مبلغ]</code> — رمزگشایی\n"
    "• <code>دوز</code> (ریپلای/آیدی) — دوز دونفره آنلاین\n"
    "• <code>دروازه</code> (ریپلای/آیدی) — پنالتی فوتبال دونفره\n"
    "• <code>کاپ</code> / <code>/cup</code> — تورنمنت حذفی قهرمانی\n"
    "━━━━━━━━━━━━━━━━━━━━\n"
    "💡 <i>در دستورات دونفره می‌توانید با ریپلای، آیدی عددی یا یوزرنیم با @ حریف را مشخص کنید.</i>"
)


_HELP_COMBAT_TEXT = (
    "⚔️ <b>راهنمای مبارزات، دوئل و جرایم شهری</b>\n\n"
    "🔥 <b>دوئل خیابانی:</b>\n"
    "روی پیام هر کاربری در گروه ریپلای بزنید یا آیدی او را بنویسید:\n"
    "<code>دوئل 100</code>\n"
    "مبلغ شرط در صندوق امانی قرار می‌گیرد و حریف ۱۰ دقیقه فرصت پذیرش دارد. "
    "برنده بر اساس لول، سطح سواد، قدرت و شانس مشخص شده و کل پات را دریافت می‌کند!\n\n"
    "🥷 <b>سرقت و جیب‌بری:</b>\n"
    "روی پیام شخص ریپلای بزنید و بنویسید <code>دزدی</code>!\n"
    "در صورت موفقیت بین ۳۵٪ تا ۸۵٪ موجودی نقدی قربانی را تصاحب می‌کنید. "
    "اما در صورت شکست، پلیس شهر شما را دستگیر کرده و به بازداشتگاه می‌برد "
    "یا جریمه نقدی معادل ۵ برابر مبلغ تلاش دزدی اعمال می‌شود!\n\n"
    "🚨 <b>باس‌های گروهی (Raids):</b>\n"
    "با فعالیت در گروه باس‌های غول‌پیکر ظاهر می‌شوند و پاداش حمله بر اساس دمیج تقسیم می‌شود."
)


@router.message(CommandOrText(["help"], HELP_WORDS))
async def cmd_help(message: Message) -> None:
    """ارسال منوی راهنمای جامع و کلیدهای میانبر تیرامیکس."""
    await message.reply(_HELP_MAIN_TEXT, reply_markup=_help_markup())


@router.callback_query(F.data.in_(("act:help", "help:main")))
async def cb_help_main(call: CallbackQuery) -> None:
    """نمایش صفحه اصلی منوی راهنما در کال‌بک شیشه‌ای."""
    await call.answer()
    message = call.message
    if isinstance(message, Message):
        try:
            if message.photo:
                await message.edit_caption(caption=_HELP_MAIN_TEXT, reply_markup=_help_markup())
            else:
                await message.edit_text(_HELP_MAIN_TEXT, reply_markup=_help_markup())
        except Exception:
            await message.answer(_HELP_MAIN_TEXT, reply_markup=_help_markup())


@router.callback_query(F.data == "help:cmds")
async def cb_help_cmds(call: CallbackQuery) -> None:
    """نمایش لیست تفصیلی تمام دستورات شهری تیرامیکس."""
    await call.answer()
    message = call.message
    if isinstance(message, Message):
        back_markup = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="🔙 بازگشت به راهنما", callback_data="help:main"),
                    InlineKeyboardButton(text="🛍 فروشگاه و بوتیک مد", callback_data="btq:open"),
                ],
                [
                    InlineKeyboardButton(text="👤 شناسنامه من", callback_data="act:me"),
                    InlineKeyboardButton(text="🚪 بستن", callback_data="btq:close"),
                ],
            ]
        )
        try:
            if message.photo:
                await message.answer(_HELP_CMDS_TEXT, reply_markup=back_markup)
            else:
                await message.edit_text(_HELP_CMDS_TEXT, reply_markup=back_markup)
        except Exception:
            await message.answer(_HELP_CMDS_TEXT, reply_markup=back_markup)


@router.callback_query(F.data == "help:combat")
async def cb_help_combat(call: CallbackQuery) -> None:
    """نمایش راهنمای مبارزات خیابانی، دوئل و دزدی تیرامیکس."""
    await call.answer()
    message = call.message
    if isinstance(message, Message):
        back_markup = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="🔙 بازگشت به راهنما", callback_data="help:main"),
                    InlineKeyboardButton(text="🛍 فروشگاه و بوتیک مد", callback_data="btq:open"),
                ],
                [
                    InlineKeyboardButton(text="👤 شناسنامه من", callback_data="act:me"),
                    InlineKeyboardButton(text="🚪 بستن", callback_data="btq:close"),
                ],
            ]
        )
        try:
            if message.photo:
                await message.edit_caption(caption=_HELP_COMBAT_TEXT, reply_markup=back_markup)
            else:
                await message.edit_text(_HELP_COMBAT_TEXT, reply_markup=back_markup)
        except Exception:
            await message.answer(_HELP_COMBAT_TEXT, reply_markup=back_markup)
