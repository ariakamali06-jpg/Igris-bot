"""Everskies Boutique & Wardrobe handlers.

Provides:
- Daily shop carousel with single-message `edit_message_media` (zero chat spam).
- Instant preview on player's avatar skin tone before buying.
- Category filters (female, male, unisex) defaulting to player's gender.
- Personal wardrobe (/wardrobe) to browse owned sets, equip, and pick skin tones.
"""

from __future__ import annotations

import contextlib
import json
import logging
from typing import Any

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    Message,
    WebAppInfo,
)

from config import settings
from handlers.common import hydrate
from models.player import Player
from services import avatar, game

logger = logging.getLogger(__name__)

router = Router(name="boutique")

BOUTIQUE_COMMANDS = {"بوتیک", "ویترین", "شاپ_لباس", "boutique", "fashion"}
WARDROBE_COMMANDS = {"کمد", "کمد_لباس", "لباسهام", "wardrobe", "closet"}
AVATAR_COMMANDS = {"آواتار", "ظاهر", "پوست", "avatar"}
CUSTOMIZER_COMMANDS = {"کاستومایز", "شخصی_سازی", "طراحی_کاراکتر", "مینی_اپ", "customizer", "app"}

GENDER_LABELS = {
    "female": "🚺 دخترانه",
    "male": "🚹 پسرانه",
    "unisex": "⚧ اسپرت",
}


def _get_player_default_gender(player: Player) -> str:
    """Map player gender string to catalog category."""
    p_gen = (player.gender or "").lower()
    if any(k in p_gen for k in ("مرد", "پسر", "boy", "male")):
        return "male"
    if any(k in p_gen for k in ("زن", "دختر", "girl", "female")):
        return "female"
    return "unisex"


async def _render_photo_payload(url: str, filename: str = "avatar.png") -> Any:
    """Fetch and 4x upscale avatar PNG in-memory, falling back to raw URL on failure."""
    hd_bytes = await avatar.fetch_upscaled_avatar(url, scale=4)
    if hd_bytes:
        return BufferedInputFile(hd_bytes, filename=filename)
    return url


# -----------------------------------------------------------------------------
# Card Builders
# -----------------------------------------------------------------------------

