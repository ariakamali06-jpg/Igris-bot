"""/daily, /work, /heist and the mini-casino (dice, coinflip).

Each command follows the same shape: cooldown gate → domain call → one
rendered reply.  Bets are parsed from the command args with hard clamps from
config, and every outcome (win or loss) already committed its ledger row
before we format the message, so chat text can never disagree with balances.
"""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from config import settings
from handlers.common import CommandOrText, answer_error, energy_bar, esc, hydrate
from services import economy
from services.game import GameError, exp_to_next

logger = logging.getLogger(__name__)
router = Router(name="economy")


def _activity_card(result: economy.ActivityResult) -> str:
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


def _extract_args(message: Message, command: CommandObject | None = None) -> list[str]:
    if command and command.args:
        return list(command.args.split())
    text = (message.text or "").strip()
    parts = text.split()
    return list(parts[1:]) if len(parts) > 1 else []


DAILY_WORDS = {"روزانه", "جایزه", "پاداش", "حقوق", "daily", "claim"}
WORK_WORDS = {"کار", "شغل", "شیفت", "work", "shift"}
BAL_WORDS = {"موجودی", "پول", "سکه", "کیف", "کیف پول", "حساب", "balance", "bal", "wallet", "money"}
STATS_WORDS = {"آمار", "استاتس", "لول", "وضعیت", "stats", "level"}
HELP_WORDS = {"راهنما", "کمک", "آموزش", "اموزش", "دستورات", "help"}


# ---------------------------------------------------------------------------
# /daily
# ---------------------------------------------------------------------------


@router.message(CommandOrText(["daily", "claim"], DAILY_WORDS))
async def cmd_daily(message: Message) -> None:
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
# /work
# ---------------------------------------------------------------------------


@router.message(CommandOrText(["work", "shift"], WORK_WORDS))
async def cmd_work(message: Message) -> None:
    user = message.from_user
    if user is None:
        return
    try:
        economy.require_ready(user.id, "work", settings.cooldown_work)
        player = await hydrate(user.id, user.full_name, user.username)
        result = await economy.do_work(
            player.user_id, player.level, player.drip
        )
        await message.reply(_activity_card(result))
    except Exception as exc:  # noqa: BLE001
        await answer_error(exc, message=message)


# ---------------------------------------------------------------------------
# /heist
# ---------------------------------------------------------------------------


@router.message(CommandOrText(["heist", "rob"], prefix_words=("سرقت", "دزدی", "heist")))
async def cmd_heist(message: Message, command: CommandObject | None = None) -> None:
    user = message.from_user
    if user is None:
        return
    try:
        economy.require_ready(user.id, "heist", settings.cooldown_heist)
        player = await hydrate(user.id, user.full_name, user.username)

        args = _extract_args(message, command)
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
# Casino
# ---------------------------------------------------------------------------


@router.message(CommandOrText(["dice"], prefix_words=("تاس", "dice")))
async def cmd_dice(message: Message, command: CommandObject | None = None) -> None:
    await _casino_command(message, command, game="dice")


@router.message(CommandOrText(["coinflip", "flip"], prefix_words=("سکه", "شیرخط", "شیر یا خط", "coinflip")))
async def cmd_coinflip(message: Message, command: CommandObject | None = None) -> None:
    await _casino_command(message, command, game="coinflip")


async def _casino_command(message: Message, command: CommandObject | None, game: str) -> None:
    user = message.from_user
    if user is None:
        return
    try:
        economy.require_ready(user.id, "casino", settings.cooldown_casino)
        player = await hydrate(user.id, user.full_name, user.username)

        args = _extract_args(message, command)
        if game == "dice":
            pick_raw = args[1].lower() if len(args) > 1 else ""
            pick = "high" if pick_raw in ("high", "بالا", "بزرگ") else ("low" if pick_raw in ("low", "پایین", "کوچک") else pick_raw)
            if len(args) < 2 or not args[0].isdigit() or pick not in ("high", "low"):
                raise GameError(
                    f"نحوه استفاده: <code>تاس [شرط] بالا|پایین</code> یا <code>/dice [bet] high|low</code>\n"
                    f"بالا (۸ به بالا) · پایین (۶ به پایین) — مجموع ۷ همیشه بازنده است.\n"
                    f"مبلغ شرط: {settings.casino_min_bet:,} تا {settings.casino_max_bet:,} سکه."
                )
            bet = economy.validate_bet(int(args[0]))
            result = await economy.casino_dice(player.user_id, bet, pick)
        else:
            pick_raw = args[1].lower() if len(args) > 1 else ""
            pick = "heads" if pick_raw in ("heads", "شیر") else ("tails" if pick_raw in ("tails", "خط") else pick_raw)
            if len(args) < 2 or not args[0].isdigit() or pick not in ("heads", "tails"):
                raise GameError(
                    f"نحوه استفاده: <code>سکه [شرط] شیر|خط</code> یا <code>/coinflip [bet] heads|tails</code>\n"
                    f"مبلغ شرط: {settings.casino_min_bet:,} تا {settings.casino_max_bet:,} سکه."
                )
            bet = economy.validate_bet(int(args[0]))
            result = await economy.casino_coinflip(player.user_id, bet, pick)

        await message.reply(_activity_card(result))
    except Exception as exc:  # noqa: BLE001
        await answer_error(exc, message=message)


