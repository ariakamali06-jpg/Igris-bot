"""Interactive onboarding and character creation wizard for new hunters.

When a user runs /start or sends plain "شروع" for the first time:
- Step 1: Character Name (custom input or 1-tap Telegram name)
- Step 2: Gender selection
- Step 3: Age selection
- Step 4: Body stance & skin tone
- Step 5: Hair style & visual signature
- Step 6: Starter kit selection (Urban, Tactical, Shadow)
- Final: Save to DB, composite their custom card, and show the profile hub.

On subsequent /start runs (or if already onboarded), directly opens the player card.
"""

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
    "street_fade": "فید خیابانی (Street Fade)",
    "raven_shag": "موی کلاغی سایه (Raven Shag)",
    "hood_up": "هودی کلاه‌دار مخفی (Hood Up)",
    "crown_of_shadows": "تاج تاریکی (Crown of Shadows)",
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
    waiting_for_hair = State()
    waiting_for_kit = State()


# ---------------------------------------------------------------------------
# /start & entry point
# ---------------------------------------------------------------------------


@router.message(CommandOrText(["start", "create", "new_char"], {"شروع", "استارت", "شخصیت جدید"}))
async def cmd_start(message: Message, state: FSMContext) -> None:
    user = message.from_user
    if user is None:
        return

    try:
        player = await hydrate(user.id, user.full_name, user.username)
        # If user is already onboarded and not explicitly asking for recreation
        is_recreation = message.text and any(
            t in message.text.lower() for t in ("create", "new_char", "شخصیت جدید")
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
            "✨ <b>بیداری آغاز شد! به دنیای شکارچیان ایگریس خوش آمدی.</b>\n\n"
            "سیستم برای فعال‌سازی شناسنامه و نیروی درونی‌ات نیاز به چند مشخصه پایه داره.\n\n"
            "🏷️ <b>مرحله اول: نام کاراکترت چیه؟</b>\n"
            "می‌تونی نام دلخواهت رو در چت بنویسی یا با دکمه‌ی زیر از نام تلگرامت استفاده کنی:"
        )
        await message.reply(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    except Exception as exc:  # noqa: BLE001
        await answer_error(exc, message=message)


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
                    text="🏹 استایل چابک و کماندار (پوست روشن)",
                    callback_data="ob:body:base_female:fair",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🗡️ استایل جنگجوی چابک (پوست گندمی)",
                    callback_data="ob:body:base_female:tan",
                )
            ],
        ]
    elif gender == "مرد":
        buttons = [
            [
                InlineKeyboardButton(
                    text="🏃 استایل چابک مانهوا (پوست روشن)",
                    callback_data="ob:body:base_male:fair",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🛡 استایل تانک و رزمی (پوست گندمی)",
                    callback_data="ob:body:base_aegis:tan",
                )
            ],
        ]
    else:
        buttons = [
            [
                InlineKeyboardButton(
                    text="🌑 پیکره‌ی اثیری سایه (فرمانده ارتش تاریکی)",
                    callback_data="ob:body:base_shadow:shadow",
                )
            ],
        ]

    age_str = f"{age} سال" if age < 999 else "نامیرا"
    prompt = (
        f"⏳ سن: <b>{age_str}</b>\n\n"
        "🥋 <b>مرحله چهارم: استایل فیزیکی و فیزیک بدنی:</b>\n"
        "نوع استقرار و نژاد بدنی کاراکترت رو انتخاب کن:"
    )
    if isinstance(message, Message):
        await render_panel(message, text=prompt, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))


# ---------------------------------------------------------------------------
# Step 4: Body stance & Skin tone
# ---------------------------------------------------------------------------