async def _build_boutique_card(
    user_id: int,
    gender: str = "female",
    index: int = 0,
    is_preview: bool = False,
) -> tuple[str, str, InlineKeyboardMarkup]:
    """Generate media URL, caption, and keyboard for a boutique set."""
    sets = avatar.get_daily_boutique(gender=gender)
    if not sets:
        empty_url = avatar.get_render_url([-1])
        empty_caption = (
            "🛍 <b>ویترین روزانه بوتیک</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "امروز هیچ ستی در این دسته‌بندی موجود نیست!\n"
            "لطفاً دسته‌بندی دیگری را امتحان کنید."
        )
        markup = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="🚺 زنانه", callback_data="btq:cat:female"),
                    InlineKeyboardButton(text="🚹 مردانه", callback_data="btq:cat:male"),
                    InlineKeyboardButton(text="⚧ اسپرت", callback_data="btq:cat:unisex"),
                ],
                [
                    InlineKeyboardButton(text="🔙 راهنما", callback_data="help:main"),
                    InlineKeyboardButton(text="🏠 کارت من", callback_data="act:me"),
                    InlineKeyboardButton(text="🚪 بستن", callback_data="btq:close"),
                ],
            ]
        )
        return empty_url, empty_caption, markup

    total = len(sets)
    idx = max(0, min(index, total - 1))
    current_set = sets[idx]
    set_id = current_set["id"]

    player_av = await avatar.get_player_avatar(user_id)
    is_owned = await avatar.owns_set(user_id, set_id)

    # Determine render image
    if is_preview:
        render_url = avatar.get_render_url(
            current_set["var_ids"] + [player_av["skin_tone"]],
        )
        preview_notice = "\n<i>👤 در حال پیش‌نمایش زنده روی پوست کاراکتر شما</i>\n"
    else:
        render_url = current_set["render_url"]
        preview_notice = ""

    # Status / ownership notice
    if is_owned:
        if player_av["active_set_id"] == set_id:
            status_text = "👕 <b>این ست هم‌اکنون بر تن شماست.</b>"
        else:
            status_text = "✅ <b>این ست در کمد لباس شما موجود است.</b>"
    else:
        status_text = f"💰 <b>قیمت ست:</b> {current_set['price']:,} سکه"

    caption = (
        f"🛍 <b>بوتیک خیابانی مد و لباس روزانه</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"👗 <b>نام ست:</b> {current_set['name']}\n"
        f"📦 <b>تعداد اقلام:</b> {current_set['item_count']} تکه لباس\n"
        f"⚧ <b>دسته‌بندی:</b> {GENDER_LABELS.get(current_set['gender'], current_set['gender'])}\n"
        f"{status_text}\n"
        f"{preview_notice}"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"<i>ست {idx + 1} از {total} • به‌روزرسانی خودکار ساعت ۰۰:۰۰ بامداد</i>"
    )

    # Keyboard layout
    prev_idx = (idx - 1) % total
    next_idx = (idx + 1) % total
    prev_val = 1 if is_preview else 0

    nav_row = [
        InlineKeyboardButton(
            text="⬅️ قبلی", callback_data=f"btq:nav:{gender}:{prev_idx}:{prev_val}"
        ),
        InlineKeyboardButton(text=f"[{idx + 1} / {total}]", callback_data="noop"),
        InlineKeyboardButton(
            text="بعدی ➡️", callback_data=f"btq:nav:{gender}:{next_idx}:{prev_val}"
        ),
    ]

    # Action row: Buy or Equip
    if is_owned:
        if player_av["active_set_id"] == set_id:
            action_btn = InlineKeyboardButton(
                text="✅ در حال حاضر پوشیده‌اید", callback_data="noop"
            )
        else:
            action_btn = InlineKeyboardButton(
                text="👕 تن زدن این ست",
                callback_data=f"btq:equip:{gender}:{idx}:{set_id}",
            )
    else:
        action_btn = InlineKeyboardButton(
            text=f"🛒 خرید این ست ({current_set['price']:,} سکه)",
            callback_data=f"btq:buy:{gender}:{idx}:{set_id}",
        )

    # Preview toggle row
    if is_preview:
        preview_btn = InlineKeyboardButton(
            text="🔙 مانکن ست",
            callback_data=f"btq:prev:{gender}:{idx}:0",
        )
    else:
        preview_btn = InlineKeyboardButton(
            text="👁 پرو روی کاراکتر خودم",
            callback_data=f"btq:prev:{gender}:{idx}:1",
        )

    # چیدمان تمیز، مینیمال و استاندارد دکمه‌های بوتیک جهت جلوگیری از سردرگمی بازیکن
    act_preview_row = [action_btn, preview_btn]

    # تب‌های انتخاب دسته‌بندی جنسیت
    f_mark = "• 🚺 دخترانه •" if gender == "female" else "🚺 دخترانه"
    m_mark = "• 🚹 پسرانه •" if gender == "male" else "🚹 پسرانه"
    u_mark = "• ⚧ اسپرت •" if gender == "unisex" else "⚧ اسپرت"

    cat_row = [
        InlineKeyboardButton(text=f_mark, callback_data="btq:cat:female"),
        InlineKeyboardButton(text=m_mark, callback_data="btq:cat:male"),
        InlineKeyboardButton(text=u_mark, callback_data="btq:cat:unisex"),
    ]

    extra_row = [
        InlineKeyboardButton(text="🎨 کاتالوگ مینی‌اپ", url=settings.webapp_url),
        InlineKeyboardButton(text="🧥 کمد من", callback_data="wdr:open:0"),
    ]

    bottom_row = [
        InlineKeyboardButton(text="🔙 راهنما", callback_data="help:main"),
        InlineKeyboardButton(text="🏠 کارت من", callback_data="act:me"),
        InlineKeyboardButton(text="🚪 بستن", callback_data="btq:close"),
    ]

    markup = InlineKeyboardMarkup(
        inline_keyboard=[nav_row, act_preview_row, cat_row, extra_row, bottom_row]
    )
    return render_url, caption, markup


