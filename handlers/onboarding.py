
"""Interactive onboarding and character creation wizard for Tiramix Life Simulator."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    User,
    WebAppInfo,
)

from config import settings
from handlers.common import (
    CommandOrText,
    answer_error,
    editable_message,
    esc,
    hydrate,
)
from handlers.profile import _send_profile
from services import avatar, game
from services.channels import channel_join_markup, get_missing_channels

logger = logging.getLogger(__name__)
router = Router(name="onboarding")


def is_admin_or_owner(user_id: int) -> bool:
    """Check if the user is the project owner (Rex Lapis) or has admin privileges."""
    return settings.is_admin(user_id) or user_id == 5765828495


class OnboardingState(StatesGroup):
    waiting_for_name = State()
    waiting_for_age = State()
    waiting_for_gender = State()
    waiting_for_skin = State()
    waiting_for_eye_shape = State()
    waiting_for_eye_color = State()
    waiting_for_nose = State()
    waiting_for_mouth = State()
    waiting_for_hair_style = State()
    waiting_for_hair_color = State()
    waiting_for_starter_set = State()


# ---------------------------------------------------------------------------
# Label Constants
# ---------------------------------------------------------------------------

SKIN_LABELS = {
    "1": "🌕 مهتابی و فوق‌العاده روشن",
    "2": "🌾 روشن و لطیف",
    "3": "🍑 طبیعی و شاداب",
    "4": "🌰 گندمی و گرم",
    "5": "🍫 سبزه و تیره",
    "6": "🌑 دارک پرکلاغی",
    "-1": "🌕 مهتابی و فوق‌العاده روشن",
    "-2": "🌾 روشن و لطیف",
    "-3": "🍑 طبیعی و شاداب",
    "-4": "🌰 گندمی و گرم",
    "-5": "🍫 سبزه و تیره",
    "-6": "🌑 دارک پرکلاغی",
}

EYE_SHAPE_LABELS = {
    "1": "🌸 شاداب و گرد (مدل ۱)",
    "2": "⚡️ تیز و جسور (مدل ۲)",
    "3": "🕊 آرام و خونسرد (مدل ۳)",
    "4": "🔥 بیدارشده و حماسی (مدل ۴)",
    "eyes1_1": "🌸 شاداب و گرد (مدل ۱)",
    "eyes2_1": "⚡️ تیز و جسور (مدل ۲)",
    "eyes3_1": "🕊 آرام و خونسرد (مدل ۳)",
}

EYE_COLOR_LABELS = {
    "blue": "💎 آبی یاقوتی",
    "green": "🌿 سبز زمردی",
    "amber": "🍯 عسلی درخشان",
    "violet": "🔮 بنفش رویایی",
    "black": "🖤 مشکی پرکلاغی",
}

NOSE_LABELS = {
    "1": "👃 طبیعی کلاسیک (مدل ۱)",
    "2": "✨ قلمی و عروسکی (مدل ۲)",
    "3": "💎 پیرسینگ نگین الماس (مدل ۳)",
    "4": "💍 پیرسینگ حلقه نقره‌ای (مدل ۴)",
    "5": "⚡️ پیرسینگ دوتایی پانک (مدل ۵)",
    "nose_natural": "👃 طبیعی کلاسیک",
    "nose_petite": "✨ قلمی و عروسکی",
    "nose_diamond_stud": "💎 پیرسینگ نگین الماس",
    "nose_silver_ring": "💍 پیرسینگ حلقه نقره‌ای",
    "nose_double_punk": "⚡️ پیرسینگ دوتایی پانک",
}

HAIR_STYLES_FEMALE = {
    "hair1": "🎀 بلند موج‌دار کژوال",
    "hair2": "🌸 لایه‌ای مدرن و کره‌ای",
    "hair3": "👱‍♀️ دم‌اسبی پرانرژی",
    "hair4": "💇‍♀️ باب کوتاه شهری",
    "hair5": "✨ باز رها روی شانه",
}

HAIR_STYLES_MALE = {
    "hair1": "🕶 کوتاه فید مدرن (Street Fade)",
    "hair2": "⚡️ لیر آشفته و کره‌ای (Wolf Cut)",
    "hair3": "🎩 فرق بغل کلاسیک",
    "hair4": "🌪 بلند و رها",
    "hair5": "🗡 بوکات منظم",
}

HAIR_COLOR_LABELS = {
    "black": "🖤 مشکی پرکلاغی",
    "silver": "🌪 نقره‌ای پلاتینیوم",
    "brown": "🌰 قهوه‌ای خرمایی",
    "blonde": "🌾 بلوند پلاتینیوم",
    "crimson": "🍷 شرابی سلطنتی",
}

MOUTH_LABELS = {
    "1": "😊 لبخند نود طبیعی (مدل ۱)",
    "2": "😏 پوزخند مغرور و جذاب (مدل ۲)",
    "3": "🍒 رژ مخملی گیلاسی (مدل ۳)",
    "4": "😐 خط لب جدی و باوقار (مدل ۴)",
    "5": "🍷 رژ شرابی اشرافی (مدل ۵)",
    "lip_natural_nude": "😊 لبخند نود طبیعی",
    "lip_velvet_cherry": "🍒 رژ مخملی گیلاسی",
    "lip_royal_wine": "🍷 رژ شرابی اشرافی تیره",
    "lip_peach_gloss": "🍑 رژ شاین هلویی براق",
    "lip_dark_gothic": "🖤 خط لب جدی و تیره",
}


# ---------------------------------------------------------------------------
# Entry Point: /start, /create
# ---------------------------------------------------------------------------

async def _start_name_wizard(
    target: Message, state: FSMContext, user: User, is_edit: bool = False
) -> None:
    """Initialize the FSM state and display the first wizard step (name selection)."""
    await state.clear()
    await state.set_state(OnboardingState.waiting_for_name)

    tg_name = (user.first_name or "مسافر")[:24]
    buttons = [
        [
            InlineKeyboardButton(
                text=f"👤 استفاده از نام تلگرام: {tg_name}",
                callback_data="ob:name_tg",
            )
        ]
    ]
    text = (
        "🍁 <b>به ایستگاه قطار شهر تیرامیکس خوش آمدی!</b>\n\n"
        "صدای باران پاییزی روی سنگ‌فرش‌های خیابان و بوی قهوه گرم کافه‌های شهر حسابی دلنشینه...\n"
        "تو مسافر جدید تیرامیکسی؛ شهری مدرن، پرجنب‌وجوش و پر از فرصت برای ساختن آینده و شهرت!\n\n"
        "برای سفارشی‌سازی و صدور شناسنامه شهروندی، مشخصاتت رو با هم کامل می‌کنیم.\n\n"
        "🏷️ <b>مرحله اول: نام شهروندی کاراکترت چیه؟</b>\n"
        "می‌تونی اسمت رو در چت بنویسی یا با دکمه زیر از نام تلگرامت استفاده کنی:"
    )
    markup = InlineKeyboardMarkup(inline_keyboard=buttons)
    if is_edit:
        with contextlib.suppress(Exception):
            await target.edit_text(text, reply_markup=markup)
            if hasattr(target, "message_id"):
                await state.update_data(tracked_msg_ids=[target.message_id])
            return

    sent = await target.reply(text, reply_markup=markup)
    tracked = [target.message_id]
    if hasattr(sent, "message_id"):
        tracked.append(sent.message_id)
    await state.update_data(tracked_msg_ids=tracked)


@router.message(CommandOrText(["start"], {}))
async def cmd_start_wear(message: Message, state: FSMContext) -> None:
    """Handle /start with composable Mini App payload: /start w_<b64> or wear_<b64>.

    Telegram Mini Apps opened from a menu button or glass button cannot use
    sendMessage, so the save travels via this deep link instead.
    """
    if not message.text:
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        return
    arg = parts[1]
    if arg.startswith("w_"):
        await _handle_compact_wear_payload(message, arg[len("w_"):], state=state)
        return
    if arg.startswith("wear_"):
        await _handle_wear_payload(message, arg[len("wear_"):], state=state)
        return


async def _handle_compact_wear_payload(
    message: Message, encoded: str, state: FSMContext | None = None
) -> None:
    """Decode the compact binary Mini App save payload (<64 chars) and persist it."""
    import base64

    user = message.from_user
    if user is None:
        return
    try:
        padded = encoded + "=" * (-len(encoded) % 4)
        data = base64.urlsafe_b64decode(padded)
        if not data:
            return
        header = data[0]
        is_male = 1 if ((header >> 7) & 1) else 0
        is_plus = 1 if ((header >> 6) & 1) else 0
        skin_idx = header & 0x3F
        var_ids = []
        for i in range(1, len(data), 3):
            chunk = data[i : i + 3]
            if len(chunk) == 3:
                var_ids.append((chunk[0] << 16) | (chunk[1] << 8) | chunk[2])

        if is_plus:
            skin_map = {1: -7, 2: -8, 3: -9, 4: -10, 5: -11, 6: -6}
            skin_tone_id = skin_map.get(skin_idx, -7)
        else:
            skin_map = {1: -1, 2: -2, 3: -3, 4: -4, 5: -5}
            skin_tone_id = skin_map.get(skin_idx, -1)

        player = await hydrate(user.id, user.full_name, user.username)
        fsm_state = (await state.get_state()) if state else None
        is_new_player = (player.onboarding_completed == 0) or (fsm_state is not None)

        await avatar.save_custom_avatar(
            user_id=user.id,
            var_ids=var_ids,
            skin_tone=skin_tone_id,
            is_plus_size=is_plus,
        )

        if is_new_player:
            fsm_data = {}
            if state and hasattr(state, "get_data"):
                res = state.get_data()
                if asyncio.iscoroutine(res) or hasattr(res, "__await__"):
                    res = await res
                if isinstance(res, dict):
                    fsm_data = res

            raw_name = (
                fsm_data.get("name")
                or player.display_name
                or getattr(user, "first_name", "مسافر")
                or "مسافر"
            )
            name = str(raw_name) if isinstance(raw_name, str) else "مسافر"
            name = name[:30]

            gender = (
                ("male" if is_male else "female")
                if is_male or not fsm_data.get("gender")
                else fsm_data.get("gender", "female")
            )
            gender_fa = "دختر" if gender in ("female", "دختر") else "پسر"
            raw_age = fsm_data.get("age") or player.age or 20
            try:
                age = int(raw_age)
            except (ValueError, TypeError):
                age = 20

            body_stance = (
                f"base_vn_{skin_idx}" if gender == "female" else f"base_boy_{skin_idx}"
            )
            player = await game.complete_character_creation(
                user_id=user.id,
                display_name=name,
                gender=gender_fa,
                age=age,
                skin_tone=str(skin_tone_id),
                eye_color="amber",
                body_stance=body_stance,
                hair_style="hair1",
                hair_color="black",
            )
            tracked_msg_ids = list(fsm_data.get("tracked_msg_ids", []))
            bot_instance = getattr(message, "bot", None)
            if bot_instance and tracked_msg_ids:
                for mid in set(tracked_msg_ids):
                    with contextlib.suppress(Exception):
                        await bot_instance.delete_message(
                            chat_id=message.chat.id, message_id=mid
                        )
            if state:
                await state.clear()

        render_url = await avatar.get_player_render_url(user.id)
        photo_payload = await avatar.fetch_upscaled_avatar(render_url, scale=2)

        custom_url = await avatar.get_customizer_url(user.id)
        if is_new_player:
            caption = (
                f"🎉 <b>شناسنامه شهروندی تیرامیکس صادر شد!</b> 🍁\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                f"👤 <b>نام:</b> {esc(player.display_name)}\n"
                f"🎂 <b>سن:</b> {player.age} سال | ⚧ <b>جنسیت:</b> {player.gender}\n"
                f"🧍 <b>فرم بدنه:</b> {'پلاس' if is_plus else 'اسلیم'}\n"
                f"👕 <b>پوشش اولیه:</b> {len(var_ids)} قلم لباس انتخابی خودت\n"
                f"💰 <b>سرمایه اولیه:</b> ۲۵۰ سکه هدیه ورود به شهر\n\n"
                "✨ <b>کاراکتر شما با موفقیت ساخته شد و آماده زندگی در تیرامیکس است!</b>\n\n"
                "🚀 <b>گام‌های بعدی برای شروع بازی:</b>\n"
                "• برای کسب اولین درآمد شهری، روی <b>«💼 رفتن سر کار»</b> بزنید.\n"
                "• برای آشنایی با تمام قابلیت‌های بازی، <b>«📜 راهنمای کامل»</b> را ببینید.\n"
                "• برای خرید ست‌های جدید لباس، به <b>«🛍 بوتیک مد»</b> سر بزنید."
            )
            markup = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="💼 رفتن سر کار (شروع درآمد)", callback_data="act:work"
                        ),
                        InlineKeyboardButton(
                            text="📜 راهنمای کامل شهر", callback_data="help:main"
                        ),
                    ],
                    [
                        InlineKeyboardButton(
                            text="🛍 ویترین بوتیک و مد",
                            callback_data=(
                                "btq:cat:female"
                                if player.gender == "دختر"
                                else "btq:cat:male"
                            ),
                        ),
                        InlineKeyboardButton(text="🎒 کمد لباس من", callback_data="wdr:view:0"),
                    ],
                    [
                        InlineKeyboardButton(
                            text="🎨 ویرایش مجدد در مینی‌اپ",
                            web_app=WebAppInfo(url=custom_url),
                        )
                    ],
                ]
            )
        else:
            caption = (
                "✅ <b>استایل کاراکتر از مینی‌اپ ثبت شد!</b>\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                f"👕 <b>قطعات:</b> {len(var_ids)} قلم\n"
                f"🧍 <b>فرم بدنه:</b> {'پلاس' if is_plus else 'اسلیم'}"
            )
            markup = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="🎨 باز کردن کاستومایز",
                            web_app=WebAppInfo(url=custom_url),
                        )
                    ],
                    [InlineKeyboardButton(text="👕 کمد لباس", callback_data="wdr:view:0")],
                    [InlineKeyboardButton(text="🚪 بستن", callback_data="btq:close")],
                ]
            )

        if photo_payload is None:
            logger.warning("w_ save: render fetch failed for %s", user.id)
            await message.reply(caption, reply_markup=markup)
        else:
            await message.reply_photo(photo=photo_payload, caption=caption, reply_markup=markup)
    except Exception as exc:  # noqa: BLE001
        logger.warning("compact wear_ deep link failed: %s", exc)


async def _handle_wear_payload(
    message: Message, encoded: str, state: FSMContext | None = None
) -> None:
    """Decode the Mini App save payload and persist it as the user's avatar."""
    import base64
    import json as _json

    user = message.from_user
    if user is None or not message.from_user:
        return
    try:
        # base64 UTF-8: تابع منبع payload در مینی‌اپ است
        padded = encoded + "=" * (-len(encoded) % 4)
        payload = _json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
        var_ids = [int(x) for x in payload.get("items", [])]
        skin_idx = int(payload.get("skin", 1))
        body = str(payload.get("body", "slim"))
        is_plus = 1 if body == "plus" else 0

        if is_plus:
            skin_map = {1: -7, 2: -8, 3: -9, 4: -10, 5: -11, 6: -6}
            skin_tone_id = skin_map.get(skin_idx, -7)
        else:
            skin_map = {1: -1, 2: -2, 3: -3, 4: -4, 5: -5}
            skin_tone_id = skin_map.get(skin_idx, -1)

        # hydrate + ensure_player: اگر کاربر تازه‌کار باشد هم ردیف بازی ساخته می‌شود
        player = await hydrate(user.id, user.full_name, user.username)
        fsm_state = (await state.get_state()) if state else None
        is_new_player = (player.onboarding_completed == 0) or (fsm_state is not None)

        await avatar.save_custom_avatar(
            user_id=user.id,
            var_ids=var_ids,
            skin_tone=skin_tone_id,
            is_plus_size=is_plus,
        )

        if is_new_player:
            fsm_data = {}
            if state and hasattr(state, "get_data"):
                res = state.get_data()
                if asyncio.iscoroutine(res) or hasattr(res, "__await__"):
                    res = await res
                if isinstance(res, dict):
                    fsm_data = res

            raw_name = (
                fsm_data.get("name")
                or player.display_name
                or getattr(user, "first_name", "مسافر")
                or "مسافر"
            )
            name = str(raw_name) if isinstance(raw_name, str) else "مسافر"
            name = name[:30]

            payload_gen = payload.get("gen")
            if payload_gen:
                gender = "female" if (payload_gen == "female" or "دختر" in payload_gen) else "male"
            else:
                gender = fsm_data.get("gender") or (
                    "female" if "دختر" in player.gender else "male"
                )
            gender_fa = "دختر" if gender in ("female", "دختر") else "پسر"
            raw_age = fsm_data.get("age") or player.age or 20
            try:
                age = int(raw_age)
            except (ValueError, TypeError):
                age = 20

            body_stance = (
                f"base_vn_{skin_idx}" if gender == "female" else f"base_boy_{skin_idx}"
            )
            player = await game.complete_character_creation(
                user_id=user.id,
                display_name=name,
                gender=gender_fa,
                age=age,
                skin_tone=str(skin_tone_id),
                eye_color="amber",
                body_stance=body_stance,
                hair_style="hair1",
                hair_color="black",
            )
            tracked_msg_ids = list(fsm_data.get("tracked_msg_ids", []))
            bot_instance = getattr(message, "bot", None)
            if bot_instance and tracked_msg_ids:
                for mid in set(tracked_msg_ids):
                    with contextlib.suppress(Exception):
                        await bot_instance.delete_message(
                            chat_id=message.chat.id, message_id=mid
                        )
            if state:
                await state.clear()

        render_url = await avatar.get_player_render_url(user.id)
        photo_payload = await avatar.fetch_upscaled_avatar(render_url, scale=2)

        custom_url = await avatar.get_customizer_url(user.id)
        if is_new_player:
            caption = (
                f"🎉 <b>شناسنامه شهروندی تیرامیکس صادر شد!</b> 🍁\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                f"👤 <b>نام:</b> {esc(player.display_name)}\n"
                f"🎂 <b>سن:</b> {player.age} سال | ⚧ <b>جنسیت:</b> {player.gender}\n"
                f"🧍 <b>فرم بدنه:</b> {'پلاس' if is_plus else 'اسلیم'}\n"
                f"👕 <b>پوشش اولیه:</b> {len(var_ids)} قلم لباس انتخابی خودت\n"
                f"💰 <b>سرمایه اولیه:</b> ۲۵۰ سکه هدیه ورود به شهر\n\n"
                "✨ <b>کاراکتر شما با موفقیت ساخته شد و آماده زندگی در تیرامیکس است!</b>\n\n"
                "🚀 <b>گام‌های بعدی برای شروع بازی:</b>\n"
                "• برای کسب اولین درآمد شهری، روی <b>«💼 رفتن سر کار»</b> بزنید.\n"
                "• برای آشنایی با تمام قابلیت‌های بازی، <b>«📜 راهنمای کامل»</b> را ببینید.\n"
                "• برای خرید ست‌های جدید لباس، به <b>«🛍 بوتیک مد»</b> سر بزنید."
            )
            markup = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="💼 رفتن سر کار (شروع درآمد)", callback_data="act:work"
                        ),
                        InlineKeyboardButton(
                            text="📜 راهنمای کامل شهر", callback_data="help:main"
                        ),
                    ],
                    [
                        InlineKeyboardButton(
                            text="🛍 ویترین بوتیک و مد",
                            callback_data=(
                                "btq:cat:female"
                                if player.gender == "دختر"
                                else "btq:cat:male"
                            ),
                        ),
                        InlineKeyboardButton(text="🎒 کمد لباس من", callback_data="wdr:view:0"),
                    ],
                    [
                        InlineKeyboardButton(
                            text="🎨 ویرایش مجدد در مینی‌اپ",
                            web_app=WebAppInfo(url=custom_url),
                        )
                    ],
                ]
            )
        else:
            caption = (
                "✅ <b>استایل کاراکتر از مینی‌اپ ثبت شد!</b>\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                f"👕 <b>قطعات:</b> {len(var_ids)} قلم\n"
                f"🧍 <b>فرم بدنه:</b> {'پلاس' if is_plus else 'اسلیم'}"
            )
            markup = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="🎨 باز کردن کاستومایز",
                            web_app=WebAppInfo(url=custom_url),
                        )
                    ],
                    [InlineKeyboardButton(text="👕 کمد لباس", callback_data="wdr:view:0")],
                    [InlineKeyboardButton(text="🚪 بستن", callback_data="btq:close")],
                ]
            )

        if photo_payload is None:
            # رندر کلود از دسترس خارج بود؛ استایل ثبت شد ولی عکس نمی‌فرستیم
            logger.warning("wear_ save: render fetch failed for %s", user.id)
            await message.reply(caption, reply_markup=markup)
        else:
            await message.reply_photo(photo=photo_payload, caption=caption, reply_markup=markup)
    except Exception as exc:  # noqa: BLE001
        logger.warning("wear_ deep link failed: %s", exc)


