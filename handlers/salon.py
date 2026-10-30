"""Interactive Telegram router for Tiramix Salon, Barber & Solarium.

Zero-spam inline menus for:
- Solarium: Paid skin tone changes (500 credits) with live Everskies avatar CDN preview.
- Makeup Studio: Hair styling & coloring, Eyes & lenses, Nose & piercings,
  Ears & earrings, Lips & lipstick.
"""

from __future__ import annotations

import contextlib
import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    Message,
)

from handlers.common import CommandOrText, answer_error
from services import salon

logger = logging.getLogger(__name__)

router = Router(name="salon")

SALON_COMMANDS = {
    "آرایشگاه",
    "سلمونی",
    "میکاپ",
    "زیبایی",
    "جراحی",
    "عمل",
    "سولاریوم",
    "پیرایش",
    "رنگ پوست",
}


def _nav_btn(text: str, cb: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=cb)


async def _build_salon_menu(user_id: int) -> tuple[str, str, InlineKeyboardMarkup]:
    """Generate main salon overview with current style and navigation buttons."""
    st = await salon.get_player_salon_state(user_id)
    caption = (
        f"✂️ <b>سالن پیرایش، زیبایی و سولاریوم تیرامیکس</b> 💄\n"
        f"💰 موجودی شما: <b>{st['credits']:,}</b> سکه\n\n"
        f"👤 <b>وضعیت فعلی چهره و استایل شما:</b>\n"
        f"• ☀️ رنگ پوست: <b>{st['skin_label']}</b>\n"
        f"• ✂️ مدل و رنگ مو: <b>{st['hair_label']}</b> ({st['hair_color_label']})\n"
        f"• 👁 فرم چشم و لنز: <b>{st['eye_label']}</b> ({st['eye_color_label']})\n"
        f"• 👃 بینی: <b>{st['nose_label']}</b>\n"
        f"• 👂 گوش: <b>{st['ear_label']}</b>\n"
        f"• 👄 لب و رژ: <b>{st['lip_label']}</b>\n\n"
        f"👇 بخش مورد نظرت رو برای تغییر انتخاب کن:"
    )

    buttons = [
        [
            _nav_btn("☀️ سولاریوم و رنگ پوست (۵۰۰ سکه)", "sal:skin"),
        ],
        [
            _nav_btn("💄 سالن میکاپ و چهره", "sal:makeup"),
        ],
        [
            _nav_btn("👗 بوتیک لباس", "btq:cat:female"),
            _nav_btn("👕 کمد لباس من", "wdr:view:0"),
        ],
        [
            _nav_btn("❌ خروج از آرایشگاه", "sal:close"),
        ],
    ]
    return st["render_url"], caption, InlineKeyboardMarkup(inline_keyboard=buttons)


async def _build_solarium_menu(user_id: int) -> tuple[str, str, InlineKeyboardMarkup]:
    """Generate paid solarium skin tone menu."""
    st = await salon.get_player_salon_state(user_id)
    caption = (
        f"☀️ <b>سولاریوم و برنزه‌سازی تیرامیکس</b>\n\n"
        f"💵 هزینه هر نوبت تغییر پوست: <b>{salon.SKIN_CHANGE_FEE:,}</b> سکه\n"
        f"💰 موجودی شما: <b>{st['credits']:,}</b> سکه\n"
        f"🎭 رنگ پوست فعلی: <b>{st['skin_label']}</b>\n\n"
        f"رنگ مورد نظرت رو برای تغییر پوست انتخاب کن:"
    )

    buttons: list[list[InlineKeyboardButton]] = []
    for tone in salon.SKIN_TONES:
        is_cur = " (فعلی) ✅" if tone["id"] == st["skin_tone_id"] else ""
        label = f"{tone['label']}{is_cur}"
        buttons.append([_nav_btn(label, f"sal:set_skin:{tone['id']}")])

    buttons.append([_nav_btn("🔙 بازگشت به منوی آرایشگاه", "sal:menu")])
    return st["render_url"], caption, InlineKeyboardMarkup(inline_keyboard=buttons)