async def _build_wardrobe_card(
    user_id: int,
    index: int = 0,
) -> tuple[str, str, InlineKeyboardMarkup]:
    """Generate media URL, caption, and keyboard for player wardrobe."""
    items = await avatar.list_wardrobe(user_id)
    player_av = await avatar.get_player_avatar(user_id)

    if not items:
        # Truly empty wardrobe: show player's live avatar (mannequin)
        render_url = await avatar.get_player_render_url(user_id)
        caption = (
            "🧥 <b>کمد لباس شخصی شما</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "کاراکتر شما در حال حاضر لباسی بر تن ندارد و هنوز ستی در کمد ذخیره نشده است!\n\n"
            "• برای طراحی لباس و تیپ اختصاصی روی <b>«📱 مینی‌اپ شخصی‌سازی»</b> بزنید.\n"
            "• برای خرید ست‌های جذاب به <b>«🛍 ویترین بوتیک»</b> سر بزنید."
        )
        custom_url = await avatar.get_customizer_url(user_id) if settings.webapp_url else ""
        buttons = []
        if custom_url:
            buttons.append(
                [
                    InlineKeyboardButton(
                        text="📱 مینی‌اپ شخصی‌سازی و طراحی لباس",
                        web_app=WebAppInfo(url=custom_url),
                    )
                ]
            )
        buttons.append(
            [
                InlineKeyboardButton(text="🛍 ویترین بوتیک مد", callback_data="btq:cat:female"),
                InlineKeyboardButton(text="🎨 انتخاب رنگ پوست", callback_data="wdr:skin_menu"),
            ]
        )
        buttons.append([InlineKeyboardButton(text="🚪 بستن کمد", callback_data="btq:close")])
        markup = InlineKeyboardMarkup(inline_keyboard=buttons)
        return render_url, caption, markup

    total = len(items)
    idx = max(0, min(index, total - 1))
    current_set = items[idx]
    set_id = current_set["set_id"]
    equipped_vars = player_av.get("equipped_var_ids", [])
    is_active = (
        player_av.get("active_set_id") == set_id
        or (bool(equipped_vars) and set(current_set["var_ids"]) == set(equipped_vars))
    )

    # Render set on player's skin (or live player avatar if currently active)
    if is_active:
        render_url = await avatar.get_player_render_url(user_id)
    else:
        skin_id = player_av["skin_tone"]
        is_plus = current_set.get("is_plus_size") or player_av.get("is_plus_size", 0)
        if is_plus:
            plus_head_map = {-7: -13, -8: -14, -9: -15, -10: -16, -11: -17, -6: -12}
            head_id = plus_head_map.get(skin_id, -13)
            render_url = avatar.get_render_url(current_set["var_ids"] + [skin_id, head_id])
        else:
            render_url = avatar.get_render_url(current_set["var_ids"] + [skin_id])

    status_str = "👕 <b>بر تن کاراکتر</b>" if is_active else "📦 در کمد لباس"

    caption = (
        f"🧥 <b>کمد لباس شخصی شما</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"👗 <b>ست:</b> {current_set['name']}\n"
        f"📦 <b>تعداد اقلام:</b> {current_set['item_count']} تکه\n"
        f"⚧ <b>دسته‌بندی:</b> {GENDER_LABELS.get(current_set['gender'], current_set['gender'])}\n"
        f"📌 <b>وضعیت:</b> {status_str}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"<i>لباس {idx + 1} از {total}</i>"
    )

    if total > 1:
        prev_idx = (idx - 1) % total
        next_idx = (idx + 1) % total
        nav_row = [
            InlineKeyboardButton(text="⬅️ قبلی", callback_data=f"wdr:nav:{prev_idx}"),
            InlineKeyboardButton(text=f"[{idx + 1} / {total}]", callback_data="noop"),
            InlineKeyboardButton(text="بعدی ➡️", callback_data=f"wdr:nav:{next_idx}"),
        ]
    else:
        nav_row = [InlineKeyboardButton(text=f"✨ {total} ست موجود", callback_data="noop")]

    # دکمه‌های تعویض لباس و مدیریت ست‌های کمد
    action_row: list[InlineKeyboardButton] = []
    if not is_active:
        action_row.append(
            InlineKeyboardButton(
                text="👕 پوشیدن این ست",
                callback_data=f"wdr:equip:{set_id}:{idx}",
            )
        )
    action_row.append(
        InlineKeyboardButton(
            text="🔄 تعویض لباس (انتخاب ست)",
            callback_data="wdr:sets_list:0",
        )
    )

    options_row = [
        InlineKeyboardButton(
            text="💇‍♀️ آرایشگاه و چهره", callback_data="sal:menu"
        ),
    ]

    if settings.webapp_url:
        custom_url = await avatar.get_customizer_url(user_id)
        options_row.insert(
            0,
            InlineKeyboardButton(
                text="📱 مینی‌اپ شخصی‌سازی",
                web_app=WebAppInfo(url=custom_url),
            ),
        )

    bottom_row = [
        InlineKeyboardButton(text="🛍 رفتن به بوتیک", callback_data="btq:cat:female"),
        InlineKeyboardButton(text="🚪 بستن کمد", callback_data="btq:close"),
    ]

    markup = InlineKeyboardMarkup(
        inline_keyboard=[nav_row, action_row, options_row, bottom_row]
    )
    return render_url, caption, markup