@router.message(
    CommandOrText(
        ["start", "create", "new_char"],
        {"شروع", "استارت", "شخصیت جدید", "ساخت", "تغییر چهره"},
    )
)
async def cmd_start(message: Message, state: FSMContext) -> None:
    user = message.from_user
    if user is None:
        return

    try:
        player = await hydrate(user.id, user.full_name, user.username)
        is_recreation = message.text and any(
            t in message.text.lower()
            for t in ("create", "new_char", "شخصیت جدید", "ساخت", "تغییر چهره")
        )
        # بررسی وضعیت ذخیره کاراکتر و جلوگیری از ساخت مکرر
        if player.onboarding_completed == 1:
            if not is_recreation:
                # کاربر کاراکتر فعال دارد؛ با /start مستقیماً پروفایل و شناسنامه‌اش لود می‌شود
                await _send_profile(message, player)
                return

            # کاربر با داشتن کاراکتر، دستور ساخت مجدد فرستاده است (/create یا ساخت)
            # شناسنامه شهروندی قفل است و فقط در صورت دسترسی مدیریتی مجاز است
            if not is_admin_or_owner(user.id):
                await message.reply(
                    "⚠️ <b>شهروند گرامی، شناسنامه شما قفل است و قبلاً ثبت شده است!</b>\n\n"
                    "📜 شناسنامه هویتی شما در شهرداری تیرامیکس صادر شده و دائمی است.\n"
                    "🔹 هر کاربر در بازی فقط یک کاراکتر پایدار دارد.\n"
                    "🔹 برای تغییر استایل و لباس‌ها از <code>بوتیک</code> استفاده کنید.\n"
                    "🔹 برای تغییر مدل مو و چهره به <code>آرایشگاه</code> مراجعه کنید.\n"
                    "🔹 جهت مشاهده وضعیت خود، کلمه <code>پروفایل</code> را ارسال کنید."
                )
                return

        # Check mandatory channel subscriptions before entering wizard
        bot = getattr(message, "bot", None)
        missing = await get_missing_channels(bot, user.id)
        if missing:
            markup = channel_join_markup(missing)
            text = (
                "🍁 <b>به بازی شبیه‌ساز زندگی تیرامیکس (Igris Life) خوش آمدید!</b>\n\n"
                "مسافر گرامی، قطار به ایستگاه شهر تیرامیکس نزدیک می‌شود...\n"
                "برای ورود به شهر و دریافت شناسنامه شهروندی،\n"
                "ابتدا باید در کانال‌های اسپانسر زیر عضو شوید.\n\n"
                "<i>از دکمه‌های زیر عضو شوید، سپس دکمه تایید را لمس کنید:</i>"
            )
            sent = await message.reply(text, reply_markup=markup, disable_web_page_preview=True)
            tracked = [message.message_id]
            if hasattr(sent, "message_id"):
                tracked.append(sent.message_id)
            await state.update_data(tracked_msg_ids=tracked)
            return

        await _start_name_wizard(message, state, user)
    except Exception as exc:  # noqa: BLE001
        await answer_error(exc, message=message)


