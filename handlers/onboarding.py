"""Interactive onboarding and character creation wizard for Igris."""

from __future__ import annotations

import logging
from typing import Any

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from handlers.common import (
    CommandOrText,
    answer_error,
    card_bytes,
    editable_message,
    esc,
    hydrate,
)
from handlers.panel import photo_bytes, render_panel
from handlers.profile import _profile_markup, _send_profile, _show_profile
from services import game

logger = logging.getLogger(__name__)
router = Router(name="onboarding")

STARTER_KITS: dict[str, tuple[str, tuple[str, ...]]] = {
    "street": ("شکارچی شهری", ("fitted_tee", "street_slacks")),
    "tactical": ("تکاور تاکتیکال", ("tactical_hoodie", "combat_boots")),
    "shadow": ("کارآگاه سایه", ("hunter_trench", "techwear_cargo")),
}

HAIR_LABELS: dict[str, str] = {
    "street_fade": "⚡️ فید آندرکات مانهوا (Street Fade)",
    "raven_shag": "🐺 مدل موی گرگی (Wolf Cut)",
    "hood_up": "🏹 دم‌اسبی رزمی (Ponytail)",
    "crown_of_shadows": "👑 تاج تاریکی (Crown of Shadows)",
}

HAIR_COLOR_LABELS: dict[str, str] = {
    "black": "🖤 مشکی کلاغی مانهوا",
    "silver": "🌪 نقره‌ای / سفید پلاتینیوم",
    "crimson": "🩸 زرشکی آتشین",
    "blonde": "⚡️ بلوند طلایی",
    "blue": "🌌 آبی کهکشانی",
}

EYE_LABELS: dict[str, str] = {
    "blue": "⚡️ آبی نئونی (چشم بیداری)",
    "red": "🩸 قرمز خونی (مود شکارچی)",
    "purple": "🔮 بنفش تاریکی (پادشاه سایه)",
    "gold": "👑 کهربایی طلایی (سلطنتی)",
    "default": "👁 ساده و کلاسیک",
}

SKIN_LABELS: dict[str, str] = {
    "fair": "⚪️ پوست روشن مانهوایی",
    "tan": "🌾 پوست گندمی طبیعی",
    "dark": "🏽 پوست برنزه ورزشی",
    "pale": "🌑 پوست رنگ‌پریده / مهتابی",
}

BODY_LABELS: dict[str, str] = {
    "base_male": "چابک مانهوا (مرد)",
    "base_female": "چابک رزمی (زن)",
    "base_shadow": "پیکره‌ی اثیری سایه",
    "base_street": "چابک و سرعتی",
    "base_aegis": "تنومند و تدافعی",
}


class OnboardingState(StatesGroup):
    waiting_for_name = State()
    waiting_for_gender = State()
    waiting_for_age = State()
    waiting_for_body = State()
    waiting_for_eyes = State()
    waiting_for_hair = State()
    waiting_for_hair_color = State()
    waiting_for_kit = State()


# ---------------------------------------------------------------------------
# /start, /create & entry point
# ---------------------------------------------------------------------------