async def _build_outfit_switch_menu(
    user_id: int,
) -> tuple[str, str, InlineKeyboardMarkup]:
    """Generate change-outfit menu listing all available owned sets with 1-tap equip."""
    items = await avatar.list_wardrobe(user_id)
    player_av = await avatar.get_player_avatar(user_id)
    equipped_vars = player_av.get("equipped_var_ids", [])
    active_set_id = player_av.get("active_set_id")
    render_url = await avatar.get_player_render_url(user_id)

    clean_eq_set = set(int(v) for v in equipped_vars if int(v) > 0)

    caption = (
        "🔄 <b>منوی تعویض لباس و انتخاب ست</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "روی هر کدام از ست‌های زیر بزنید تا بلافاصله بر تن کاراکتر شما بنشیند:\n"
    )

    buttons: list[list[InlineKeyboardButton]] = []
    for idx, s in enumerate(items):
        s_id = s["set_id"]
        is_cur = (
            (active_set_id is not None and s_id == active_set_id)
            or (bool(clean_eq_set) and set(s.get("var_ids", [])) == clean_eq_set)
        )
        if is_cur:
            btn_text = f"✅ {s['name']} (بر تن)"
            cb = "noop"
        else:
            btn_text = f"👕 {s['name']} ({s['item_count']} تکه)"
            cb = f"wdr:equip:{s_id}:{idx}"
        buttons.append([InlineKeyboardButton(text=btn_text, callback_data=cb)])

    if settings.webapp_url:
        custom_url = await avatar.get_customizer_url(user_id)
        buttons.append(
            [
                InlineKeyboardButton(
                    text="📱 طراحی و شخصی‌سازی در مینی‌اپ",
                    web_app=WebAppInfo(url=custom_url),
                )
            ]
        )

    buttons.append([InlineKeyboardButton(text="🔙 بازگشت به کمد", callback_data="wdr:open:0")])
    return render_url, caption, InlineKeyboardMarkup(inline_keyboard=buttons)


async def _build_skin_menu(user_id: int) -> tuple[str, str, InlineKeyboardMarkup]:
    """Generate skin tone picker interface."""
    player_av = await avatar.get_player_avatar(user_id)
    render_url = await avatar.get_player_render_url(user_id)

    caption = (
        "🎨 <b>انتخاب رنگ پوست کاراکتر</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "رنگ پوست مورد علاقه خود را انتخاب کنید تا روی ظاهر کاراکتر شما اعمال شود:"
    )

    current_tone = player_av["skin_tone"]
    buttons: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []

    for tone_id, label in avatar.SKIN_TONES_SLIM:
        mark = "✓ " if tone_id == current_tone else ""
        row.append(
            InlineKeyboardButton(
                text=f"{mark}{label}",
                callback_data=f"wdr:set_skin:{tone_id}",
            )
        )
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    buttons.append([InlineKeyboardButton(text="🔙 بازگشت به کمد", callback_data="wdr:open:0")])
    return render_url, caption, InlineKeyboardMarkup(inline_keyboard=buttons)


# -----------------------------------------------------------------------------
# Message Handlers
# -----------------------------------------------------------------------------

@router.message(Command("boutique", "fashion"))
@router.message(F.text.lower().in_(BOUTIQUE_COMMANDS))
async def handle_boutique_command(message: Message) -> None:
    """Open daily boutique carousel."""
    if not message.from_user:
        return
    user_id = message.from_user.id
    player = await game.ensure_player(
        user_id=user_id,
        username=message.from_user.username or "",
        display_name=message.from_user.full_name or "مسافر",
    )
    default_gen = _get_player_default_gender(player)
    render_url, caption, markup = await _build_boutique_card(
        user_id=user_id,
        gender=default_gen,
        index=0,
        is_preview=False,
    )
    photo_payload = await _render_photo_payload(render_url, "boutique.png")
    await message.reply_photo(photo=photo_payload, caption=caption, reply_markup=markup)


@router.message(Command("wardrobe", "closet"))
@router.message(F.text.lower().in_(WARDROBE_COMMANDS))
async def handle_wardrobe_command(message: Message) -> None:
    """Open personal wardrobe carousel."""
    if not message.from_user:
        return
    user_id = message.from_user.id
    await game.ensure_player(
        user_id=user_id,
        username=message.from_user.username or "",
        display_name=message.from_user.full_name or "مسافر",
    )
    render_url, caption, markup = await _build_wardrobe_card(user_id=user_id, index=0)
    photo_payload = await _render_photo_payload(render_url, "wardrobe.png")
    sent = await message.reply_photo(photo=photo_payload, caption=caption, reply_markup=markup)
    avatar.set_user_wardrobe_message(user_id, sent.chat.id, sent.message_id)