@router.callback_query(F.data == "ob:check_channels")
async def cb_check_channels(call: CallbackQuery, state: FSMContext) -> None:
    """Verify channel memberships when user clicks the verification button."""
    user = call.from_user
    if user is None:
        return
    bot = getattr(call, "bot", None)
    missing = await get_missing_channels(bot, user.id)
    if missing:
        missing_names = "، ".join(ch.get("name", "کانال") for ch in missing)
        await call.answer(f"⚠️ هنوز در {missing_names} عضو نشده‌اید!", show_alert=True)
        text = (
            "⚠️ <b>عضویت شما در کانال‌ها هنوز کامل نشده است!</b>\n\n"
            "برای ورود به شهر و ساخت کاراکتر، باید در تمام کانال‌های زیر عضو باشید.\n\n"
            "<i>از طریق دکمه‌های زیر عضو شوید و دوباره دکمه تایید را لمس کنید:</i>"
        )
        markup = channel_join_markup(missing)
        msg = editable_message(call)
        if msg:
            with contextlib.suppress(Exception):
                await msg.edit_text(text, reply_markup=markup, disable_web_page_preview=True)
        return

    await call.answer("✅ عضویت در کانال‌ها با موفقیت تأیید شد! 🎉")
    msg = editable_message(call)
    target = msg if msg is not None else call.message

    # اگر کاربر قبلاً کاراکتر ساخته باشد، مستقیم شناسنامه‌اش لود می‌شود و ویزارد باز نمی‌شود
    player = await hydrate(user.id, user.full_name, user.username)
    if player.onboarding_completed == 1:
        if isinstance(target, Message):
            await _send_profile(target, player)
        return

    if isinstance(target, Message):
        await _start_name_wizard(target, state, user, is_edit=True)


