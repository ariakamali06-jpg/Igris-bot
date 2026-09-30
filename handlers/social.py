"""Tiramix Social Interactions: Theft, Duel, Marriage, Divorce (with random group Judge), Intimacy, Affairs and Clans."""

from __future__ import annotations

import logging
import random
import time

from aiogram import F, Router
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from handlers.common import (
    CommandOrText,
    answer_error,
    esc,
    hydrate,
)
from services import economy, game, tiramix

logger = logging.getLogger(__name__)
router = Router(name="social")

# In-memory proposal and divorce pending storage
# {target_id: {"proposer_id": int, "proposer_name": str, "timestamp": int}}
PENDING_MARRIAGES: dict[int, dict] = {}
# {spouse_id: {"requester_id": int, "requester_name": str, "group_id": int, "judge_id": int, "judge_name": str}}
PENDING_DIVORCES: dict[int, dict] = {}


# ---------------------------------------------------------------------------
# 1. Crime: Theft (دزدی)
# ---------------------------------------------------------------------------

@router.message(CommandOrText(["steal", "robbery"], {"دزدی", "جیب‌بری", "خفت‌گیری"}))
async def cmd_steal(message: Message) -> None:
    user = message.from_user
    if user is None:
        return

    reply = message.reply_to_message
    if reply is None or reply.from_user is None:
        await message.reply(
            "🥷 <b>دستور دزدی و جیب‌بری</b>\n\n"
            "برای سرقت از یک شهروند، باید روی پیام او ریپلای بزنید و بنویسید <code>دزدی</code>!\n"
            "⚠️ <i>هشدار: در صورت شکست، دادگاه شما را ۵ برابر مبلغ جریمه نقدی یا ۳۰ دقیقه زندانی می‌کند!</i>"
        )
        return

    target_user = reply.from_user
    if target_user.is_bot:
        await message.reply("❌ نمی‌توانید از ربات‌ها دزدی کنید!")
        return

    try:
        thief = await hydrate(user.id, user.full_name, user.username)
        victim = await hydrate(target_user.id, target_user.full_name, target_user.username)

        res = await tiramix.attempt_theft(thief, victim)
        await message.reply(res["message"])
    except Exception as exc:  # noqa: BLE001
        await answer_error(exc, message=message)


# ---------------------------------------------------------------------------
# 3. Marriage (ازدواج)
# ---------------------------------------------------------------------------