@router.message(Command("avatar"))
@router.message(F.text.lower().in_(AVATAR_COMMANDS))
async def handle_avatar_command(message: Message) -> None:
    """Show current character avatar and styling options."""
    if not message.from_user:
        return
    user_id = message.from_user.id
    await game.ensure_player(
        user_id=user_id,
        username=message.from_user.username or "",
        display_name=message.from_user.full_name or "مسافر",
    )
    render_url = await avatar.get_player_render_url(user_id)
    photo_payload = await _render_photo_payload(render_url, "avatar.png")
    caption = (
        "👤 <b>پروفایل آواتار و استایل کاراکتر</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "برای تغییر رنگ پوست در سولاریوم یا میکاپ چهره (مو، چشم، بینی، گوش، لب) "
        "به آرایشگاه تیرامیکس مراجعه کنید."
    )
    markup = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="💇‍♀️ ورود به آرایشگاه و سولاریوم (پولی)",
                    callback_data="sal:menu",
                ),
            ],
            [
                InlineKeyboardButton(text="👕 کمد لباس من", callback_data="wdr:view:0"),
                InlineKeyboardButton(text="🛍 ویترین بوتیک", callback_data="btq:cat:female"),
            ],
            [
                InlineKeyboardButton(text="🚪 بستن", callback_data="btq:close"),
            ],
        ]
    )
    if settings.webapp_url:
        u_id = message.from_user.id if message.from_user else 0
        custom_url = await avatar.get_customizer_url(u_id)
        markup.inline_keyboard.insert(
            0,
            [
                InlineKeyboardButton(
                    text="📱 مینی‌اپ شخصی‌سازی کاراکتر",
                    web_app=WebAppInfo(url=custom_url),
                )
            ],
        )
    await message.reply_photo(photo=photo_payload, caption=caption, reply_markup=markup)


@router.message(Command("customizer", "app"))
@router.message(F.text.lower().in_(CUSTOMIZER_COMMANDS))
async def handle_customizer_command(message: Message) -> None:
    """Launch or guide user to the character customizer Mini App."""
    if not message.from_user:
        return
    user_id = message.from_user.id
    await game.ensure_player(
        user_id=user_id,
        username=message.from_user.username or "",
        display_name=message.from_user.full_name or "مسافر",
    )
    render_url = await avatar.get_player_render_url(user_id)
    photo_payload = await _render_photo_payload(render_url, "avatar.png")

    caption = (
        "👑 <b>مینی‌اپ شخصی‌سازی و تجهیز کاراکتر ایگریس</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "در این بخش می‌توانید آواتار خود را با بیش از ۹,۰۰۰ قطعه در ۵ ناحیه بدنه "
        "(سر، بالاتنه، پایین‌تنه، کفش و اکسسوری) به صورت زنده طراحی و ست کنید.\n\n"
        "✨ کلیه تغییرات بدون تاخیر روی ماکت اعمال شده و با زدن دکمه ذخیره، "
        "آواتار شما در ربات ثبت می‌گردد."
    )

    buttons: list[list[InlineKeyboardButton]] = []
    if settings.webapp_url:
        custom_url = await avatar.get_customizer_url(user_id)
        buttons.append(
            [
                InlineKeyboardButton(
                    text="📱 باز کردن مینی‌اپ شخصی‌سازی",
                    web_app=WebAppInfo(url=custom_url),
                )
            ]
        )
    buttons.append(
        [
            InlineKeyboardButton(text="👕 کمد لباس من", callback_data="wdr:view:0"),
            InlineKeyboardButton(text="🛍 ویترین بوتیک", callback_data="btq:cat:female"),
        ]
    )
    buttons.append([InlineKeyboardButton(text="🚪 بستن", callback_data="btq:close")])

    markup = InlineKeyboardMarkup(inline_keyboard=buttons)
    await message.reply_photo(photo=photo_payload, caption=caption, reply_markup=markup)