@router.callback_query(F.data.in_({"act:create", "panel:create"}))
async def cb_start_creation(call: CallbackQuery, state: FSMContext) -> None:
    user = call.from_user
    if user is None:
        return
    player = await hydrate(user.id, user.full_name, user.username)
    if player.onboarding_completed == 1 and not is_admin_or_owner(user.id):
        await call.answer("⚠️ هویت شما قبلاً ثبت شده و قفل است!", show_alert=True)
        return

    bot = getattr(call, "bot", None)
    missing = await get_missing_channels(bot, user.id)
    if missing:
        await call.answer("⚠️ ابتدا باید در کانال‌های رسمی بازی عضو شوید!", show_alert=True)
        text = (
            "🍁 <b>به بازی شبیه‌ساز زندگی تیرامیکس (Igris Life) خوش آمدید!</b>\n\n"
            "برای ورود به شهر و ساخت کاراکتر، ابتدا باید در کانال‌های رسمی زیر عضو شوید.\n\n"
            "<i>از دکمه‌های زیر عضو شوید، سپس دکمه تایید را لمس کنید:</i>"
        )
        markup = channel_join_markup(missing)
        msg = editable_message(call)
        if msg:
            with contextlib.suppress(Exception):
                await msg.edit_text(text, reply_markup=markup, disable_web_page_preview=True)
        return

    await call.answer()
    msg = editable_message(call)
    target = msg if msg is not None else call.message
    if isinstance(target, Message):
        await _start_name_wizard(target, state, user, is_edit=True)


