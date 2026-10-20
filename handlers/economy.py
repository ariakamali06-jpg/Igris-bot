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

import logging

from aiogram import F, Router
from aiogram.filters import CommandObject
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from config import settings
from handlers.common import (
    CommandOrText,
    answer_error,
    energy_bar,
    esc,
    extract_args,
    hydrate,
)
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
        bits.append(f"{sign}{result.credits_delta:,} سکه")
    if result.shards_delta:
        sign = "+" if result.shards_delta > 0 else ""
        bits.append(f"{sign}{result.shards_delta} شارد")
    if result.exp_gained:
        bits.append(f"+{result.exp_gained} EXP")
    if bits:
        lines.append(" · ".join(bits))
    return "\n".join(lines)


DAILY_WORDS = {"روزانه", "جایزه", "پاداش", "حقوق", "daily", "claim"}
WORK_WORDS = {"کار", "شغل", "شیفت", "work", "shift"}
BAL_WORDS = {"موجودی", "پول", "سکه", "کیف", "کیف پول", "حساب", "balance", "bal", "wallet", "money"}
STATS_WORDS = {"آمار", "استاتس", "لول", "وضعیت", "stats", "level"}
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


@router.message(CommandOrText(["study", "edu"], {"تحصیل", "درس", "دانشگاه", "مدرک", "کلاس"}))
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

        unlocked_jobs = tiramix.list_available_jobs(player.education_level)
        lines = [
            "💼 <b>اداره کار و استخدام مرکزی شهر تیرامیکس</b>\n",
            f"👤 شغل فعلی شما: <b>{player.job}</b>",
            f"🎓 مدرک تحصیلی: <b>{player.education_title}</b>\n",
            "📋 <b>فرصت‌های شغلی متناسب با مدرک شما:</b>",
        ]
        for j in unlocked_jobs:
            lines.append(
                f"• <b>{j['title']}</b>: ساعتی <b>{j['wage']:,}</b> سکه\n  <i>{j['desc']}</i>"
            )

        lines.append("\n💡 <i>برای استخدام در یک شغل، نام آن را بنویسید:</i>")
        lines.append("<code>شغل [نام شغل]</code>")
        lines.append("<i>مثال: شغل برنامه‌نویس جونیور</i>")

        await message.reply("\n".join(lines))
    except Exception as exc:  # noqa: BLE001
        await answer_error(exc, message=message)


