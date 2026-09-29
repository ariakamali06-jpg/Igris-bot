"""Interactive onboarding and character creation wizard for Tiramix Life Simulator."""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from config import settings
from handlers.common import (
    CommandOrText,
    answer_error,
    card_bytes,
    editable_message,
    esc,
    hydrate,
)
from handlers.panel import photo_bytes, render_panel
from handlers.profile import _send_profile
from services import game

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
    waiting_for_hair_style = State()
    waiting_for_hair_color = State()
    waiting_for_mouth = State()


# ---------------------------------------------------------------------------
# Label Constants
# ---------------------------------------------------------------------------

SKIN_LABELS = {
    "1": "🌕 مهتابی و فوق‌العاده روشن",
    "2": "🌾 روشن و لطیف",
    "3": "🍑 طبیعی و شاداب",
    "4": "🌰 گندمی و گرم",
    "5": "🍫 تیره شکلاتی",
}

EYE_SHAPE_LABELS = {
    "1": "🌸 شاداب و گرد (مدل ۱)",
    "2": "⚡️ تیز و جسور (مدل ۲)",
    "3": "🕊 آرام و خونسرد (مدل ۳)",
    "4": "🔥 بیدارشده و حماسی (مدل ۴)",
}

EYE_COLOR_LABELS = {
    "blue": "💎 آبی یاقوتی",
    "green": "🌿 سبز زمردی",
    "amber": "🍯 عسلی درخشان",
    "violet": "🔮 بنفش رویایی",
    "black": "🖤 مشکی پرکلاغی",
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
}

MOUTH_LABELS = {
    "1": "😊 لبخند ملایم و صمیمی",
    "2": "😏 پوزخند مغرور و جذاب",
    "3": "😄 خنده شاداب و پرانرژی",
    "4": "😐 خط لب جدی و باوقار",
}


# ---------------------------------------------------------------------------
# Entry Point: /start, /create
# ---------------------------------------------------------------------------