# ---------------------------------------------------------------------------
# Step 1: Name -> Step 2: Age
# ---------------------------------------------------------------------------

async def _advance_to_age(target: Message, state: FSMContext, name: str) -> None:
    data = await state.get_data()
    tracked_msg_ids = list(data.get("tracked_msg_ids", []))
    if hasattr(target, "message_id"):
        tracked_msg_ids.append(target.message_id)

    await state.update_data(name=name, tracked_msg_ids=tracked_msg_ids)
    await state.set_state(OnboardingState.waiting_for_age)

    buttons = [
        [
            InlineKeyboardButton(text="🎂 ۱۸ سال", callback_data="ob:age:18"),
            InlineKeyboardButton(text="🎂 ۲۰ سال", callback_data="ob:age:20"),
        ],
        [
            InlineKeyboardButton(text="🎂 ۲۵ سال", callback_data="ob:age:25"),
            InlineKeyboardButton(text="🎂 ۳۰ سال", callback_data="ob:age:30"),
        ],
    ]
    text = (
        f"✅ نام شهروندی شما ثبت شد: <b>{esc(name)}</b>\n\n"
        "🎂 <b>مرحله دوم: سن کاراکترت چقدره؟</b>\n"
        "می‌تونی از دکمه‌های زیر انتخاب کنی یا سن دلخواهت رو در چت تایپ کنی (مثلاً ۲۲):"
    )
    markup = InlineKeyboardMarkup(inline_keyboard=buttons)
    try:
        await target.edit_text(text, reply_markup=markup)
    except Exception:
        sent = await target.reply(text, reply_markup=markup)
        if hasattr(sent, "message_id"):
            tracked_msg_ids.append(sent.message_id)
            await state.update_data(tracked_msg_ids=tracked_msg_ids)