async def _build_makeup_menu(user_id: int) -> tuple[str, str, InlineKeyboardMarkup]:
    """Generate makeup studio category hub."""
    st = await salon.get_player_salon_state(user_id)
    caption = (
        f"💄 <b>سالن میکاپ و چهره تیرامیکس</b>\n\n"
        f"💰 موجودی شما: <b>{st['credits']:,}</b> سکه\n\n"
        f"در این بخش می‌توانید اجزای مختلف چهره‌تان را با جدیدترین متدهای مد ارتقا دهید.\n"
        f"لطفاً بخش مورد نظر را انتخاب کنید:"
    )

    buttons = [
        [
            _nav_btn(f"✂️ مدل و رنگ مو ({salon.HAIR_CHANGE_FEE:,} سکه)", "sal:mu:hair"),
        ],
        [
            _nav_btn(f"👁 چشم، لنز و خط چشم ({salon.EYES_CHANGE_FEE:,} سکه)", "sal:mu:eyes"),
        ],
        [
            _nav_btn(f"👃 فرم و پیرسینگ بینی ({salon.NOSE_CHANGE_FEE:,} سکه)", "sal:mu:nose"),
        ],
        [
            _nav_btn(f"👂 گوش، گوشواره و پیرسینگ ({salon.EARS_CHANGE_FEE:,} سکه)", "sal:mu:ears"),
        ],
        [
            _nav_btn(f"👄 لب، رژ لب و لبخند ({salon.LIPS_CHANGE_FEE:,} سکه)", "sal:mu:lips"),
        ],
        [
            _nav_btn("🔙 بازگشت به آرایشگاه", "sal:menu"),
        ],
    ]
    return st["render_url"], caption, InlineKeyboardMarkup(inline_keyboard=buttons)


async def _build_hair_menu(user_id: int) -> tuple[str, str, InlineKeyboardMarkup]:
    """Generate hairstyle and hair color picker tailored to player gender."""
    st = await salon.get_player_salon_state(user_id)
    p_gen = (st.get("gender") or "").lower()
    is_male = any(k in p_gen for k in ("مرد", "پسر", "boy", "male"))

    caption = (
        f"✂️ <b>پیرایش و سالن تخصصی مو تیرامیکس</b>\n"
        f"💵 هزینه هر مدل مو: <b>{salon.HAIR_CHANGE_FEE:,}</b> سکه\n"
        f"💰 موجودی شما: <b>{st['credits']:,}</b> سکه\n"
        f"👤 استایل فعلی: <b>{st['hair_label']}</b> ({st['hair_color_label']})\n\n"
        f"مدل موی مورد نظرت رو لمس کن تا در لحظه بر سر کاراکتر بنشیند:"
    )

    buttons: list[list[InlineKeyboardButton]] = []
    # Filter catalog by player gender
    target_gender = "male" if is_male else "female"
    curated_hairs = [
        (k, defn["name"])
        for k, defn in salon.HAIR_CATALOG.items()
        if defn["gender"] in (target_gender, "unisex")
    ]

    for k, name in curated_hairs:
        mark = " ✅" if k == st["hair_style"] else ""
        buttons.append([_nav_btn(f"{name}{mark}", f"sal:set_hair:{k}")])

    # Hair colors
    color_row1 = [
        _nav_btn("🖤 مشکی", "sal:set_hair_col:black"),
        _nav_btn("🌰 قهوه‌ای", "sal:set_hair_col:brown"),
        _nav_btn("🌾 بلوند", "sal:set_hair_col:blonde"),
    ]
    color_row2 = [
        _nav_btn("❄️ نقره‌ای", "sal:set_hair_col:silver"),
        _nav_btn("🍷 شرابی", "sal:set_hair_col:crimson"),
        _nav_btn("🔮 بنفش", "sal:set_hair_col:purple"),
    ]
    buttons.append(color_row1)
    buttons.append(color_row2)
    buttons.append([_nav_btn("🔙 بازگشت به سالن چهره", "sal:makeup")])

    return st["render_url"], caption, InlineKeyboardMarkup(inline_keyboard=buttons)