# ---------------------------------------------------------------------------
# بانک مرکزی، گاوصندوق و وام (/bank)
# ---------------------------------------------------------------------------


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
        creds, _ = await economy.balances(player.user_id)

        if not args:
            loan_info = (
                f"بدهی فعال: <b>{player.loan_amount:,}</b> سکه"
                if player.loan_amount > 0
                else "بدون بدهی معوق ✅"
            )
            text = (
                "🏦 <b>بانک مرکزی شهر تیرامیکس (سیستم امن ضدسرقت)</b>\n\n"
                f"💵 موجودی نقد در جیب: <b>{creds:,}</b> سکه\n"
                f"🔒 موجودی در گاوصندوق بانک: <b>{player.bank_balance:,}</b> سکه\n"
                f"📊 وضعیت تسهیلات: {loan_info}\n\n"
                "📌 <b>دستورات بانکی:</b>\n"
                "• <code>بانک واریز [مبلغ]</code> — انتقال پول نقد به گاوصندوق امن\n"
                "• <code>بانک برداشت [مبلغ]</code> — برداشت وجه نقد به کیف پول\n"
                "• <code>بانک وام [مبلغ]</code> — دریافت تسهیلات با بهره ۱۵٪\n"
                "• <code>بانک تسویه [مبلغ]</code> — پرداخت اقساط و بدهی وام"
            )
            await message.reply(text)
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
                f"حداقل {settings.heist_stake_min:,} و حداکثر {settings.heist_stake_max:,} سکه.\n"
                f"استایل (DRIP) شانس موفقیت را افزایش می‌دهد!"
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
    """مشاهده موجودی نقد، حساب بانکی، شارد و وضعیت شغلی شهروند."""
    user = message.from_user
    if user is None:
        return
    try:
        player = await hydrate(user.id, user.full_name, user.username)
        credits, shards = await economy.balances(player.user_id)
        loan_line = (
            f"\n⚠️ بدهی وام بانکی: <b>{player.loan_amount:,}</b> سکه"
            if player.loan_amount > 0
            else ""
        )
        await message.reply(
            f"💰 <b>وضعیت مالی شهروندی {esc(player.display_tag)}</b>\n\n"
            f"💵 موجودی نقد (کیف پول): <b>{credits:,}</b> سکه\n"
            f"🏦 سپرده امن بانکی: <b>{player.bank_balance:,}</b> سکه\n"
            f"💎 شارد روح: <b>{shards}</b> عدد{loan_line}\n\n"
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
        await message.reply(
            f"📊 <b>مشخصات {esc(player.display_tag)}</b> — لول <b>{player.level}</b>\n"
            f"⚡ {energy_bar(player.energy, stats.max_energy)} "
            f"{player.energy}/{stats.max_energy} "
            f"(+{settings.energy_regen_per_minute:g}/دقیقه)\n"
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
    """کلیک دکمه سریع کار در زیر کارت پروفایل."""
    user = call.from_user
    if user is None:
        return
    try:
        economy.require_ready(user.id, "work", settings.cooldown_work)
        player = await hydrate(user.id, user.full_name, user.username)
        result = await economy.do_work(player.user_id, player.level, player.drip)
        await call.answer(
            f"{result.headline}: {result.detail}", show_alert=True
        )
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
                InlineKeyboardButton(text="👤 شناسنامه من", callback_data="act:me"),
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
    "• <b>بوتیک و مد:</b> ویترین روزانه ست‌های اوراسکایز، پرو زنده و کاتالوگ مینی‌اپ.\n"
    "• <b>خانواده و جامعه:</b> ازدواج، فرزندآوری، طلاق با قاضی تصادفی از اعضای گروه، و کلن‌ها.\n"
    "• <b>قانون و ریسک:</b> دزدی از سایر شهروندان با ریسک حبس در بازداشتگاه یا جریمه ۵ برابری.\n\n"
    "👇 <i>از دکمه‌های زیر برای دسترسی سریع یا مشاهده لیست تمام دستورات استفاده کنید:</i>"
)


_HELP_CMDS_TEXT = (
    "📜 <b>فهرست کامل دستورات شهر تیرامیکس:</b>\n"
    "━━━━━━━━━━━━━━━━━━━━\n\n"
    "👤 <b>هویت و ظاهر:</b>\n"
    "• <code>پروفایل</code> / <code>/me</code> — شناسنامه رسمی و کارت گرافیکی\n"
    "• <code>آمار</code> / <code>/stats</code> — لول، انرژی، سلامت، اتک، دفاع و استایل\n"
    "• <code>بوتیک</code> / <code>/boutique</code> — ویترین روزانه ست‌های لباس و خرید\n"
    "• <code>کمد</code> / <code>/wardrobe</code> — مشاهده و پوشیدن لباس‌های خریداری‌شده\n"
    "• <code>آواتار</code> / <code>/avatar</code> — استایل شخصی، کمد و میانبرهای چهره\n"
    "• <code>/customizer</code> — مینی‌اپ کاتالوگ و شخصی‌سازی کاراکتر\n"
    "• <code>آرایشگاه</code> — مدل و رنگ مو | <code>زیبایی</code> — جراحی چهره\n"
    "• <code>سولاریوم</code> — برنزه‌سازی پوست | <code>کلینیک</code> — درمانگاه شهر\n\n"
    "💼 <b>اقتصاد، شغل و دارایی:</b>\n"
    "• <code>کار</code> / <code>/work</code> — شیفت کاری و دریافت درآمد\n"
    "• <code>تحصیل</code> / <code>/study</code> — ارتقای مدرک تحصیلی دانشگاهی\n"
    "• <code>شغل</code> / <code>/job</code> — مشاهده و استخدام در مشاغل\n"
    "• <code>کیف پول</code> / <code>موجودی</code> — موجودی نقد، بانک و شارد روح\n"
    "• <code>بانک</code> / <code>/bank</code> — واریز، برداشت و دریافت وام\n"
    "• <code>روزانه</code> / <code>/daily</code> — پاداش وفاداری ۲۴ ساعته\n"
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