@router.callback_query(OnboardingState.waiting_for_name, F.data == "ob:name_tg")
async def cb_name_tg(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    user = call.from_user
    name = (user.first_name or "مسافر")[:24] if user else "مسافر"
    msg = editable_message(call)
    if msg:
        await _advance_to_age(msg, state, name)


@router.message(OnboardingState.waiting_for_name)
async def msg_name(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    tracked = list(data.get("tracked_msg_ids", []))
    if hasattr(message, "message_id"):
        tracked.append(message.message_id)
        await state.update_data(tracked_msg_ids=tracked)

    text = (message.text or "").strip()
    if len(text) < 2 or len(text) > 30:
        await message.reply("⚠️ نام باید بین ۲ تا ۳۰ کاراکتر باشد. لطفاً مجدداً تایپ کنید:")
        return
    await _advance_to_age(message, state, text)


# Aliases for tests/legacy callers
cb_name_telegram = cb_name_tg
msg_name_text = msg_name


# ---------------------------------------------------------------------------
# Step 2: Age -> Step 3: Gender
# ---------------------------------------------------------------------------

async def _advance_to_gender(target: Message, state: FSMContext, age: int) -> None:
    data = await state.get_data()
    tracked_msg_ids = list(data.get("tracked_msg_ids", []))
    if hasattr(target, "message_id"):
        tracked_msg_ids.append(target.message_id)

    await state.update_data(age=age, tracked_msg_ids=tracked_msg_ids)
    await state.set_state(OnboardingState.waiting_for_gender)

    buttons = [
        [
            InlineKeyboardButton(text="👧 دختر", callback_data="ob:gen:female"),
            InlineKeyboardButton(text="👦 پسر", callback_data="ob:gen:male"),
        ]
    ]
    text = (
        f"✅ سن کاراکتر ثبت شد: <b>{age} سال</b>\n\n"
        "⚧ <b>مرحله سوم: جنسیت کاراکترت رو انتخاب کن:</b>"
    )
    markup = InlineKeyboardMarkup(inline_keyboard=buttons)
    try:
        await target.edit_text(text, reply_markup=markup)
    except Exception:
        sent = await target.reply(text, reply_markup=markup)
        if hasattr(sent, "message_id"):
            tracked_msg_ids.append(sent.message_id)
            await state.update_data(tracked_msg_ids=tracked_msg_ids)


@router.callback_query(OnboardingState.waiting_for_age, F.data.startswith("ob:age:"))
async def cb_age(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    val = int(call.data.split(":")[2])
    msg = editable_message(call)
    if msg:
        await _advance_to_gender(msg, state, val)


@router.message(OnboardingState.waiting_for_age)
async def msg_age(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    tracked = list(data.get("tracked_msg_ids", []))
    if hasattr(message, "message_id"):
        tracked.append(message.message_id)
        await state.update_data(tracked_msg_ids=tracked)

    text = (message.text or "").strip()
    if not text.isdigit() or not (15 <= int(text) <= 99):
        await message.reply("⚠️ لطفاً یک سن معتبر بین ۱۵ تا ۹۹ سال وارد کنید:")
        return
    await _advance_to_gender(message, state, int(text))


msg_age_text = msg_age


# ---------------------------------------------------------------------------
# Step 3: Gender -> Step 4: Skin Tone
# ---------------------------------------------------------------------------

@router.callback_query(OnboardingState.waiting_for_gender, F.data.startswith("ob:gen:"))
async def cb_gender(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    gender = call.data.split(":")[2]
    gender_fa = "دختر" if gender == "female" else "پسر"
    await state.update_data(gender=gender, gender_fa=gender_fa)
    await state.set_state(OnboardingState.waiting_for_skin)

    data = await state.get_data()
    name = data.get("name", (call.from_user.first_name if call.from_user else "مسافر")[:24])
    age = data.get("age", 20)

    # Starter var IDs matching selected gender (default bottom item so mannequin is clothed)
    starter_bottom = [avatar.DEFAULT_STARTER_BOTTOM.get(gender, 272755)]
    custom_url = await avatar.get_customizer_url(
        user_id=call.from_user.id if call.from_user else 0,
        display_name=name,
        gender=gender,
        level=1,
        credits=250,
        skin_tone=-1,
        is_plus_size=0,
        var_ids=starter_bottom,
    )

    buttons = [
        [
            InlineKeyboardButton(
                text="🎨 طراحی زنده در مینی‌اپ تصویری (پیشنهادی)",
                web_app=WebAppInfo(url=custom_url),
            )
        ],
        [
            InlineKeyboardButton(
                text="⚡️ انتخاب سریع تیپ اولیه (شروع فوری)",
                callback_data=f"ob:quick_sets:{gender}",
            )
        ],
        [
            InlineKeyboardButton(
                text="🎛 تنظیم دستی رنگ پوست و چهره",
                callback_data=f"ob:manual:{gender}",
            )
        ],
    ]
    text = (
        f"✅ <b>هویت ثبت شد:</b>\n"
        f"👤 نام: <b>{esc(name)}</b> | 🎂 سن: <b>{age} سال</b> | ⚧ جنسیت: <b>{gender_fa}</b>\n\n"
        "✨ <b>حالا چطور می‌خوای چهره و تیپ کاراکترت رو بسازی؟</b>\n\n"
        "🔹 <b>طراحی در مینی‌اپ (پیشنهادی):</b> باز شدن صفحه تمام‌لمسی؛ "
        "مشاهده زنده مو، چهره و لباس‌ها روی مانکن و ذخیره سریع.\n"
        "🔹 <b>انتخاب سریع تیپ:</b> ورق زدن ست‌های شیک و آماده (کژوال، استریت، کالج) و صدور فوری.\n"
        "🔹 <b>تنظیم دستی:</b> انتخاب سنتی گزینه‌ها به صورت دکمه‌ای در تلگرام."
    )
    msg = editable_message(call)
    if msg:
        await target_send(msg, text, buttons)


@router.callback_query(F.data.startswith("ob:manual:"))
async def cb_manual_steps(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    await state.set_state(OnboardingState.waiting_for_skin)
    buttons = [
        [InlineKeyboardButton(text="🌕 درجه ۱: بسیار روشن", callback_data="ob:skin:1")],
        [InlineKeyboardButton(text="🌾 درجه ۲: روشن و لطیف", callback_data="ob:skin:2")],
        [InlineKeyboardButton(text="🍑 درجه ۳: طبیعی و شاداب", callback_data="ob:skin:3")],
        [InlineKeyboardButton(text="🌰 درجه ۴: گندمی و گرم", callback_data="ob:skin:4")],
        [InlineKeyboardButton(text="🍫 درجه ۵: تیره شکلاتی", callback_data="ob:skin:5")],
    ]
    text = "🎨 <b>مرحله چهارم: رنگ پوست کاراکترت رو انتخاب کن:</b>"
    msg = editable_message(call)
    if msg:
        await target_send(msg, text, buttons)


@router.callback_query(F.data.startswith("ob:quick_sets:"))
async def cb_quick_sets(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    await _show_starter_set_step(call, state)


# ---------------------------------------------------------------------------
# Step 4: Skin -> Step 5: Eye Shape
# ---------------------------------------------------------------------------

@router.callback_query(OnboardingState.waiting_for_skin, F.data.startswith("ob:skin:"))
async def cb_skin(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    skin_num = call.data.split(":")[2]
    await state.update_data(skin_tone=skin_num)
    await state.set_state(OnboardingState.waiting_for_eye_shape)

    buttons = [
        [InlineKeyboardButton(text="🌸 مدل ۱: شاداب و گرد", callback_data="ob:eyes:1")],
        [InlineKeyboardButton(text="⚡️ مدل ۲: تیز و جسور", callback_data="ob:eyes:2")],
        [InlineKeyboardButton(text="🕊 مدل ۳: آرام و خونسرد", callback_data="ob:eyes:3")],
        [InlineKeyboardButton(text="🔥 مدل ۴: بیدارشده و حماسی", callback_data="ob:eyes:4")],
    ]
    text = (
        f"✅ رنگ پوست: <b>{SKIN_LABELS.get(skin_num, skin_num)}</b>\n\n"
        "👁 <b>مرحله پنجم: مدل و فرم چشم‌ها رو انتخاب کن:</b>"
    )
    msg = editable_message(call)
    if msg:
        await target_send(msg, text, buttons)


# ---------------------------------------------------------------------------
# Step 5: Eye Shape -> Step 6: Eye Color
# ---------------------------------------------------------------------------

@router.callback_query(OnboardingState.waiting_for_eye_shape, F.data.startswith("ob:eyes:"))
async def cb_eye_shape(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    eye_shape = call.data.split(":")[2]
    await state.update_data(eye_shape=eye_shape)
    await state.set_state(OnboardingState.waiting_for_eye_color)

    buttons = [
        [
            InlineKeyboardButton(text="💎 آبی یاقوتی", callback_data="ob:eyec:blue"),
            InlineKeyboardButton(text="🌿 سبز زمردی", callback_data="ob:eyec:green"),
        ],
        [
            InlineKeyboardButton(text="🍯 عسلی درخشان", callback_data="ob:eyec:amber"),
            InlineKeyboardButton(text="🔮 بنفش رویایی", callback_data="ob:eyec:violet"),
        ],
        [
            InlineKeyboardButton(text="🖤 مشکی پرکلاغی", callback_data="ob:eyec:black"),
        ],
    ]
    text = (
        f"✅ فرم چشم: <b>{EYE_SHAPE_LABELS.get(eye_shape, eye_shape)}</b>\n\n"
        "🎨 <b>مرحله ششم: رنگ چشم کاراکترت چیه؟</b>"
    )
    msg = editable_message(call)
    if msg:
        await target_send(msg, text, buttons)


# ---------------------------------------------------------------------------
# Step 6: Eye Color -> Step 7: Nose Selection
# ---------------------------------------------------------------------------

@router.callback_query(OnboardingState.waiting_for_eye_color, F.data.startswith("ob:eyec:"))
async def cb_eye_color(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    eye_col = call.data.split(":")[2]
    await state.update_data(eye_color=eye_col)
    await state.set_state(OnboardingState.waiting_for_nose)

    buttons = [
        [InlineKeyboardButton(text="👃 مدل ۱: طبیعی کلاسیک", callback_data="ob:nose:1")],
        [InlineKeyboardButton(text="✨ مدل ۲: قلمی و عروسکی", callback_data="ob:nose:2")],
        [InlineKeyboardButton(text="💎 مدل ۳: پیرسینگ نگین الماس", callback_data="ob:nose:3")],
        [InlineKeyboardButton(text="💍 مدل ۴: پیرسینگ حلقه نقره‌ای", callback_data="ob:nose:4")],
        [InlineKeyboardButton(text="⚡️ مدل ۵: پیرسینگ دوتایی پانک", callback_data="ob:nose:5")],
    ]
    text = (
        f"✅ رنگ چشم: <b>{EYE_COLOR_LABELS.get(eye_col, eye_col)}</b>\n\n"
        "👃 <b>مرحله هفتم: مدل و فرم بینی (دماغ) یا پیرسینگ رو انتخاب کن:</b>"
    )
    msg = editable_message(call)
    if msg:
        await target_send(msg, text, buttons)


# ---------------------------------------------------------------------------
# Step 7: Nose Selection -> Step 8: Mouth Selection
# ---------------------------------------------------------------------------

@router.callback_query(OnboardingState.waiting_for_nose, F.data.startswith("ob:nose:"))
async def cb_nose(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    nose_key = call.data.split(":")[2]
    await state.update_data(nose_style=nose_key)
    await state.set_state(OnboardingState.waiting_for_mouth)

    buttons = [
        [InlineKeyboardButton(text="😊 مدل ۱: لبخند نود طبیعی", callback_data="ob:mouth:1")],
        [InlineKeyboardButton(text="😏 مدل ۲: پوزخند مغرور", callback_data="ob:mouth:2")],
        [InlineKeyboardButton(text="🍒 مدل ۳: رژ مخملی گیلاسی", callback_data="ob:mouth:3")],
        [InlineKeyboardButton(text="😐 مدل ۴: خط لب جدی و باوقار", callback_data="ob:mouth:4")],
        [InlineKeyboardButton(text="🍷 مدل ۵: رژ شرابی اشرافی", callback_data="ob:mouth:5")],
    ]
    text = (
        f"✅ فرم بینی: <b>{NOSE_LABELS.get(nose_key, nose_key)}</b>\n\n"
        "👄 <b>مرحله هشتم: فرم لب و میمیک چهره (دهن) رو انتخاب کن:</b>"
    )
    msg = editable_message(call)
    if msg:
        await target_send(msg, text, buttons)


# ---------------------------------------------------------------------------
# Step 9: Hair Style -> Step 10: Hair Color
# ---------------------------------------------------------------------------

@router.callback_query(OnboardingState.waiting_for_hair_style, F.data.startswith("ob:hair:"))
async def cb_hair_style(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    hair_key = call.data.split(":")[2]
    await state.update_data(hair_style=hair_key)
    await state.set_state(OnboardingState.waiting_for_hair_color)

    buttons = [
        [InlineKeyboardButton(text="🖤 مشکی پرکلاغی", callback_data="ob:hairc:black")],
        [InlineKeyboardButton(text="🌪 نقره‌ای پلاتینیوم", callback_data="ob:hairc:silver")],
        [InlineKeyboardButton(text="🌰 قهوه‌ای خرمایی", callback_data="ob:hairc:brown")],
        [InlineKeyboardButton(text="🌾 بلوند پلاتینیوم", callback_data="ob:hairc:blonde")],
        [InlineKeyboardButton(text="🍷 شرابی سلطنتی", callback_data="ob:hairc:crimson")],
    ]
    text = (
        "✅ مدل مو ثبت شد.\n\n"
        "🎨 <b>مرحله دهم: رنگ موی کاراکترت چیه؟</b>"
    )
    msg = editable_message(call)
    if msg:
        await target_send(msg, text, buttons)


# ---------------------------------------------------------------------------
# Step 10: Hair Color -> Step 11: Starter Set
# ---------------------------------------------------------------------------

@router.callback_query(OnboardingState.waiting_for_hair_color, F.data.startswith("ob:hairc:"))
async def cb_hair_color(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    hair_col = call.data.split(":")[2]
    await state.update_data(hair_color=hair_col)

    await _show_starter_set_step(call, state)


async def _show_starter_set_step(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(OnboardingState.waiting_for_starter_set)
    data = await state.get_data()
    gender = data.get("gender", "male")
    starters = avatar.get_starter_sets(gender)
    if not starters:
        starters = avatar.get_starter_sets("male")

    name = data.get("name", (call.from_user.first_name if call.from_user else "مسافر")[:24])
    starter_bottom = [avatar.DEFAULT_STARTER_BOTTOM.get(gender, 272755)]
    custom_url = await avatar.get_customizer_url(
        user_id=call.from_user.id if call.from_user else 0,
        display_name=name,
        gender=gender,
        level=1,
        credits=250,
        skin_tone=-1,
        is_plus_size=0,
        var_ids=starter_bottom,
    )

    buttons = [
        [
            InlineKeyboardButton(
                text="🎨 باز کردن این بخش در مینی‌اپ تصویری",
                web_app=WebAppInfo(url=custom_url),
            )
        ]
    ]
    for s in starters:
        buttons.append(
            [InlineKeyboardButton(text=f"👕 {s['name']}", callback_data=f"ob:set:{s['id']}")]
        )

    text = (
        "👗 <b>مرحله پایانی: انتخاب استایل و تیپ اولیه:</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "یکی از ست‌های هماهنگ‌شده زیر رو به عنوان تیپ اولیه انتخاب کن:\n"
        "<i>(این ست به صورت ۱۰۰٪ رایگان به کمد لباس و تن کاراکتر شما اضافه می‌شود)</i>\n\n"
        "همچنین می‌تونی دکمه <b>«طراحی در مینی‌اپ»</b> رو بزنی و تمام اجزا رو زنده دستکاری کنی!"
    )
    msg = editable_message(call)
    if msg:
        await target_send(msg, text, buttons)


# ---------------------------------------------------------------------------
# Step 9: Finalizing & Issuing Citizen ID Card
# ---------------------------------------------------------------------------

@router.callback_query(OnboardingState.waiting_for_mouth, F.data.startswith("ob:mouth:"))
async def cb_mouth(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    mouth_key = call.data.split(":")[2]
    await state.update_data(mouth_style=mouth_key)

    data = await state.get_data()
    # If hair was already selected, proceed to starter set
    if data.get("hair_style") and data.get("hair_color"):
        await _show_starter_set_step(call, state)
        return

    await state.set_state(OnboardingState.waiting_for_hair_style)
    is_female = data.get("gender") == "female"
    hair_catalog = HAIR_STYLES_FEMALE if is_female else HAIR_STYLES_MALE

    buttons = [
        [InlineKeyboardButton(text=lbl, callback_data=f"ob:hair:{key}")]
        for key, lbl in hair_catalog.items()
    ]
    text = (
        f"✅ فرم لب و دهان: <b>{MOUTH_LABELS.get(mouth_key, mouth_key)}</b>\n\n"
        "💇 <b>مرحله نهم: مدل موی کاراکترت رو انتخاب کن:</b>"
    )
    msg = editable_message(call)
    if msg:
        await target_send(msg, text, buttons)


# ---------------------------------------------------------------------------
# Step 11: Starter Set & Character Finalization (Transparent, No Background)
# ---------------------------------------------------------------------------

@router.callback_query(OnboardingState.waiting_for_starter_set, F.data.startswith("ob:set:"))
async def cb_starter_set(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer("صدور شناسنامه و ساخت کاراکتر تیرامیکس... 🍁")
    user = call.from_user
    if user is None:
        return

    set_id_str = call.data.split(":")[2]
    try:
        chosen_set_id = int(set_id_str)
    except ValueError:
        chosen_set_id = 101

    await _finalize_character(call, state, chosen_set_id=chosen_set_id)


async def _finalize_character(
    call: CallbackQuery,
    state: FSMContext,
    chosen_set_id: int | None = None,
) -> None:
    user = call.from_user
    if user is None:
        return

    data = await state.get_data()
    name = data.get("name", user.first_name or "مسافر")[:30]
    gender = data.get("gender", "male")
    gender_fa = "دختر" if gender == "female" else "پسر"
    age = data.get("age", 20)
    skin_num = data.get("skin_tone", "3")
    eye_shape = data.get("eye_shape", "1")
    eye_color = data.get("eye_color", "blue")
    nose_style = data.get("nose_style", "1")
    mouth_style = data.get("mouth_style", "1")
    hair_style = data.get("hair_style", "hair1")
    hair_color = data.get("hair_color", "black")

    skin_map = {"1": -1, "2": -2, "3": -3, "4": -4, "5": -5, "6": -6}
    skin_tone_id = skin_map.get(str(skin_num), -1)

    if chosen_set_id is None:
        starters = avatar.get_starter_sets(gender)
        chosen_set_id = starters[0]["id"] if starters else (101 if gender == "female" else 201)

    makeup_data = {
        "nose": str(nose_style),
        "lip": str(mouth_style),
        "eye_shape": str(eye_shape),
        "eye_color": str(eye_color),
        "hair_style": str(hair_style),
        "hair_color": str(hair_color),
    }

    if gender == "female":
        body_stance = f"base_vn_{skin_num}"
        eye_style_key = f"eyes{eye_shape}_{skin_num}"
        mouth_style_key = f"mouth{mouth_style}_{skin_num}"
    else:
        body_stance = f"base_boy_{skin_num}"
        eye_style_key = str(eye_shape)
        mouth_style_key = str(mouth_style)

    player = await game.complete_character_creation(
        user_id=user.id,
        display_name=name,
        gender=gender_fa,
        age=age,
        skin_tone=str(skin_tone_id),
        eye_color=eye_color,
        body_stance=body_stance,
        hair_style=hair_style,
        hair_color=hair_color,
        starter_items=(),  # No old legacy clothes!
        eye_style=eye_style_key,
        mouth_style=mouth_style_key,
        nose_style=str(nose_style),
    )

    # Assign new starter set and avatar setup (transparent PNG, no background)
    starter_res = await avatar.assign_starter_set(
        user_id=user.id,
        set_id=chosen_set_id,
        gender=gender,
        skin_tone=skin_tone_id,
        makeup_data=makeup_data,
    )
    render_url = starter_res["render_url"]

    await state.clear()

    hair_catalog = HAIR_STYLES_FEMALE if gender == "female" else HAIR_STYLES_MALE
    hair_name = hair_catalog.get(hair_style, hair_style)

    caption = (
        f"🍁 <b>شناسنامه شهروندی تیرامیکس صادر شد!</b>\n\n"
        f"👤 <b>نام:</b> {esc(player.display_name)}\n"
        f"🎂 <b>سن:</b> {player.age} سال | ⚧ <b>جنسیت:</b> {player.gender}\n"
        f"🎨 <b>رنگ پوست:</b> {SKIN_LABELS.get(str(skin_num), skin_num)}\n"
        f"👁 <b>چشم:</b> {EYE_SHAPE_LABELS.get(eye_shape, eye_shape)} "
        f"({EYE_COLOR_LABELS.get(eye_color, eye_color)})\n"
        f"👃 <b>بینی:</b> {NOSE_LABELS.get(nose_style, nose_style)}\n"
        f"👄 <b>لب:</b> {MOUTH_LABELS.get(mouth_style, mouth_style)}\n"
        f"💇 <b>مو:</b> {hair_name}\n"
        f"🎨 <b>رنگ مو:</b> {HAIR_COLOR_LABELS.get(hair_color, hair_color)}\n"
        f"👗 <b>ست لباس اولیه:</b> {starter_res['name']}\n\n"
        f"🎓 <b>سطح سواد:</b> {player.education_title}\n"
        f"💼 <b>شغل:</b> {player.job}\n"
        f"💰 <b>کیف پول:</b> ۲۵۰ سکه | 🏦 <b>بانک:</b> ۰ سکه\n\n"
        "✨ <b>کاراکتر شما با ست‌های جدید ساخته شد و بدون بک‌گراند آماده است!</b>"
    )

    chat_id = call.message.chat.id if isinstance(call.message, Message) else user.id
    bot_instance = getattr(call, "bot", None) or (
        call.message.bot if isinstance(call.message, Message) else None
    )

    tracked_msg_ids = list(data.get("tracked_msg_ids", []))
    if isinstance(call.message, Message) and hasattr(call.message, "message_id"):
        tracked_msg_ids.append(call.message.message_id)

    if bot_instance:
        for mid in set(tracked_msg_ids):
            with contextlib.suppress(Exception):
                await bot_instance.delete_message(chat_id=chat_id, message_id=mid)
    elif isinstance(call.message, Message):
        with contextlib.suppress(Exception):
            await call.message.delete()

    # Send character avatar photo upscaled 4x (Nearest-Neighbor, crisp HD)
    photo_payload: Any = render_url
    hd_bytes = await avatar.fetch_upscaled_avatar(render_url, scale=4)
    if hd_bytes:
        photo_payload = BufferedInputFile(hd_bytes, filename="citizen_avatar.png")

    custom_url = await avatar.get_customizer_url(user.id)
    final_markup = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="💼 رفتن سر کار (شروع درآمد)", callback_data="act:work"
                ),
                InlineKeyboardButton(
                    text="📜 راهنمای کامل شهر", callback_data="help:main"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🛍 ویترین بوتیک و مد",
                    callback_data=(
                        "btq:cat:female"
                        if player.gender == "دختر"
                        else "btq:cat:male"
                    ),
                ),
                InlineKeyboardButton(text="🎒 کمد لباس من", callback_data="wdr:view:0"),
            ],
            [
                InlineKeyboardButton(
                    text="🎨 کمد و شخصی‌سازی در مینی‌اپ",
                    web_app=WebAppInfo(url=custom_url),
                )
            ],
        ]
    )

    sent_photo = False
    if bot_instance:
        try:
            await bot_instance.send_photo(
                chat_id=chat_id,
                photo=photo_payload,
                caption=caption,
                reply_markup=final_markup,
            )
            sent_photo = True
        except Exception as e:
            logger.error("Failed to send avatar photo via bot: %s", e)

    if not sent_photo and isinstance(call.message, Message):
        if hasattr(call.message, "answer_photo"):
            try:
                await call.message.answer_photo(
                    photo=photo_payload,
                    caption=caption,
                    reply_markup=final_markup,
                )
                sent_photo = True
            except Exception as e:
                logger.error("Failed to answer_photo fallback: %s", e)
        elif hasattr(call.message, "reply_photo"):
            try:
                await call.message.reply_photo(
                    photo=photo_payload,
                    caption=caption,
                    reply_markup=final_markup,
                )
                sent_photo = True
            except Exception as e:
                logger.error("Failed to reply_photo fallback: %s", e)


async def target_send(
    target: Message, text: str, buttons: list[list[InlineKeyboardButton]]
) -> None:
    markup = InlineKeyboardMarkup(inline_keyboard=buttons)
    try:
        await target.edit_text(text, reply_markup=markup)
    except Exception:
        await target.reply(text, reply_markup=markup)