@router.message(CommandOrText(["marry", "propose"], {"ازدواج", "خواستگاری", "عقد"}))
async def cmd_marriage(message: Message) -> None:
    user = message.from_user
    if user is None:
        return

    reply = message.reply_to_message
    if reply is None or reply.from_user is None:
        await message.reply(
            "💍 <b>ثبت ازدواج رسمی در شهر تیرامیکس</b>\n\n"
            "روی پیام عشق یا همسر آینده‌ات ریپلای بزن و بنویس <code>ازدواج</code>!\n"
            "⚠️ <i>شرط: هر دو طرف باید حداقل به لول ۳ رسیده باشند.</i>"
        )
        return

    target_user = reply.from_user
    if target_user.id == user.id:
        await message.reply("❌ خودشیفتگی حدی داره! نمی‌تونی با خودت ازدواج کنی! 😂")
        return

    try:
        proposer = await hydrate(user.id, user.full_name, user.username)
        partner = await hydrate(target_user.id, target_user.full_name, target_user.username)

        if proposer.spouse_id:
            await message.reply("❌ شما متأهل هستید! تیرامیکس تک‌همسری است.")
            return
        if partner.spouse_id:
            await message.reply(f"❌ {partner.display_name} متأهل است و تعهد دارد!")
            return
        if proposer.level < 3 or partner.level < 3:
            await message.reply("⚠️ برای ثبت ازدواج رسمی، هر دو طرف باید حداقل به <b>لول ۳</b> رسیده باشند.")
            return

        PENDING_MARRIAGES[partner.user_id] = {
            "proposer_id": proposer.user_id,
            "proposer_name": proposer.display_name,
            "timestamp": int(time.time()),
        }

        buttons = [
            [
                InlineKeyboardButton(text="💍 بله! با افتخار می‌پذیرم 💐", callback_data=f"marry:yes:{proposer.user_id}"),
                InlineKeyboardButton(text="❌ متاسفم، خیر", callback_data=f"marry:no:{proposer.user_id}"),
            ]
        ]
        text = (
            f"💐💍 <b>پیشنهاد ازدواج رسمی در شهر تیرامیکس!</b>\n\n"
            f"کاربر <b>{proposer.display_name}</b> رسماً از شما (<b>{partner.display_name}</b>) خواستگاری کرد!\n\n"
            f"آیا این پیوند مقدس را می‌پذیرید؟"
        )
        await message.reply(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    except Exception as exc:  # noqa: BLE001
        await answer_error(exc, message=message)


@router.callback_query(F.data.startswith("marry:"))
async def cb_marry_response(call: CallbackQuery) -> None:
    parts = call.data.split(":")
    decision = parts[1]
    proposer_id = int(parts[2])
    user = call.from_user

    if user is None:
        return

    req = PENDING_MARRIAGES.get(user.id)
    if not req or req["proposer_id"] != proposer_id:
        await call.answer("⚠️ این پیشنهاد برای شما نیست یا منقضی شده است.", show_alert=True)
        return

    await call.answer()
    PENDING_MARRIAGES.pop(user.id, None)

    if decision == "no":
        await call.message.edit_text(f"💔 درخواست ازدواج با <b>{user.full_name}</b> رد شد.")
        return

    try:
        proposer = await game.load_player(proposer_id)
        if proposer is None:
            await call.answer("کاربر یافت نشد", show_alert=True)
            return
        partner = await hydrate(user.id, user.full_name, user.username)
        res = await tiramix.marry_citizens(proposer, partner)
        await call.message.edit_text(res["message"])
    except Exception as exc:  # noqa: BLE001
        await answer_error(exc, callback=call)


# ---------------------------------------------------------------------------
# 4. Divorce & Random Group Judge (طلاق و قاضی تصادفی)
# ---------------------------------------------------------------------------

@router.message(CommandOrText(["divorce"], {"طلاق", "جدایی"}))
async def cmd_divorce(message: Message) -> None:
    user = message.from_user
    if user is None:
        return

    try:
        player = await hydrate(user.id, user.full_name, user.username)
        if not player.spouse_id:
            await message.reply("❌ شما مجرد هستید و همسری برای طلاق دادن ندارید!")
            return

        spouse = await game.load_player(player.spouse_id)
        if spouse is None:
            await message.reply("همسر شما در دیتابیس یافت نشد!")
            return
        PENDING_DIVORCES[spouse.user_id] = {
            "requester_id": player.user_id,
            "requester_name": player.display_name,
            "group_id": message.chat.id,
        }

        buttons = [
            [
                InlineKeyboardButton(text="📜 قبول طلاق توافقی", callback_data=f"div:agree:{player.user_id}"),
                InlineKeyboardButton(text="⚖️ ارجاع پرونده به دادگاه گروه!", callback_data=f"div:court:{player.user_id}"),
            ]
        ]
        text = (
            f"💔 <b>دادخواست رسمی طلاق در تیرامیکس</b>\n\n"
            f"همسر شما (<b>{player.display_name}</b>) تقاضای جدایی و طلاق کرده است!\n\n"
            f"آیا با جدایی توافقی موافقید یا پرونده برای حکم نهایی به <b>دادگاه خانواده تیرامیکس</b> با قاضی تصادفی ارجاع شود؟"
        )
        await message.reply(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    except Exception as exc:  # noqa: BLE001
        await answer_error(exc, message=message)


@router.callback_query(F.data.startswith("div:"))
async def cb_divorce_response(call: CallbackQuery) -> None:
    parts = call.data.split(":")
    action = parts[1]
    requester_id = int(parts[2])
    user = call.from_user

    if user is None:
        return

    if action == "agree":
        await call.answer()
        res = await tiramix.divorce_citizens(user.id, requester_id)
        await call.message.edit_text(res["message"])
        PENDING_DIVORCES.pop(user.id, None)
    elif action == "court":
        await call.answer("تشکیل دادگاه خانواده تیرامیکس... ⚖️")
        # Appoint random judge from group
        # Pick the user who clicked as a party, and requester as party, judge can be anyone in chat
        # If bot cannot list members, we announce in chat and first impartial member to vote acts as Judge!
        judge_prompt = (
            f"⚖️🏛 <b>دادگاه رسمی تیرامیکس تشکیل شد!</b>\n\n"
            f"مدعی: <b>{user.full_name}</b> | همسر: کاربر <code>{requester_id}</code>\n"
            f"به علت عدم توافق، <b>هر یک از اعضای بی‌طرف حاضر در گروه</b> می‌تواند به عنوان قاضی پرونده حکم صادر کند!\n\n"
            f"جناب قاضی، لطفاً رای نهایی را صادر بفرمایید:"
        )
        judge_buttons = [
            [
                InlineKeyboardButton(text="📜 تایید طلاق و جدایی قطعی", callback_data=f"judge:divorce:{user.id}:{requester_id}"),
                InlineKeyboardButton(text="❌ رد طلاق (ادامه زندگی مشترک)", callback_data=f"judge:reject:{user.id}:{requester_id}"),
            ]
        ]
        await call.message.edit_text(judge_prompt, reply_markup=InlineKeyboardMarkup(inline_keyboard=judge_buttons))


@router.callback_query(F.data.startswith("judge:"))
async def cb_judge_verdict(call: CallbackQuery) -> None:
    parts = call.data.split(":")
    verdict = parts[1]
    p1_id = int(parts[2])
    p2_id = int(parts[3])
    judge_user = call.from_user

    if judge_user is None:
        return

    if judge_user.id in (p1_id, p2_id):
        await call.answer("⚠️ طرفین دعوا نمی‌توانند قاضی پرونده خود باشند!", show_alert=True)
        return

    await call.answer("حکم قاضی ثبت شد ⚖️")

    if verdict == "divorce":
        await tiramix.divorce_citizens(p1_id, p2_id)
        verdict_text = (
            f"⚖️ <b>حکم نهایی دادگاه صادر شد!</b>\n\n"
            f"جناب قاضی (<b>{judge_user.full_name}</b>) پس از بررسی پرونده، <b>حکم طلاق و جدایی قطعی</b> را صادر کرد.\n"
            f"طرفین از این لحظه رسماً مجرد هستند."
        )
    else:
        verdict_text = (
            f"⚖️ <b>حکم نهایی دادگاه صادر شد!</b>\n\n"
            f"جناب قاضی (<b>{judge_user.full_name}</b>) دادخواست طلاق را <b>رد</b> کرد و طرفین را به صلح و سازش در زندگی مشترک فراخواند! 🕊💐"
        )

    await call.message.edit_text(verdict_text)


# ---------------------------------------------------------------------------
# 5. Intimacy & Childbirth (سکس / رابطه)
# ---------------------------------------------------------------------------

@router.message(CommandOrText(["sex", "intimacy"], {"سکس", "رابطه", "عشق‌بازی"}))
async def cmd_intimacy(message: Message) -> None:
    user = message.from_user
    if user is None:
        return

    try:
        p1 = await hydrate(user.id, user.full_name, user.username)
        if not p1.spouse_id:
            await message.reply("❌ این دستور فقط مختص افراد متأهل و با همسر قانونی است!")
            return

        p2 = await game.load_player(p1.spouse_id)
        if p2 is None:
            await message.reply("همسر شما در دیتابیس یافت نشد!")
            return
        res = await tiramix.execute_intimacy(p1, p2)
        await message.reply(res["message"])
    except Exception as exc:  # noqa: BLE001
        await answer_error(exc, message=message)


# ---------------------------------------------------------------------------
# 6. Affair (خیانت)
# ---------------------------------------------------------------------------

@router.message(CommandOrText(["affair"], {"خیانت", "رابطه_پنهانی"}))
async def cmd_affair(message: Message) -> None:
    user = message.from_user
    if user is None:
        return

    reply = message.reply_to_message
    if reply is None or reply.from_user is None:
        await message.reply("🤫 برای برقراری رابطه پنهانی، روی پیام پارتنر خیانت ریپلای بزنید و بنویسید <code>خیانت</code>!")
        return

    partner_user = reply.from_user
    if partner_user.id == user.id:
        await message.reply("❌ خیانت به خود امکان‌پذیر نیست!")
        return

    try:
        cheater = await hydrate(user.id, user.full_name, user.username)
        if not cheater.spouse_id:
            await message.reply("❌ شما مجرد هستید! خیانت فقط برای افراد متأهل معنی دارد.")
            return

        partner = await hydrate(partner_user.id, partner_user.full_name, partner_user.username)
        spouse = await game.load_player(cheater.spouse_id)
        if spouse is None:
            await message.reply("همسر شما در دیتابیس یافت نشد!")
            return

        res = await tiramix.attempt_affair(cheater, partner, spouse)
        await message.reply(res["message"])
    except Exception as exc:  # noqa: BLE001
        await answer_error(exc, message=message)


# ---------------------------------------------------------------------------
# 7. Clans & Syndicates (کلن)
# ---------------------------------------------------------------------------

@router.message(CommandOrText(["clan"], {"کلن", "گروه", "سندیکا"}))
async def cmd_clan(message: Message) -> None:
    user = message.from_user
    if user is None:
        return

    parts = (message.text or "").strip().split()
    try:
        player = await hydrate(user.id, user.full_name, user.username)
        if len(parts) >= 3 and parts[0] in ("ساخت", "تاسیس") and parts[1] in ("کلن", "گروه", "سندیکا"):
            clan_name = " ".join(parts[2:])
            res = await tiramix.create_new_clan(player, message.chat.id, clan_name)
            await message.reply(res["message"])
            return

        if len(parts) >= 3 and parts[1] in ("ساخت", "تاسیس", "create"):
            clan_name = " ".join(parts[2:])
            res = await tiramix.create_new_clan(player, message.chat.id, clan_name)
            await message.reply(res["message"])
            return

        if player.clan_id:
            info = await tiramix.get_player_clan(player.clan_id)
            if info:
                text = (
                    f"🛡️ <b>کلن اختصاصی «{info['name']}»</b>\n\n"
                    f"👑 لیدر: کاربر <code>{info['leader_id']}</code>\n"
                    f"💰 موجودی خزانه کلن: <b>{info['treasury']:,}</b> سکه\n"
                    f"🎖 نقش شما: <b>{player.clan_role or 'عضو'}</b>"
                )
                await message.reply(text)
                return

        text = (
            "🛡️ <b>سیستم کلن‌ها و سندیکاهای شهری تیرامیکس</b>\n\n"
            "شما در حال حاضر در هیچ کلنی عضویت ندارید.\n\n"
            "📌 برای تأسیس یک کلن جدید (هزینه: ۵,۰۰۰ سکه):\n"
            "<code>کلن ساخت [نام کلن]</code>\n"
            "<i>مثال: کلن ساخت گرگ‌های شب</i>"
        )
        await message.reply(text)
    except Exception as exc:  # noqa: BLE001
        await answer_error(exc, message=message)


# Aliases for external callers and tests
cb_marry = cb_marry_response
cmd_marry = cmd_marriage
cmd_clan_create = cmd_clan