# ---------------------------------------------------------------------------
# /balance, /stats
# ---------------------------------------------------------------------------


@router.message(CommandOrText(["balance", "bal", "wallet", "money"], BAL_WORDS))
async def cmd_balance(message: Message) -> None:
    user = message.from_user
    if user is None:
        return
    try:
        player = await hydrate(user.id, user.full_name, user.username)
        credits, shards = await economy.balances(player.user_id)
        discount = economy.drip_discount(player.drip)
        await message.reply(
            f"💰 <b>کیف پول {esc(player.display_tag)}</b>\n"
            f"سکه: <b>{credits:,}</b>\n"
            f"شارد روح: <b>{shards}</b> 💎\n"
            f"تخفیف استایل در فروشگاه: <b>{discount:.0%}</b>"
        )
    except Exception as exc:  # noqa: BLE001
        await answer_error(exc, message=message)


@router.message(CommandOrText(["stats", "level"], STATS_WORDS))
async def cmd_stats(message: Message) -> None:
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
            f"⚔️ قدرت: <b>{player.atk}</b> · 🛡 دفاع: <b>{player.defense}</b> · 💎 استایل: <b>{player.drip}</b>\n"
            f"✨ پیشرفت: <b>{player.exp}/{exp_to_next(player.level)}</b> EXP تا لول بعدی"
        )
    except Exception as exc:  # noqa: BLE001
        await answer_error(exc, message=message)


# ---------------------------------------------------------------------------
# Quick action buttons (posted under the character card)
# ---------------------------------------------------------------------------


@router.callback_query(F.data == "act:work")
async def cb_work(call: CallbackQuery) -> None:
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
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🎴 کارت من", callback_data="act:me"),
                InlineKeyboardButton(text="🏪 فروشگاه", callback_data="shop:0"),
            ],
            [
                InlineKeyboardButton(text="💼 کار کردن", callback_data="act:work"),
                InlineKeyboardButton(text="📅 جایزه روزانه", callback_data="act:daily"),
            ],
            [
                InlineKeyboardButton(text="📜 لیست تمام دستورات", callback_data="help:cmds"),
                InlineKeyboardButton(text="⚔️ راهنمای مبارزات", callback_data="help:combat"),
            ],
        ]
    )


_HELP_MAIN_TEXT = (
    "🎮 <b>به دنیای بازی شهری فانتزی (Urban Fantasy RPG) خوش اومدی!</b>\n\n"
    "توی این بازی می‌تونی کاراکتر خودت رو بسازی، کار کنی، لول‌آپ بشی، تجهیزات و لباس‌های خفن بخری، "
    "توی چت با بقیه اعضا دوئل کنی و به باس‌های غول‌پیکر گروهی حمله کنی!\n\n"
    "⚡ <b>انرژی:</b> منبع اصلی فعالیت‌ها و مبارزات که در طول زمان بازسازی میشه.\n"
    "⚔️ <b>قدرت (ATK):</b> افزایش دمیج شما توی دوئل‌ها و باس‌ها.\n"
    "🛡 <b>دفاع (DEF):</b> کاهش آسیب دریافتی از حریف.\n"
    "💎 <b>استایل (DRIP):</b> جذابیت ظاهری! شانس موفقیت سرقت رو می‌بره بالا و توی فروشگاه تخفیف میده.\n\n"
    "👇 از دکمه‌های زیر برای دسترسی سریع یا مشاهده لیست دستورات استفاده کن:"
)