async def _build_eyes_menu(user_id: int) -> tuple[str, str, InlineKeyboardMarkup]:
    """Generate eyes, markings and lens colors picker."""
    st = await salon.get_player_salon_state(user_id)
    caption = (
        f"👁 <b>میکاپ چشم، لنز و خط چشم</b>\n"
        f"💵 هزینه هر تغییر: <b>{salon.EYES_CHANGE_FEE:,}</b> سکه\n"
        f"💰 موجودی: <b>{st['credits']:,}</b> سکه\n"
        f"👤 مدل فعلی: <b>{st['eye_label']}</b> ({st['eye_color_label']})\n\n"
        f"فرم چشم یا رنگ لنز مورد نظرت رو انتخاب کن:"
    )

    buttons: list[list[InlineKeyboardButton]] = []
    for k, v in salon.EYE_SHAPES.items():
        mark = " ✅" if k == st["eye_style"] else ""
        buttons.append([_nav_btn(f"{v}{mark}", f"sal:set_eye:{k}")])

    lens_row = [
        _nav_btn("💎 آبی", "sal:set_eye_col:blue"),
        _nav_btn("🌿 سبز", "sal:set_eye_col:green"),
        _nav_btn("🍯 عسلی", "sal:set_eye_col:amber"),
    ]
    lens_row2 = [
        _nav_btn("🔮 بنفش", "sal:set_eye_col:violet"),
        _nav_btn("🖤 مشکی", "sal:set_eye_col:black"),
        _nav_btn("🩸 قرمز", "sal:set_eye_col:crimson"),
    ]
    buttons.append(lens_row)
    buttons.append(lens_row2)
    buttons.append([_nav_btn("🔙 بازگشت به سالن میکاپ", "sal:makeup")])

    return st["render_url"], caption, InlineKeyboardMarkup(inline_keyboard=buttons)


async def _build_nose_menu(user_id: int) -> tuple[str, str, InlineKeyboardMarkup]:
    """Generate nose and piercing selector."""
    st = await salon.get_player_salon_state(user_id)
    caption = (
        f"👃 <b>فرم و پیرسینگ بینی</b>\n"
        f"💵 هزینه: <b>{salon.NOSE_CHANGE_FEE:,}</b> سکه\n"
        f"💰 موجودی: <b>{st['credits']:,}</b> سکه\n"
        f"👤 استایل فعلی: <b>{st['nose_label']}</b>\n\n"
        f"مدل دلخواهتان را انتخاب کنید:"
    )

    buttons: list[list[InlineKeyboardButton]] = []
    for k, v in salon.NOSE_STYLES.items():
        mark = " ✅" if k == st["nose_style"] else ""
        buttons.append([_nav_btn(f"{v}{mark}", f"sal:set_nose:{k}")])

    buttons.append([_nav_btn("🔙 بازگشت به سالن میکاپ", "sal:makeup")])
    return st["render_url"], caption, InlineKeyboardMarkup(inline_keyboard=buttons)


async def _build_ears_menu(user_id: int) -> tuple[str, str, InlineKeyboardMarkup]:
    """Generate ears, earrings and ear piercings selector."""
    st = await salon.get_player_salon_state(user_id)
    caption = (
        f"👂 <b>گوش، گوشواره و پیرسینگ گوش</b>\n"
        f"💵 هزینه: <b>{salon.EARS_CHANGE_FEE:,}</b> سکه\n"
        f"💰 موجودی: <b>{st['credits']:,}</b> سکه\n"
        f"👤 استایل فعلی: <b>{st['ear_label']}</b>\n\n"
        f"مدل دلخواهتان را انتخاب کنید:"
    )

    buttons: list[list[InlineKeyboardButton]] = []
    for k, v in salon.EAR_STYLES.items():
        mark = " ✅" if k == st["ear_style"] else ""
        buttons.append([_nav_btn(f"{v}{mark}", f"sal:set_ear:{k}")])

    buttons.append([_nav_btn("🔙 بازگشت به سالن میکاپ", "sal:makeup")])
    return st["render_url"], caption, InlineKeyboardMarkup(inline_keyboard=buttons)