@router.message(F.web_app_data)
async def handle_customizer_webapp_data(message: Message) -> None:
    """Handle character customizer Mini App submission."""
    if not message.from_user or not message.web_app_data:
        return
    user_id = message.from_user.id
    try:
        raw_data = message.web_app_data.data
        data = json.loads(raw_data)
        var_ids = [int(x) for x in data.get("items", [])]
        skin_idx = int(data.get("skin", 1))
        body = str(data.get("body", "slim"))
        is_plus = 1 if body == "plus" else 0

        # Skin tone ID: slim 1..5 -> -1..-5, plus 1..6 -> -7..-12 or -6
        if is_plus:
            skin_map = {1: -7, 2: -8, 3: -9, 4: -10, 5: -11, 6: -6}
            skin_tone_id = skin_map.get(skin_idx, -7)
        else:
            skin_map = {1: -1, 2: -2, 3: -3, 4: -4, 5: -5}
            skin_tone_id = skin_map.get(skin_idx, -1)

        raw_name = getattr(message.from_user, "full_name", None)
        full_name = raw_name if isinstance(raw_name, str) else "مسافر"
        raw_user = getattr(message.from_user, "username", None)
        username = raw_user if isinstance(raw_user, str) else None
        player = await hydrate(user_id, full_name, username)
        is_new_player = player.onboarding_completed == 0

        custom_set = data.get("custom_set")
        await avatar.save_custom_avatar(
            user_id=user_id,
            var_ids=var_ids,
            skin_tone=skin_tone_id,
            is_plus_size=is_plus,
            custom_set=custom_set,
        )

        if is_new_player:
            payload_gen = data.get("gen")
            if payload_gen:
                gender_fa = "دختر" if (payload_gen == "female" or "دختر" in payload_gen) else "پسر"
            else:
                gender_fa = player.gender if player.gender != "نامشخص" else "پسر"
            gender_key = "female" if gender_fa == "دختر" else "male"
            body_stance = (
                f"base_vn_{skin_idx}" if gender_key == "female" else f"base_boy_{skin_idx}"
            )
            player = await game.complete_character_creation(
                user_id=user_id,
                display_name=player.display_name,
                gender=gender_fa,
                age=player.age,
                skin_tone=str(skin_tone_id),
                eye_color="amber",
                body_stance=body_stance,
                hair_style="hair1",
                hair_color="black",
            )

        render_url = await avatar.get_player_render_url(user_id)
        photo_payload = await _render_photo_payload(render_url, "custom_avatar.png")

        # اگر کاربر پیام کمد بازی داشت، آن پیام را درجا با عکس جدید ویرایش کن
        bot_instance = getattr(message, "bot", None)
        wdr_msg = avatar.get_user_wardrobe_message(user_id)
        if wdr_msg and bot_instance:
            w_chat_id, w_msg_id = wdr_msg
            with contextlib.suppress(Exception):
                w_url, w_cap, w_mark = await _build_wardrobe_card(user_id=user_id, index=0)
                w_photo = await _render_photo_payload(w_url, "wardrobe.png")
                await bot_instance.edit_message_media(
                    chat_id=w_chat_id,
                    message_id=w_msg_id,
                    media=InputMediaPhoto(media=w_photo, caption=w_cap, parse_mode="HTML"),
                    reply_markup=w_mark,
                )

        custom_url = await avatar.get_customizer_url(user_id)
        if is_new_player:
            caption = (
                f"🎉 <b>شناسنامه شهروندی تیرامیکس صادر شد!</b> 🍁\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                f"👤 <b>نام:</b> {player.display_name}\n"
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
                "✨ <b>استایل جدید کاراکتر شما با موفقیت ثبت شد!</b>\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                f"👕 <b>تعداد قطعات بر تن:</b> {len(var_ids)} قلم\n"
                f"🧍 <b>فرم بدنه:</b> {'پلاس' if is_plus else 'اسلیم'}\n\n"
                "استایل شما در تمامی مبارزات، رویدادها و پروفایل عمومی بازی "
                "با این ظاهر نمایش داده می‌شود."
            )
            markup = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="🎨 ویرایش مجدد در مینی‌اپ",
                            web_app=WebAppInfo(url=custom_url),
                        )
                    ],
                    [InlineKeyboardButton(text="👕 کمد لباس", callback_data="wdr:view:0")],
                    [InlineKeyboardButton(text="🚪 بستن", callback_data="btq:close")],
                ]
            )
        await message.reply_photo(photo=photo_payload, caption=caption, reply_markup=markup)
    except Exception as exc:
        logger.warning("Error processing customizer web_app_data: %s", exc)
        await message.reply("❌ در پردازش و ذخیره استایل شما مشکلی پیش آمد.")


# -----------------------------------------------------------------------------
# Callback Query Handlers
# -----------------------------------------------------------------------------