_HELP_CMDS_TEXT = (
    "📜 <b>لیست تمام دستورات بازی (متنی ساده و اسلش)</b>\n\n"
    "👤 <b>کاراکتر و مشخصات:</b>\n"
    "• <code>پروفایل</code> یا <code>کارت</code> یا <code>/me</code> — نمایش کارت گرافیکی\n"
    "• <code>کوله</code> یا <code>کیف</code> یا <code>/inventory</code> — مشاهده و مدیریت تجهیزات\n"
    "• <code>آمار</code> یا <code>/stats</code> — اطلاعات ارقام و لول\n"
    "• <code>موجودی</code> یا <code>/balance</code> — سکه‌ها و شاردهای روح\n\n"
    "💰 <b>اقتصاد و درآمد:</b>\n"
    "• <code>کار</code> یا <code>/work</code> — شیفت کاری و دریافت سکه\n"
    "• <code>روزانه</code> یا <code>/daily</code> — دریافت حقوق و جایزه ۲۴ ساعته\n"
    "• <code>سرقت [مبلغ]</code> یا <code>/heist [مبلغ]</code> — سرقت خیابانی پرریسک\n"
    "• <code>فروشگاه</code> یا <code>شاپ</code> یا <code>/shop</code> — بوتیک روزانه\n\n"
    "🎲 <b>کازینو و شانس:</b>\n"
    "• <code>تاس [شرط] بالا|پایین</code> یا <code>/dice [bet] high|low</code>\n"
    "• <code>سکه [شرط] شیر|خط</code> یا <code>/coinflip [bet] heads|tails</code>\n\n"
    "⚔️ <b>مبارزات:</b>\n"
    "• <code>دوئل [مبلغ]</code> (در ریپلای به پیام دیگران) — چالش مبارزه\n"
    "• <code>باس</code> یا <code>حمله</code> یا <code>/boss</code> — باس فعال گروه"
)


_HELP_COMBAT_TEXT = (
    "⚔️ <b>راهنمای مبارزات، دوئل و باس‌های گروهی</b>\n\n"
    "🔥 <b>دوئل خیابانی:</b>\n"
    "روی پیام هر کاربری در گروه ریپلای بزن و بنویس:\n"
    "<code>دوئل 100</code>\n"
    "مبلغ شرط بلافاصله امانت نگه داشته میشه و حریف ۱۰ دقیقه وقت داره قبول یا رد کنه. "
    "مبارزه در راندهای مهیج بر اساس قدرت و دفاع و تاس محاسبه میشه و کل پات به برنده می‌رسه!\n\n"
    "🚨 <b>باس‌های گروهی (Raids):</b>\n"
    "با چت کردن و فعالیت اعضای گروه، غول‌های خیابانی ظاهر میشن! "
    "هر ضربه مقداری انرژی مصرف می‌کنه و به باس آسیب می‌زنه. وقتی باس از پا دربیاد، "
    "سکه و شارد روح بر اساس درصد دمیج بین همه ضربه‌زننده‌ها تقسیم میشه!"
)


@router.message(CommandOrText(["help"], HELP_WORDS))
async def cmd_help(message: Message) -> None:
    await message.reply(_HELP_MAIN_TEXT, reply_markup=_help_markup())


@router.callback_query(F.data.in_(("act:help", "help:main")))
async def cb_help_main(call: CallbackQuery) -> None:
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
    await call.answer()
    message = call.message
    if isinstance(message, Message):
        back_markup = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🔙 بازگشت به راهنما", callback_data="help:main")]
            ]
        )
        try:
            if message.photo:
                await message.edit_caption(caption=_HELP_CMDS_TEXT, reply_markup=back_markup)
            else:
                await message.edit_text(_HELP_CMDS_TEXT, reply_markup=back_markup)
        except Exception:
            await message.answer(_HELP_CMDS_TEXT, reply_markup=back_markup)


@router.callback_query(F.data == "help:combat")
async def cb_help_combat(call: CallbackQuery) -> None:
    await call.answer()
    message = call.message
    if isinstance(message, Message):
        back_markup = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🔙 بازگشت به راهنما", callback_data="help:main")]
            ]
        )
        try:
            if message.photo:
                await message.edit_caption(caption=_HELP_COMBAT_TEXT, reply_markup=back_markup)
            else:
                await message.edit_text(_HELP_COMBAT_TEXT, reply_markup=back_markup)
        except Exception:
            await message.answer(_HELP_COMBAT_TEXT, reply_markup=back_markup)