async def _build_lips_menu(user_id: int) -> tuple[str, str, InlineKeyboardMarkup]:
    """Generate lips and lipstick makeup selector."""
    st = await salon.get_player_salon_state(user_id)
    caption = (
        f"👄 <b>لب، رژ لب و میکاپ لب</b>\n"
        f"💵 هزینه: <b>{salon.LIPS_CHANGE_FEE:,}</b> سکه\n"
        f"💰 موجودی: <b>{st['credits']:,}</b> سکه\n"
        f"👤 استایل فعلی: <b>{st['lip_label']}</b>\n\n"
        f"مدل رژ لب دلخواهتان را انتخاب کنید:"
    )

    buttons: list[list[InlineKeyboardButton]] = []
    for k, v in salon.LIP_STYLES.items():
        mark = " ✅" if k == st["lip_style"] else ""
        buttons.append([_nav_btn(f"{v}{mark}", f"sal:set_lip:{k}")])

    buttons.append([_nav_btn("🔙 بازگشت به سالن میکاپ", "sal:makeup")])
    return st["render_url"], caption, InlineKeyboardMarkup(inline_keyboard=buttons)


# ---------------------------------------------------------------------------
# Message Commands
# ---------------------------------------------------------------------------

@router.message(CommandOrText(["salon", "barber", "makeup", "beauty", "skin"], SALON_COMMANDS))
async def cmd_salon_entry(message: Message) -> None:
    """Entry point for salon, barbershop, solarium and makeup."""
    user = message.from_user
    if user is None:
        return
    try:
        parts = (message.text or "").strip().split()
        cmd_trigger = parts[0].lstrip("/").lower() if parts else ""

        # Quick text command: e.g. "آرایشگاه 2 مشکی"
        if len(parts) >= 3 and cmd_trigger in ("barber", "salon", "آرایشگاه", "سلمونی"):
            s_num = parts[1]
            c_fa = parts[2]
            color_map = {
                "مشکی": "black",
                "نقره‌ای": "silver",
                "نقره": "silver",
                "قهوه‌ای": "brown",
                "قهوه": "brown",
                "بلوند": "blonde",
                "شرابی": "crimson",
                "بنفش": "purple",
                "صورتی": "pink",
                "آبی": "blue",
            }
            c_key = color_map.get(c_fa, "black")
            s_key = f"hair{s_num}" if s_num in ("1", "2", "3", "4", "5") else "hair1"
            res = await salon.change_hair_paid(user.id, s_key, c_key)
            await message.reply(res["message"])
            return

        # Quick beauty surgery: e.g. "زیبایی 2 آبی 1"
        if len(parts) >= 4 and cmd_trigger in ("beauty", "زیبایی", "جراحی", "عمل"):
            e_num = parts[1] if parts[1] in ("1", "2", "3", "4") else "1"
            col_fa = parts[2]
            m_num = parts[3] if parts[3] in ("1", "2", "3", "4") else "1"
            color_map = {
                "آبی": "blue",
                "سبز": "green",
                "عسلی": "amber",
                "بنفش": "violet",
                "مشکی": "black",
            }
            col_key = color_map.get(col_fa, "blue")
            e_key = f"eyes{e_num}_1"
            res_e = await salon.change_eyes_paid(user.id, e_key, col_key)
            if not res_e["success"]:
                await message.reply(res_e["message"])
                return
            await salon.change_lips_paid(user.id, "lip_natural_nude")
            await message.reply(
                f"💎 <b>جراحی و میکاپ چهره با موفقیت ثبت شد!</b>\n"
                f"• فرم چشم: <b>مدل {e_num} با لنز {col_fa}</b>\n"
                f"• فرم لب: <b>مدل {m_num}</b>"
            )
            return

        # Quick solarium: e.g. "سولاریوم 3" or "پوست 4"
        if len(parts) >= 2 and cmd_trigger in ("skin", "سولاریوم", "پوست"):
            tone_arg = parts[1]
            t_id = -int(tone_arg) if tone_arg.isdigit() and 1 <= int(tone_arg) <= 6 else -1
            res_s = await salon.change_skin_tone_paid(user.id, t_id)
            await message.reply(res_s["message"])
            return

        render_url, caption, markup = await _build_salon_menu(user.id)
        if render_url:
            from handlers.boutique import _render_photo_payload
            photo_payload = await _render_photo_payload(render_url, "salon.png")
            await message.reply_photo(
                photo=photo_payload, caption=caption, reply_markup=markup, parse_mode="HTML"
            )
        else:
            await message.reply(caption, reply_markup=markup, parse_mode="HTML")
    except Exception as exc:  # noqa: BLE001
        await answer_error(exc, message=message)