async def open_boutique_from_callback(call: CallbackQuery) -> None:
    """
    نمایش و باز کردن مستقیم ویترین تصویری بوتیک مدرن از طریق کال‌بک شیشه‌ای.

    اگر پیام قبلی دارای عکس باشد (کارت شناسنامه)، مدیا و کیبورد درجا ویرایش می‌شوند.
    اگر پیام قبلی متنی باشد (مانند منوی راهنما)، پیام متنی پاک شده و کارت تصویری
    جذاب بوتیک همراه با کنترل‌های کاروسل و ناوبری ارسال می‌گردد.
    """
    if not call.from_user or not call.message:
        await call.answer()
        return

    user_id = call.from_user.id
    player = await game.load_player(user_id)
    if not player:
        player = await game.ensure_player(
            user_id=user_id,
            username=call.from_user.username or "",
            display_name=call.from_user.full_name or "مسافر",
        )
    gender = _get_player_default_gender(player)
    render_url, caption, markup = await _build_boutique_card(
        user_id=user_id,
        gender=gender,
        index=0,
        is_preview=False,
    )

    is_photo_message = bool(getattr(call.message, "photo", None))
    if is_photo_message:
        try:
            await call.message.edit_media(
                media=InputMediaPhoto(media=render_url, caption=caption, parse_mode="HTML"),
                reply_markup=markup,
            )
        except TelegramBadRequest as exc:
            logger.debug("edit_media error in boutique callback: %s", exc)
            with contextlib.suppress(Exception):
                await call.message.edit_caption(caption=caption, reply_markup=markup)
    else:
        with contextlib.suppress(Exception):
            await call.message.delete()
        photo_payload = await _render_photo_payload(render_url, "boutique.png")
        await call.message.answer_photo(
            photo=photo_payload,
            caption=caption,
            reply_markup=markup,
        )
    await call.answer()


@router.callback_query(F.data.startswith("btq:"))
async def handle_boutique_callback(call: CallbackQuery) -> None:
    """Handle all boutique interactions with single-message carousel editing."""
    if not call.from_user or not call.data or not call.message:
        await call.answer()
        return

    user_id = call.from_user.id
    parts = call.data.split(":")
    action = parts[1] if len(parts) > 1 else ""

    if action in ("open", "main"):
        await open_boutique_from_callback(call)
        return

    if action == "close":
        avatar.clear_user_wardrobe_message(user_id)
        await call.answer()
        with contextlib.suppress(TelegramBadRequest):
            await call.message.delete()
        return

    if action == "cat":
        # Switch gender category
        gender = parts[2] if len(parts) > 2 else "female"
        render_url, caption, markup = await _build_boutique_card(
            user_id=user_id,
            gender=gender,
            index=0,
            is_preview=False,
        )
        try:
            await call.message.edit_media(
                media=InputMediaPhoto(media=render_url, caption=caption, parse_mode="HTML"),
                reply_markup=markup,
            )
        except TelegramBadRequest as exc:
            logger.debug("edit_media error on cat switch: %s", exc)
        await call.answer()
        return

    if action == "nav":
        # Slide through sets
        gender = parts[2] if len(parts) > 2 else "female"
        idx = int(parts[3]) if len(parts) > 3 else 0
        is_preview = bool(int(parts[4])) if len(parts) > 4 else False
        render_url, caption, markup = await _build_boutique_card(
            user_id=user_id,
            gender=gender,
            index=idx,
            is_preview=is_preview,
        )
        try:
            await call.message.edit_media(
                media=InputMediaPhoto(media=render_url, caption=caption, parse_mode="HTML"),
                reply_markup=markup,
            )
        except TelegramBadRequest as exc:
            logger.debug("edit_media error on nav: %s", exc)
        await call.answer()
        return

    if action == "prev":
        # Toggle preview on player's avatar
        gender = parts[2] if len(parts) > 2 else "female"
        idx = int(parts[3]) if len(parts) > 3 else 0
        is_preview = bool(int(parts[4])) if len(parts) > 4 else False
        render_url, caption, markup = await _build_boutique_card(
            user_id=user_id,
            gender=gender,
            index=idx,
            is_preview=is_preview,
        )
        try:
            await call.message.edit_media(
                media=InputMediaPhoto(media=render_url, caption=caption, parse_mode="HTML"),
                reply_markup=markup,
            )
        except TelegramBadRequest as exc:
            logger.debug("edit_media error on preview toggle: %s", exc)
        await call.answer()
        return

    if action == "buy":
        # Purchase set
        gender = parts[2] if len(parts) > 2 else "female"
        idx = int(parts[3]) if len(parts) > 3 else 0
        set_id = int(parts[4]) if len(parts) > 4 else 0

        player = await game.ensure_player(
            user_id=user_id,
            username=call.from_user.username or "",
            display_name=call.from_user.full_name or "مسافر",
        )

        try:
            price, s_info = await avatar.buy_boutique_set(
                user_id=user_id,
                set_id=set_id,
                player_drip=player.drip,
            )
            await call.answer(
                f"🎉 ست «{s_info['name']}» با {price:,} سکه خریداری شد و به کمد شما رفت!",
                show_alert=True,
            )
        except avatar.AlreadyOwned:
            await call.answer("شما قبلاً این ست را خریده‌اید!", show_alert=True)
        except Exception as exc:
            await call.answer(f"خطا در خرید: {exc}", show_alert=True)

        # Refresh current card
        render_url, caption, markup = await _build_boutique_card(
            user_id=user_id,
            gender=gender,
            index=idx,
            is_preview=False,
        )
        with contextlib.suppress(TelegramBadRequest):
            await call.message.edit_media(
                media=InputMediaPhoto(media=render_url, caption=caption, parse_mode="HTML"),
                reply_markup=markup,
            )
        return

    if action == "equip":
        gender = parts[2] if len(parts) > 2 else "female"
        idx = int(parts[3]) if len(parts) > 3 else 0
        set_id = int(parts[4]) if len(parts) > 4 else 0
        try:
            await avatar.equip_set(user_id, set_id)
            await call.answer("👕 ست مورد نظر با موفقیت بر تن کاراکتر شما نشست!", show_alert=True)
        except Exception as exc:
            await call.answer(f"خطا: {exc}", show_alert=True)

        render_url, caption, markup = await _build_boutique_card(
            user_id=user_id,
            gender=gender,
            index=idx,
            is_preview=True,
        )
        with contextlib.suppress(TelegramBadRequest):
            await call.message.edit_media(
                media=InputMediaPhoto(media=render_url, caption=caption, parse_mode="HTML"),
                reply_markup=markup,
            )
        return

    await call.answer()