@router.message(CommandOrText(["start", "create", "new_char"], {"شروع", "استارت", "شخصیت جدید", "ساخت", "تغییر چهره"}))
async def cmd_start(message: Message, state: FSMContext) -> None:
    user = message.from_user
    if user is None:
        return

    try:
        player = await hydrate(user.id, user.full_name, user.username)
        is_recreation = message.text and any(
            t in message.text.lower() for t in ("create", "new_char", "شخصیت جدید", "ساخت", "تغییر چهره")
        )
        if player.onboarding_completed == 1:
            if not is_recreation:
                await _send_profile(message, player)
                return
            if not is_admin_or_owner(user.id):
                await message.reply(
                    "⚠️ <b>وَخَه بینُم شهروند! شناسنامه تو قفل است و قبلاً ثبت شده!</b>\n\n"
                    "شناسنامه شهروندی تو در شهرداری تیرامیکس صادر شده و هر شخص فقط یک‌بار ثبت‌نام اولیه دارد.\n"
                    "برای تغییر ظاهر و مو از دستورات <code>آرایشگاه</code> یا <code>زیبایی</code> استفاده کن!"
                )
                return

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
        sent = await message.reply(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
        tracked = [message.message_id]
        if hasattr(sent, "message_id"):
            tracked.append(sent.message_id)
        await state.update_data(tracked_msg_ids=tracked)
    except Exception as exc:  # noqa: BLE001
        await answer_error(exc, message=message)


@router.callback_query(F.data.in_({"act:create", "panel:create"}))
async def cb_start_creation(call: CallbackQuery, state: FSMContext) -> None:
    user = call.from_user
    if user is None:
        return
    player = await hydrate(user.id, user.full_name, user.username)
    if player.onboarding_completed == 1 and not is_admin_or_owner(user.id):
        await call.answer("⚠️ هویت شما قبلاً ثبت شده و قفل است!", show_alert=True)
        return
    await call.answer()
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
    msg = editable_message(call)
    await render_panel(msg, text=text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    if msg and hasattr(msg, "message_id"):
        await state.update_data(tracked_msg_ids=[msg.message_id])


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

    buttons = [
        [InlineKeyboardButton(text="🌕 درجه ۱: مهتابی و فوق‌العاده روشن", callback_data="ob:skin:1")],
        [InlineKeyboardButton(text="🌾 درجه ۲: روشن و لطیف", callback_data="ob:skin:2")],
        [InlineKeyboardButton(text="🍑 درجه ۳: طبیعی و شاداب", callback_data="ob:skin:3")],
        [InlineKeyboardButton(text="🌰 درجه ۴: گندمی و گرم", callback_data="ob:skin:4")],
        [InlineKeyboardButton(text="🍫 درجه ۵: تیره شکلاتی", callback_data="ob:skin:5")],
    ]
    text = (
        f"✅ جنسیت: <b>{gender_fa}</b>\n\n"
        "🎨 <b>مرحله چهارم: رنگ پوست کاراکترت رو انتخاب کن:</b>"
    )
    msg = editable_message(call)
    if msg:
        await target_send(msg, text, buttons)


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
# Step 6: Eye Color -> Step 7: Hair Style
# ---------------------------------------------------------------------------

@router.callback_query(OnboardingState.waiting_for_eye_color, F.data.startswith("ob:eyec:"))
async def cb_eye_color(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    eye_col = call.data.split(":")[2]
    await state.update_data(eye_color=eye_col)
    await state.set_state(OnboardingState.waiting_for_hair_style)

    data = await state.get_data()
    is_female = data.get("gender") == "female"
    hair_catalog = HAIR_STYLES_FEMALE if is_female else HAIR_STYLES_MALE

    buttons = [
        [InlineKeyboardButton(text=lbl, callback_data=f"ob:hair:{key}")]
        for key, lbl in hair_catalog.items()
    ]
    text = (
        f"✅ رنگ چشم: <b>{EYE_COLOR_LABELS.get(eye_col, eye_col)}</b>\n\n"
        "💇 <b>مرحله هفتم: مدل موی کاراکترت رو انتخاب کن:</b>"
    )
    msg = editable_message(call)
    if msg:
        await target_send(msg, text, buttons)


# ---------------------------------------------------------------------------
# Step 7: Hair Style -> Step 8: Hair Color
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
    ]
    text = (
        "✅ مدل مو ثبت شد.\n\n"
        "🎨 <b>مرحله هشتم: رنگ موی کاراکترت چیه؟</b>"
    )
    msg = editable_message(call)
    if msg:
        await target_send(msg, text, buttons)


# ---------------------------------------------------------------------------
# Step 8: Hair Color -> Step 9: Mouth Expression
# ---------------------------------------------------------------------------

@router.callback_query(OnboardingState.waiting_for_hair_color, F.data.startswith("ob:hairc:"))
async def cb_hair_color(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    hair_col = call.data.split(":")[2]
    await state.update_data(hair_color=hair_col)
    await state.set_state(OnboardingState.waiting_for_mouth)

    buttons = [
        [InlineKeyboardButton(text="😊 مدل ۱: لبخند ملایم و صمیمی", callback_data="ob:mouth:1")],
        [InlineKeyboardButton(text="😏 مدل ۲: پوزخند مغرور و جذاب", callback_data="ob:mouth:2")],
        [InlineKeyboardButton(text="😄 مدل ۳: خنده شاداب و پرانرژی", callback_data="ob:mouth:3")],
        [InlineKeyboardButton(text="😐 مدل ۴: خط لب جدی و باوقار", callback_data="ob:mouth:4")],
    ]
    text = (
        f"✅ رنگ مو: <b>{HAIR_COLOR_LABELS.get(hair_col, hair_col)}</b>\n\n"
        "👄 <b>مرحله نهم: فرم لبخند و میمیک چهره:</b>"
    )
    msg = editable_message(call)
    if msg:
        await target_send(msg, text, buttons)


# ---------------------------------------------------------------------------
# Step 9: Finalizing & Issuing Citizen ID Card
# ---------------------------------------------------------------------------

@router.callback_query(OnboardingState.waiting_for_mouth, F.data.startswith("ob:mouth:"))
async def cb_mouth(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer("صدور شناسنامه شهروندی تیرامیکس... 🍁")
    user = call.from_user
    if user is None:
        return

    mouth_num = call.data.split(":")[2]
    data = await state.get_data()

    name = data.get("name", user.first_name or "مسافر")[:30]
    gender = data.get("gender", "male")
    gender_fa = "دختر" if gender == "female" else "پسر"
    age = data.get("age", 20)
    skin_num = data.get("skin_tone", "3")
    eye_shape = data.get("eye_shape", "1")
    eye_color = data.get("eye_color", "blue")
    hair_style = data.get("hair_style", "hair1")
    hair_color = data.get("hair_color", "black")

    if gender == "female":
        body_stance = f"base_vn_{skin_num}"
        eye_style_key = f"eyes{eye_shape}_{skin_num}"
        mouth_style_key = f"mouth{mouth_num}_{skin_num}"
    else:
        body_stance = f"base_boy_{skin_num}"
        eye_style_key = str(eye_shape)
        mouth_style_key = str(mouth_num)

    skin_tone_internal = {
        "1": "pale",
        "2": "fair",
        "3": "natural",
        "4": "tan",
        "5": "dark",
    }.get(skin_num, "fair")

    data = await state.get_data()
    tracked_msg_ids = list(data.get("tracked_msg_ids", []))
    if isinstance(call.message, Message) and hasattr(call.message, "message_id"):
        tracked_msg_ids.append(call.message.message_id)

    chosen_hair_id = {
        "hair1": "street_fade",
        "hair2": "hood_up",
        "hair3": "raven_shag",
        "hair4": "crown_of_shadows",
        "hair5": "hair_waves_blonde",
    }.get(hair_style, "street_fade")
    starter_kit = ("fitted_tee", "street_slacks", chosen_hair_id)

    player = await game.complete_character_creation(
        user_id=user.id,
        display_name=name,
        gender=gender_fa,
        age=age,
        skin_tone=skin_tone_internal,
        eye_color=eye_color,
        body_stance=body_stance,
        hair_style=hair_style,
        hair_color=hair_color,
        starter_items=starter_kit,
        eye_style=eye_style_key,
        mouth_style=mouth_style_key,
    )
    await state.clear()

    photo = await card_bytes(player)

    caption = (
        f"🍁 <b>شناسنامه شهروندی تیرامیکس صادر شد!</b>\n\n"
        f"👤 <b>نام:</b> {esc(player.display_name)}\n"
        f"🎂 <b>سن:</b> {player.age} سال | ⚧ <b>جنسیت:</b> {player.gender}\n"
        f"🎨 <b>پوست:</b> {SKIN_LABELS.get(skin_num, skin_num)}\n"
        f"👁 <b>چشم‌ها:</b> {EYE_SHAPE_LABELS.get(eye_shape, eye_shape)} ({EYE_COLOR_LABELS.get(eye_color, eye_color)})\n"
        f"💇 <b>مدل مو:</b> {(HAIR_STYLES_FEMALE if gender == 'female' else HAIR_STYLES_MALE).get(hair_style, hair_style)} ({HAIR_COLOR_LABELS.get(hair_color, hair_color)})\n"
        f"👄 <b>چهره:</b> {MOUTH_LABELS.get(mouth_num, mouth_num)}\n\n"
        f"🎓 <b>سطح سواد:</b> {player.education_title}\n"
        f"💼 <b>شغل:</b> {player.job}\n"
        f"💰 <b>کیف پول:</b> ۰ سکه | 🏦 <b>بانک:</b> ۰ سکه\n\n"
        "✨ <b>زندگی شما در شهر تیرامیکس رسماً آغاز شد!</b>"
    )

    chat_id = call.message.chat.id if isinstance(call.message, Message) else user.id
    bot_instance = getattr(call, "bot", None) or (call.message.bot if isinstance(call.message, Message) else None)

    # Clean up all messages from wizard steps to keep the chat tidy
    if bot_instance:
        for mid in set(tracked_msg_ids):
            try:
                await bot_instance.delete_message(chat_id=chat_id, message_id=mid)
            except Exception:
                pass
    elif isinstance(call.message, Message):
        try:
            await call.message.delete()
        except Exception:
            pass

    # Safely send citizenship card photo directly to chat
    sent_photo = False
    if bot_instance:
        try:
            await bot_instance.send_photo(
                chat_id=chat_id,
                photo=photo_bytes(photo),
                caption=caption,
                reply_markup=None,
            )
            sent_photo = True
        except Exception as e:
            logger.error("Failed to send citizenship card photo via bot: %s", e)

    if not sent_photo and isinstance(call.message, Message):
        if hasattr(call.message, "answer_photo"):
            try:
                await call.message.answer_photo(
                    photo=photo_bytes(photo),
                    caption=caption,
                    reply_markup=None,
                )
            except Exception as e:
                logger.error("Failed to answer_photo fallback: %s", e)
        elif hasattr(call.message, "reply_photo"):
            try:
                await call.message.reply_photo(
                    photo=photo_bytes(photo),
                    caption=caption,
                    reply_markup=None,
                )
            except Exception as e:
                logger.error("Failed to reply_photo fallback: %s", e)


async def target_send(target: Message, text: str, buttons: list[list[InlineKeyboardButton]]) -> None:
    markup = InlineKeyboardMarkup(inline_keyboard=buttons)
    try:
        await target.edit_text(text, reply_markup=markup)
    except Exception:
        await target.reply(text, reply_markup=markup)