@router.message(CommandOrText(["start", "create", "new_char"], {"شروع", "استارت", "شخصیت جدید", "ساخت", "تغییر چهره"}))
async def cmd_start(message: Message, state: FSMContext) -> None:
    user = message.from_user
    if user is None:
        return

    try:
        player = await hydrate(user.id, user.full_name, user.username)
        # If user is already onboarded and not explicitly asking for recreation
        is_recreation = message.text and any(
            t in message.text.lower() for t in ("create", "new_char", "شخصیت جدید", "ساخت", "تغییر چهره")
        )
        if player.onboarding_completed == 1 and not is_recreation:
            await _send_profile(message, player)
            return

        # Start Character Creation Wizard
        await state.clear()
        await state.set_state(OnboardingState.waiting_for_name)

        tg_name = (user.first_name or "شکارچی")[:24]
        buttons = [
            [
                InlineKeyboardButton(
                    text=f"👤 استفاده از نام تلگرام: {tg_name}",
                    callback_data="ob:name_tg",
                )
            ]
        ]
        text = (
            "✨ <b>به استودیوی ساخت و سفارشی‌سازی کاراکتر خوش آمدی!</b>\n\n"
            "سیستم برای فعال‌سازی شناسنامه و استایل ظاهری‌ات نیاز به چند مشخصه پایه داره.\n\n"
            "🏷️ <b>مرحله اول: نام کاراکترت چیه؟</b>\n"
            "می‌تونی نام دلخواهت رو در چت بنویسی یا با دکمه‌ی زیر از نام تلگرامت استفاده کنی:"
        )
        await message.reply(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    except Exception as exc:  # noqa: BLE001
        await answer_error(exc, message=message)


@router.callback_query(F.data.in_({"act:create", "panel:create"}))
async def cb_start_creation(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    user = call.from_user
    if user is None:
        return
    await state.clear()
    await state.set_state(OnboardingState.waiting_for_name)

    tg_name = (user.first_name or "شکارچی")[:24]
    buttons = [
        [
            InlineKeyboardButton(
                text=f"👤 استفاده از نام تلگرام: {tg_name}",
                callback_data="ob:name_tg",
            )
        ]
    ]
    text = (
        "🎨 <b>استودیو بازطراحی و سفارشی‌سازی کاراکتر!</b>\n\n"
        "می‌تونی ظاهر، مدل مو، رنگ چشم، رنگ پوست و استایل رزمی کاراکترت رو بازطراحی کنی.\n\n"
        "🏷️ <b>مرحله اول: نام کاراکترت چیه؟</b>\n"
        "می‌تونی نام جدید بنویسی یا نام فعلی تلگرامت رو انتخاب کنی:"
    )
    msg = editable_message(call)
    await render_panel(msg, text=text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))


# ---------------------------------------------------------------------------
# Step 1: Name
# ---------------------------------------------------------------------------


@router.callback_query(OnboardingState.waiting_for_name, F.data == "ob:name_tg")
async def cb_name_telegram(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    user = call.from_user
    if user is None:
        return
    name = (user.first_name or "شکارچی").strip()[:30]
    await _proceed_to_gender(call.message, state, name)


@router.message(OnboardingState.waiting_for_name)
async def msg_name_text(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if not text or len(text) < 2:
        await message.reply("لطفاً یک نام حداقل ۲ حرفی بنویس یا از دکمه نام تلگرام استفاده کن:")
        return
    name = text[:30]
    await _proceed_to_gender(message, state, name)


async def _proceed_to_gender(message: Any, state: FSMContext, name: str) -> None:
    await state.update_data(name=name)
    await state.set_state(OnboardingState.waiting_for_gender)

    buttons = [
        [
            InlineKeyboardButton(text="🗡️ مذکر (مرد)", callback_data="ob:gen:مرد"),
            InlineKeyboardButton(text="🏹 مؤنث (زن)", callback_data="ob:gen:زن"),
        ],
        [
            InlineKeyboardButton(text="🔮 سایه‌وار (نامشخص)", callback_data="ob:gen:نامشخص"),
        ],
    ]
    prompt = (
        f"✅ نام ثبت شد: <b>{esc(name)}</b>\n\n"
        "⚡️ <b>مرحله دوم: جنسیت شکارچی خودت رو انتخاب کن:</b>"
    )
    if isinstance(message, Message):
        await message.reply(prompt, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))


# ---------------------------------------------------------------------------
# Step 2: Gender
# ---------------------------------------------------------------------------


@router.callback_query(OnboardingState.waiting_for_gender, F.data.startswith("ob:gen:"))
async def cb_gender(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    gender = call.data.split(":", 2)[2]
    await state.update_data(gender=gender)
    await state.set_state(OnboardingState.waiting_for_age)

    buttons = [
        [
            InlineKeyboardButton(text="۱۸ سال", callback_data="ob:age:18"),
            InlineKeyboardButton(text="۲۲ سال", callback_data="ob:age:22"),
            InlineKeyboardButton(text="۲۶ سال", callback_data="ob:age:26"),
        ],
        [
            InlineKeyboardButton(text="۳۰ سال", callback_data="ob:age:30"),
            InlineKeyboardButton(text="♾ نامیرا (بی‌سن)", callback_data="ob:age:999"),
        ],
    ]
    prompt = (
        f"⚡️ جنسیت: <b>{esc(gender)}</b>\n\n"
        "⏳ <b>مرحله سوم: سن کاراکترت چقدره؟</b>\n"
        "(می‌تونی از گزینه‌ها انتخاب کنی یا عدد سن رو مستقیماً تایپ کنی):"
    )
    msg = editable_message(call)
    await render_panel(msg, text=prompt, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))


# ---------------------------------------------------------------------------
# Step 3: Age
# ---------------------------------------------------------------------------


@router.callback_query(OnboardingState.waiting_for_age, F.data.startswith("ob:age:"))
async def cb_age(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    try:
        age = int(call.data.split(":", 2)[2])
    except ValueError:
        age = 20
    await _proceed_to_body(call.message, state, age)


@router.message(OnboardingState.waiting_for_age)
async def msg_age_text(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    try:
        age = int(raw)
        if age < 1 or age > 9999:
            age = 20
    except ValueError:
        await message.reply("لطفاً یک عدد معتبر برای سن وارد کن یا از گزینه‌های دکمه‌ای انتخاب کن:")
        return
    await _proceed_to_body(message, state, age)


async def _proceed_to_body(message: Any, state: FSMContext, age: int) -> None:
    await state.update_data(age=age)
    await state.set_state(OnboardingState.waiting_for_body)

    data = await state.get_data()
    gender = data.get("gender", "مرد")

    if gender == "زن":
        buttons = [
            [
                InlineKeyboardButton(
                    text="⚪️ چابک رزمی (پوست روشن)",
                    callback_data="ob:body:base_female:fair",
                ),
                InlineKeyboardButton(
                    text="🌾 چابک رزمی (پوست گندمی)",
                    callback_data="ob:body:base_female:tan",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🏽 چابک رزمی (پوست برنزه)",
                    callback_data="ob:body:base_female:dark",
                ),
                InlineKeyboardButton(
                    text="🌑 چابک رزمی (پوست مهتابی)",
                    callback_data="ob:body:base_female:pale",
                ),
            ],
        ]
    elif gender == "مرد":
        buttons = [
            [
                InlineKeyboardButton(
                    text="⚪️ چابک مانهوا (پوست روشن)",
                    callback_data="ob:body:base_male:fair",
                ),
                InlineKeyboardButton(
                    text="🌾 چابک مانهوا (پوست گندمی)",
                    callback_data="ob:body:base_male:tan",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🏽 چابک مانهوا (پوست برنزه)",
                    callback_data="ob:body:base_male:dark",
                ),
                InlineKeyboardButton(
                    text="🌑 چابک مانهوا (پوست مهتابی)",
                    callback_data="ob:body:base_male:pale",
                ),
            ],
        ]
    else:
        buttons = [
            [
                InlineKeyboardButton(
                    text="🌑 پیکره‌ی اثیری سایه (تاریکی)",
                    callback_data="ob:body:base_shadow:shadow",
                )
            ],
        ]

    age_str = f"{age} سال" if age < 999 else "نامیرا"
    prompt = (
        f"⏳ سن: <b>{age_str}</b>\n\n"
        "🥋 <b>مرحله چهارم: رنگ پوست و استایل بدنی:</b>\n"
        "رنگ پوست و فرم فیزیکی دلخواهت رو انتخاب کن:"
    )
    if isinstance(message, Message):
        await render_panel(message, text=prompt, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))


# ---------------------------------------------------------------------------
# Step 4: Body & Skin Tone -> Eyes
# ---------------------------------------------------------------------------


@router.callback_query(OnboardingState.waiting_for_body, F.data.startswith("ob:body:"))
async def cb_body(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    parts = call.data.split(":")
    body_stance = parts[2]
    skin_tone = parts[3] if len(parts) > 3 else "fair"

    await state.update_data(body_stance=body_stance, skin_tone=skin_tone)
    await state.set_state(OnboardingState.waiting_for_eyes)

    buttons = [
        [
            InlineKeyboardButton(text="⚡️ آبی نئونی (چشم بیداری)", callback_data="ob:eyes:blue"),
            InlineKeyboardButton(text="🩸 قرمز خونی (مود خشم)", callback_data="ob:eyes:red"),
        ],
        [
            InlineKeyboardButton(text="🔮 بنفش سایه (پادشاه)", callback_data="ob:eyes:purple"),
            InlineKeyboardButton(text="👑 کهربایی طلایی (اژدها)", callback_data="ob:eyes:gold"),
        ],
        [
            InlineKeyboardButton(text="👁 ساده و کلاسیک", callback_data="ob:eyes:default"),
        ],
    ]
    prompt = (
        f"🎨 رنگ پوست: <b>{SKIN_LABELS.get(skin_tone, skin_tone)}</b>\n\n"
        "👁 <b>مرحله پنجم: رنگ و درخشش چشم‌ها:</b>\n"
        "چشم‌های بیداری و انرژی درونی کاراکترت چه رنگی باشن؟"
    )
    msg = editable_message(call)
    await render_panel(msg, text=prompt, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))


# ---------------------------------------------------------------------------
# Step 5: Eyes -> Hair Style
# ---------------------------------------------------------------------------


@router.callback_query(OnboardingState.waiting_for_eyes, F.data.startswith("ob:eyes:"))
async def cb_eyes(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    eye_color = call.data.split(":", 2)[2]
    await state.update_data(eye_color=eye_color)
    await state.set_state(OnboardingState.waiting_for_hair)

    buttons = [
        [
            InlineKeyboardButton(
                text="⚡️ فید آندرکات مانهوا (Street Fade)",
                callback_data="ob:hair:street_fade",
            )
        ],
        [
            InlineKeyboardButton(
                text="🐺 مدل موی گرگی (Wolf Cut)",
                callback_data="ob:hair:raven_shag",
            )
        ],
        [
            InlineKeyboardButton(
                text="🏹 دم‌اسبی رزمی (Ponytail)",
                callback_data="ob:hair:hood_up",
            )
        ],
        [
            InlineKeyboardButton(
                text="👑 تاج تاریکی (Crown of Shadows)",
                callback_data="ob:hair:crown_of_shadows",
            )
        ],
    ]
    prompt = (
        f"👁 رنگ چشم: <b>{EYE_LABELS.get(eye_color, eye_color)}</b>\n\n"
        "💇‍♂️ <b>مرحله ششم: مدل و سبک موی سر:</b>\n"
        "کدوم حالت مو معرف شخصیت توئه؟"
    )
    msg = editable_message(call)
    await render_panel(msg, text=prompt, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))


# ---------------------------------------------------------------------------
# Step 6: Hair Style -> Hair Color
# ---------------------------------------------------------------------------


@router.callback_query(OnboardingState.waiting_for_hair, F.data.startswith("ob:hair:"))
async def cb_hair(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    hair_style = call.data.split(":", 2)[2]
    await state.update_data(hair_style=hair_style)
    await state.set_state(OnboardingState.waiting_for_hair_color)

    buttons = [
        [
            InlineKeyboardButton(text="🖤 مشکی مانهوا", callback_data="ob:hairc:black"),
            InlineKeyboardButton(text="🌪 نقره‌ای / سفید", callback_data="ob:hairc:silver"),
        ],
        [
            InlineKeyboardButton(text="🩸 زرشکی آتشین", callback_data="ob:hairc:crimson"),
            InlineKeyboardButton(text="⚡️ بلوند طلایی", callback_data="ob:hairc:blonde"),
        ],
        [
            InlineKeyboardButton(text="🌌 آبی کهکشانی", callback_data="ob:hairc:blue"),
        ],
    ]
    prompt = (
        f"💇‍♂️ مدل مو: <b>{HAIR_LABELS.get(hair_style, hair_style)}</b>\n\n"
        "🎨 <b>مرحله هفتم: رنگ موی سر:</b>\n"
        "رنگ موی شکارچی‌ات چه رنگی باشه؟"
    )
    msg = editable_message(call)
    await render_panel(msg, text=prompt, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))


# ---------------------------------------------------------------------------
# Step 7: Hair Color -> Starter Kit
# ---------------------------------------------------------------------------


@router.callback_query(OnboardingState.waiting_for_hair_color, F.data.startswith("ob:hairc:"))
async def cb_hair_color(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    hair_color = call.data.split(":", 2)[2]
    await state.update_data(hair_color=hair_color)
    await state.set_state(OnboardingState.waiting_for_kit)

    buttons = [
        [
            InlineKeyboardButton(
                text="🏙️ کیت شکارچی شهری (Street Kit)",
                callback_data="ob:kit:street",
            )
        ],
        [
            InlineKeyboardButton(
                text="🪖 کیت تکاور تاکتیکال (Tactical Kit)",
                callback_data="ob:kit:tactical",
            )
        ],
        [
            InlineKeyboardButton(
                text="🧥 کیت کارآگاه سایه (Shadow Kit)",
                callback_data="ob:kit:shadow",
            )
        ],
    ]
    prompt = (
        f"🎨 رنگ مو: <b>{HAIR_COLOR_LABELS.get(hair_color, hair_color)}</b>\n\n"
        "🎒 <b>مرحله هشتم: کیت تجهیزات آغازین (Starter Kit):</b>\n"
        "اولین ست لباس و لوداوت اهدایی سیستم رو انتخاب کن:"
    )
    msg = editable_message(call)
    await render_panel(msg, text=prompt, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))


# ---------------------------------------------------------------------------
# Step 8: Starter Kit & Finalization
# ---------------------------------------------------------------------------


@router.callback_query(OnboardingState.waiting_for_kit, F.data.startswith("ob:kit:"))
async def cb_kit(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer("در حال رندر و ثبت نهایی کاراکتر مانهوا... 🔮")
    user = call.from_user
    if user is None:
        return

    kit_key = call.data.split(":", 2)[2]
    kit_label, kit_items = STARTER_KITS.get(kit_key, STARTER_KITS["street"])

    data = await state.get_data()
    name = data.get("name", (user.first_name or "شکارچی"))[:30]
    gender = data.get("gender", "نامشخص")
    age = data.get("age", 20)
    body_stance = data.get("body_stance", "base_male" if gender == "مرد" else ("base_female" if gender == "زن" else "base_shadow"))
    skin_tone = data.get("skin_tone", "fair")
    eye_color = data.get("eye_color", "blue")
    hair_style = data.get("hair_style", "street_fade")
    hair_color = data.get("hair_color", "black")

    try:
        # Commit to DB
        player = await game.complete_character_creation(
            user_id=user.id,
            display_name=name,
            gender=gender,
            age=age,
            skin_tone=skin_tone,
            eye_color=eye_color,
            body_stance=body_stance,
            hair_style=hair_style,
            hair_color=hair_color,
            starter_items=kit_items,
        )
        await state.clear()

        # Render custom character card
        photo = await card_bytes(player)
        age_str = f"{age} سال" if age < 999 else "نامیرا"
        stance_str = BODY_LABELS.get(body_stance, "چابک")
        hair_str = HAIR_LABELS.get(hair_style, hair_style)
        hair_col_str = HAIR_COLOR_LABELS.get(hair_color, hair_color)
        eye_str = EYE_LABELS.get(eye_color, eye_color)
        skin_str = SKIN_LABELS.get(skin_tone, skin_tone)

        caption = (
            f"🎉 <b>شکارچی «{esc(player.display_name)}» با موفقیت ساخته شد!</b>\n\n"
            f"⚡️ <b>شناسنامه و استایل ظاهری:</b>\n"
            f"• جنسیت: <b>{esc(player.gender)}</b>\n"
            f"• سن: <b>{age_str}</b>\n"
            f"• پوست: <b>{skin_str}</b>\n"
            f"• چشم‌ها: <b>{eye_str}</b>\n"
            f"• مدل مو: <b>{hair_str}</b> ({hair_col_str})\n"
            f"• کیت اولیه: <b>{kit_label}</b>\n\n"
            f"⚔️ قدرت: <b>{player.atk}</b> · 🛡 دفاع: <b>{player.defense}</b> · 💎 استایل: <b>{player.drip}</b>\n\n"
            "🎮 کارت هویت سفارشی تو صادر شد! هر زمان خواستی می‌تونی با دکمه‌ی «تغییر چهره و ظاهر» یا دستور <code>/create</code> ظاهرت رو دوباره تغییر بدی."
        )

        buttons = await _profile_markup(player)

        if isinstance(call.message, Message):
            try:
                await call.message.delete()
            except Exception:
                pass
            await call.message.answer_photo(
                photo=photo_bytes(photo),
                caption=caption,
                reply_markup=buttons,
            )
        else:
            await call.bot.send_photo(
                chat_id=user.id,
                photo=photo_bytes(photo),
                caption=caption,
                reply_markup=buttons,
            )
    except Exception as exc:  # noqa: BLE001
        await answer_error(exc, callback=call)