@router.callback_query(F.data.startswith("wdr:"))
async def handle_wardrobe_callback(call: CallbackQuery) -> None:
    """Handle wardrobe interactions and skin tone selection."""
    if not call.from_user or not call.data or not call.message:
        await call.answer()
        return

    user_id = call.from_user.id
    parts = call.data.split(":")
    action = parts[1] if len(parts) > 1 else ""

    if action == "sets_list":
        render_url, caption, markup = await _build_outfit_switch_menu(user_id=user_id)
        photo_payload = await _render_photo_payload(render_url, "wardrobe.png")
        with contextlib.suppress(TelegramBadRequest):
            await call.message.edit_media(
                media=InputMediaPhoto(media=photo_payload, caption=caption, parse_mode="HTML"),
                reply_markup=markup,
            )
        await call.answer()
        return

    avatar.set_user_wardrobe_message(user_id, call.message.chat.id, call.message.message_id)

    if action in ("open", "nav", "view"):
        idx = int(parts[2]) if len(parts) > 2 else 0
        render_url, caption, markup = await _build_wardrobe_card(user_id=user_id, index=0)
        photo_payload = await _render_photo_payload(render_url, "wardrobe.png")
        with contextlib.suppress(TelegramBadRequest):
            await call.message.edit_media(
                media=InputMediaPhoto(media=photo_payload, caption=caption, parse_mode="HTML"),
                reply_markup=markup,
            )
        await call.answer()
        return

    if action == "equip":
        set_id = int(parts[2]) if len(parts) > 2 else 0
        idx = int(parts[3]) if len(parts) > 3 else 0
        try:
            await avatar.equip_set(user_id, set_id)
            await call.answer("👕 ست لباس با موفقیت بر تن کاراکتر شما نشست!", show_alert=True)
        except Exception as exc:
            await call.answer(f"خطا: {exc}", show_alert=True)

        render_url, caption, markup = await _build_wardrobe_card(user_id=user_id, index=0)
        photo_payload = await _render_photo_payload(render_url, "wardrobe.png")
        with contextlib.suppress(TelegramBadRequest):
            await call.message.edit_media(
                media=InputMediaPhoto(media=photo_payload, caption=caption, parse_mode="HTML"),
                reply_markup=markup,
            )
        return

    if action == "unequip":
        idx = int(parts[2]) if len(parts) > 2 else 0
        await avatar.unequip(user_id)
        await call.answer("لباس‌های کاراکتر درآورده شد.", show_alert=True)
        render_url, caption, markup = await _build_wardrobe_card(user_id=user_id, index=idx)
        with contextlib.suppress(TelegramBadRequest):
            await call.message.edit_media(
                media=InputMediaPhoto(media=render_url, caption=caption, parse_mode="HTML"),
                reply_markup=markup,
            )
        return

    if action in ("skin_menu", "set_skin"):
        from handlers import salon as salon_h

        render_url, caption, markup = await salon_h._build_solarium_menu(user_id)
        with contextlib.suppress(TelegramBadRequest):
            await call.message.edit_media(
                media=InputMediaPhoto(
                    media=render_url, caption=caption, parse_mode="HTML"
                ),
                reply_markup=markup,
            )
        await call.answer()
        return

    await call.answer()


@router.callback_query(F.data == "noop")
async def handle_noop_callback(call: CallbackQuery) -> None:
    """Handle inert buttons."""
    await call.answer()