@router.callback_query(OnboardingState.waiting_for_body, F.data.startswith("ob:body:"))
async def cb_body(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    parts = call.data.split(":")
    body_stance = parts[2]
    skin_tone = parts[3] if len(parts) > 3 else "fair"

    await state.update_data(body_stance=body_stance, skin_tone=skin_tone)
    await state.set_state(OnboardingState.waiting_for_hair)

    buttons = [
        [
            InlineKeyboardButton(
                text="⚡️ فید خیابانی (Street Fade)",
                callback_data="ob:hair:street_fade",
            )
        ],
        [
            InlineKeyboardButton(
                text="🦅 موی کلاغی سایه (Raven Shag)",
                callback_data="ob:hair:raven_shag",
            )
        ],
        [
            InlineKeyboardButton(
                text="🥷 هودی کلاه‌دار مخفی (Hood Up)",
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
        f"🥋 فیزیک بدنی: <b>{BODY_LABELS.get(body_stance, 'استاندارد')}</b>\n\n"
        "💇‍♂️ <b>مرحله پنجم: مدل و سبک موی سر:</b>\n"
        "کدوم حالت مو معرف شخصیت توئه؟"
    )
    msg = editable_message(call)
    await render_panel(msg, text=prompt, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))


# ---------------------------------------------------------------------------
# Step 5: Hair Style
# ---------------------------------------------------------------------------


@router.callback_query(OnboardingState.waiting_for_hair, F.data.startswith("ob:hair:"))
async def cb_hair(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    hair_style = call.data.split(":", 2)[2]

    await state.update_data(hair_style=hair_style)
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
        f"💇‍♂️ مدل مو: <b>{HAIR_LABELS.get(hair_style, hair_style)}</b>\n\n"
        "🎒 <b>مرحله نهایی: کیت تجهیزات آغازین (Starter Kit):</b>\n"
        "اولین ست لباس و لوداوت اهدایی سیستم رو انتخاب کن:"
    )
    msg = editable_message(call)
    await render_panel(msg, text=prompt, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))


# ---------------------------------------------------------------------------
# Step 6: Starter Kit & Finalization
# ---------------------------------------------------------------------------


@router.callback_query(OnboardingState.waiting_for_kit, F.data.startswith("ob:kit:"))
async def cb_kit(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer("در حال ساخت شناسنامه کاراکتر... 🔮")
    user = call.from_user
    if user is None:
        return

    kit_key = call.data.split(":", 2)[2]
    kit_label, kit_items = STARTER_KITS.get(kit_key, STARTER_KITS["street"])

    data = await state.get_data()
    name = data.get("name", (user.first_name or "شکارچی"))[:30]
    gender = data.get("gender", "نامشخص")
    age = data.get("age", 20)
    body_stance = data.get("body_stance", "base_street")
    skin_tone = data.get("skin_tone", "fair")
    eye_color = data.get("eye_color", "amber")
    hair_style = data.get("hair_style", "street_fade")

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
            starter_items=kit_items,
        )
        await state.clear()

        # Render custom character card
        photo = await card_bytes(player)
        age_str = f"{age} سال" if age < 999 else "نامیرا"
        stance_str = BODY_LABELS.get(body_stance, "چابک")
        hair_str = HAIR_LABELS.get(hair_style, hair_style)

        caption = (
            f"🎉 <b>شکارچی «{esc(player.display_name)}» با موفقیت متولد شد!</b>\n\n"
            f"⚡️ <b>شناسنامه رزمی:</b>\n"
            f"• جنسیت: <b>{esc(player.gender)}</b>\n"
            f"• سن: <b>{age_str}</b>\n"
            f"• استایل بدنی: <b>{stance_str}</b>\n"
            f"• مدل مو: <b>{hair_str}</b>\n"
            f"• لوداوت اولیه: <b>{kit_label}</b>\n\n"
            f"⚔️ قدرت: <b>{player.atk}</b> · 🛡 دفاع: <b>{player.defense}</b> · 💎 استایل: <b>{player.drip}</b>\n\n"
            "🎮 کارت هویت اختصاصی تو صادر شد! از این به بعد با هر بار زدن <code>/start</code> یا <b>پروفایل</b> مستقیماً به کارت و منوی بازی دسترسی داری."
        )

        buttons = [
            [
                InlineKeyboardButton(text="🎒 کوله‌پشتی", callback_data="inv:0"),
                InlineKeyboardButton(text="🛒 فروشگاه", callback_data="shop:page:0"),
            ],
            [
                InlineKeyboardButton(text="💼 کار و حقوق", callback_data="act:work"),
                InlineKeyboardButton(text="⚔️ دوئل", callback_data="act:duel"),
            ],
            [
                InlineKeyboardButton(text="🏠 کارت من", callback_data="act:me"),
                InlineKeyboardButton(text="📜 راهنما", callback_data="act:help"),
            ],
        ]

        msg = editable_message(call)
        if msg:
            await render_panel(
                msg,
                text=caption,
                photo=photo,
                reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
                force_media=True,
            )
        else:
            await call.message.answer_photo(
                photo=photo_bytes(photo),
                caption=caption,
                reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
            )
    except Exception as exc:  # noqa: BLE001
        await answer_error(exc, callback=call)