# ---------------------------------------------------------------------------
# Callback Handlers
# ---------------------------------------------------------------------------

@router.callback_query(F.data.startswith("sal:"))
async def cb_salon_actions(call: CallbackQuery) -> None:
    """Unified handler for all salon, solarium and makeup callbacks."""
    user = call.from_user
    if user is None:
        return

    data = call.data or ""
    parts = data.split(":")
    action = parts[1] if len(parts) > 1 else "menu"

    try:
        if action == "close":
            if call.message:
                with contextlib.suppress(TelegramBadRequest):
                    await call.message.delete()
            await call.answer("با تشکر از مراجعه شما به سالن زیبایی تیرامیکس! ✨")
            return

        if action in ("menu", "main"):
            render_url, caption, markup = await _build_salon_menu(user.id)
            await _update_view(call, render_url, caption, markup)
            await call.answer()
            return

        if action == "skin":
            render_url, caption, markup = await _build_solarium_menu(user.id)
            await _update_view(call, render_url, caption, markup)
            await call.answer()
            return

        if action == "set_skin":
            tone_id = int(parts[2])
            res = await salon.change_skin_tone_paid(user.id, tone_id)
            if not res["success"]:
                await call.answer(res["message"], show_alert=True)
                return
            await call.answer(
                f"✅ رنگ پوست تغییر یافت! ({salon.SKIN_CHANGE_FEE:,} سکه کسر شد)",
                show_alert=True,
            )
            render_url, caption, markup = await _build_solarium_menu(user.id)
            await _update_view(call, render_url, caption, markup)
            return

        if action == "makeup":
            render_url, caption, markup = await _build_makeup_menu(user.id)
            await _update_view(call, render_url, caption, markup)
            await call.answer()
            return

        if action == "mu":
            sub = parts[2]
            if sub == "hair":
                render_url, caption, markup = await _build_hair_menu(user.id)
            elif sub == "eyes":
                render_url, caption, markup = await _build_eyes_menu(user.id)
            elif sub == "nose":
                render_url, caption, markup = await _build_nose_menu(user.id)
            elif sub == "ears":
                render_url, caption, markup = await _build_ears_menu(user.id)
            elif sub == "lips":
                render_url, caption, markup = await _build_lips_menu(user.id)
            else:
                render_url, caption, markup = await _build_makeup_menu(user.id)
            await _update_view(call, render_url, caption, markup)
            await call.answer()
            return

        # Makeup apply actions
        if action == "set_hair":
            hair_key = parts[2]
            res = await salon.change_hair_paid(user.id, hair_key)
            if not res["success"]:
                await call.answer(res["message"], show_alert=True)
                return
            fee_txt = f"{salon.HAIR_CHANGE_FEE:,} سکه"
            await call.answer(f"✅ مدل مو تغییر کرد! ({fee_txt})", show_alert=True)
            render_url, caption, markup = await _build_hair_menu(user.id)
            await _update_view(call, render_url, caption, markup)
            return

        if action == "set_hair_col":
            col_key = parts[2]
            st = await salon.get_player_salon_state(user.id)
            res = await salon.change_hair_paid(user.id, st["hair_style"], col_key)
            if not res["success"]:
                await call.answer(res["message"], show_alert=True)
                return
            fee_txt = f"{salon.HAIR_CHANGE_FEE:,} سکه"
            await call.answer(f"✅ رنگ مو اعمال شد! ({fee_txt})", show_alert=True)
            render_url, caption, markup = await _build_hair_menu(user.id)
            await _update_view(call, render_url, caption, markup)
            return

        if action == "set_eye":
            eye_key = parts[2]
            res = await salon.change_eyes_paid(user.id, eye_key)
            if not res["success"]:
                await call.answer(res["message"], show_alert=True)
                return
            fee_txt = f"{salon.EYES_CHANGE_FEE:,} سکه"
            await call.answer(f"✅ فرم چشم تغییر کرد! ({fee_txt})", show_alert=True)
            render_url, caption, markup = await _build_eyes_menu(user.id)
            await _update_view(call, render_url, caption, markup)
            return

        if action == "set_eye_col":
            col_key = parts[2]
            st = await salon.get_player_salon_state(user.id)
            res = await salon.change_eyes_paid(user.id, st["eye_style"], col_key)
            if not res["success"]:
                await call.answer(res["message"], show_alert=True)
                return
            fee_txt = f"{salon.EYES_CHANGE_FEE:,} سکه"
            await call.answer(f"✅ رنگ لنز اعمال شد! ({fee_txt})", show_alert=True)
            render_url, caption, markup = await _build_eyes_menu(user.id)
            await _update_view(call, render_url, caption, markup)
            return

        if action == "set_nose":
            nose_key = parts[2]
            res = await salon.change_nose_paid(user.id, nose_key)
            if not res["success"]:
                await call.answer(res["message"], show_alert=True)
                return
            fee_txt = f"{salon.NOSE_CHANGE_FEE:,} سکه"
            await call.answer(f"✅ مدل بینی تغییر کرد! ({fee_txt})", show_alert=True)
            render_url, caption, markup = await _build_nose_menu(user.id)
            await _update_view(call, render_url, caption, markup)
            return

        if action == "set_ear":
            ear_key = parts[2]
            res = await salon.change_ears_paid(user.id, ear_key)
            if not res["success"]:
                await call.answer(res["message"], show_alert=True)
                return
            fee_txt = f"{salon.EARS_CHANGE_FEE:,} سکه"
            await call.answer(f"✅ پیرسینگ گوش ثبت شد! ({fee_txt})", show_alert=True)
            render_url, caption, markup = await _build_ears_menu(user.id)
            await _update_view(call, render_url, caption, markup)
            return

        if action == "set_lip":
            lip_key = parts[2]
            res = await salon.change_lips_paid(user.id, lip_key)
            if not res["success"]:
                await call.answer(res["message"], show_alert=True)
                return
            fee_txt = f"{salon.LIPS_CHANGE_FEE:,} سکه"
            await call.answer(f"✅ رژ لب اعمال شد! ({fee_txt})", show_alert=True)
            render_url, caption, markup = await _build_lips_menu(user.id)
            await _update_view(call, render_url, caption, markup)
            return

    except Exception as exc:  # noqa: BLE001
        await answer_error(exc, callback=call)


async def _update_view(
    call: CallbackQuery,
    render_url: str,
    caption: str,
    markup: InlineKeyboardMarkup,
) -> None:
    """Update message media or text without chat spam using fresh HD bytes."""
    if not call.message:
        return

    if render_url:
        from handlers.boutique import _render_photo_payload
        photo_payload = await _render_photo_payload(render_url, "salon.png")
        media = InputMediaPhoto(media=photo_payload, caption=caption, parse_mode="HTML")
        with contextlib.suppress(TelegramBadRequest):
            await call.message.edit_media(media=media, reply_markup=markup)
    else:
        with contextlib.suppress(TelegramBadRequest):
            await call.message.edit_text(text=caption, reply_markup=markup, parse_mode="HTML")
