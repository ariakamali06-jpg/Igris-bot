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
import time
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
from database.connection import db
from handlers.common import answer_error, hydrate
from models.enums import ActivityKind
from models.player import Player
from services import avatar, economy, game

logger = logging.getLogger(__name__)

router = Router(name="boutique")


def _bot_username() -> str:
    """نام کاربری واقعی ربات برای ساختن دیپ‌لینک در گروه‌ها (بدون @ ابتدایی)."""
    return (avatar.RUNTIME_BOT_USERNAME or settings.bot_username or "").lstrip("@")


def _is_group_chat(chat_type: str) -> bool:
    """آیا چت یک گروه/سوپرگروه است؟ (توی گروه دکمه web_app ممنوع است)."""
    return chat_type in ("group", "supergroup")


def _mini_app_btn(
    text: str, url: str, in_group: bool, payload: str = "app"
) -> InlineKeyboardButton:
    """دکمه مینی‌اپ یا دیپ‌لینک امن: در گروه → لینک به پیوی ربات، در پیوی → web_app واقعی."""
    if in_group:
        username = _bot_username() or "igrisbot"
        return InlineKeyboardButton(
            text=text, url=f"https://t.me/{username}?start={payload}"
        )
    return InlineKeyboardButton(text=text, web_app=WebAppInfo(url=url))

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
        status_text = f"💰 <b>قیمت ست:</b> {current_set['price']:,} رکسار"

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
            text=f"🛒 خرید این ست ({current_set['price']:,} رکسار)",
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


async def _build_boutique_catalog(
    user_id: int,
    category: str = "top",
    page: int = 0,
    *,
    in_group: bool = False,
) -> tuple[str, str, InlineKeyboardMarkup]:
    """Generate high-efficiency item catalog for boutique with zero image bandwidth waste."""
    from services import catalog

    player = await game.load_player(user_id)
    if not player:
        player = await game.ensure_player(
            user_id=user_id,
            username="",
            display_name="مسافر",
        )

    p_gen = (player.gender or "پسر").lower()
    is_female = any(k in p_gen for k in ("دختر", "female", "girl"))
    gender_badge = "🚺 دختر" if is_female else "🚹 پسر"

    credits_bal, _ = await economy.balances(user_id)
    cat_key = category if category in catalog.BOUTIQUE_CATEGORY_MAP else "top"
    cat_data = catalog.get_daily_boutique_items(category_key=cat_key, page=page, page_size=5)

    items = cat_data["items"]
    cur_page = cat_data["page"]
    total_pages = cat_data["total_pages"]
    cat_title = cat_data["title"]

    gender_notice = (
        "✨ <b>امتیاز جنسیت شما:</b> ۴۰٪ تخفیف ویژه روی پوشاک دخترانه (۳۰۰ رکسار)!\n"
        "🔥 پوشاک پسرانه: ۱,۲۰۰ رکسار (نرخ غیرهم‌جنسیت) | ⚧ اسپرت: ۵۰۰ رکسار"
        if is_female
        else
        "✨ <b>امتیاز جنسیت شما:</b> ۴۰٪ تخفیف ویژه روی پوشاک پسرانه (۳۰۰ رکسار)!\n"
        "🔥 پوشاک دخترانه: ۱,۲۰۰ رکسار (نرخ غیرهم‌جنسیت) | ⚧ اسپرت: ۵۰۰ رکسار"
    )

    lines = [
        "🛍 <b>بوتیک خیابانی مد و لباس ایگریس (تک‌آیتم‌ها)</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        f"👤 <b>خریدار:</b> {player.display_name} ({gender_badge})",
        f"💰 <b>موجودی شما:</b> <code>{credits_bal:,}</code> رکسار",
        gender_notice,
        "━━━━━━━━━━━━━━━━━━━━",
        f"📂 <b>دسته‌بندی:</b> {cat_title}",
        f"📄 <b>صفحه {cur_page + 1} از {total_pages}</b> ({cat_data['total_items']} آیتم)",
        "",
    ]

    item_row_btns: list[InlineKeyboardButton] = []
    digits = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣"]

    for idx, it in enumerate(items):
        vid = it["var_id"]
        it_name = it["name"]
        it_gen = it.get("gender", "unisex")
        price, price_label = avatar.calculate_item_price(it_gen, player.gender)
        is_owned = await avatar.owns_item(user_id, vid)

        owned_badge = " [✅ در کمد]" if is_owned else ""
        icon = digits[idx] if idx < len(digits) else f"[{idx+1}]"

        lines.append(f"{icon} <b>{it_name}</b>{owned_badge}")
        lines.append(f"   🏷 استایل: {it_gen} • 💰 <b>{price:,}</b> رکسار ({price_label})\n")

        item_row_btns.append(
            InlineKeyboardButton(
                text=f"{icon} انتخاب",
                callback_data=f"btq:view:{vid}:{cat_key}:{cur_page}",
            )
        )

    if not items:
        lines.append("<i>امروز آیتمی در این دسته‌بندی یافت نشد.</i>")

    lines.append("━━━━━━━━━━━━━━━━━━━━")
    lines.append("💡 <i>برای دیدن مشخصات، پرو روی کاراکتر یا خرید، روی دکمه هر آیتم بزنید.</i>")

    # Keyboard layout
    custom_url = await avatar.get_customizer_url(user_id, mode="shop")
    keyboard: list[list[InlineKeyboardButton]] = [
        [
            _mini_app_btn(
                "📱 ورود به مینی‌اپ فروشگاه (Shop Mode)",
                custom_url,
                in_group,
                payload="shop",
            )
        ]
    ]
    if item_row_btns:
        keyboard.append(item_row_btns)

    # Category tabs
    t_top = "• 👕 بالاتنه •" if cat_key == "top" else "👕 بالاتنه"
    t_bot = "• 👖 پایین‌تنه •" if cat_key == "bottom" else "👖 پایین‌تنه"
    t_sh = "• 👟 کفش •" if cat_key == "shoes" else "👟 کفش"
    t_acc = "• 🎒 اکسسوری •" if cat_key == "acc" else "🎒 اکسسوری"

    keyboard.append([
        InlineKeyboardButton(text=t_top, callback_data="btq:cat:top"),
        InlineKeyboardButton(text=t_bot, callback_data="btq:cat:bottom"),
    ])
    keyboard.append([
        InlineKeyboardButton(text=t_sh, callback_data="btq:cat:shoes"),
        InlineKeyboardButton(text=t_acc, callback_data="btq:cat:acc"),
    ])

    # Navigation row
    prev_page = (cur_page - 1) % total_pages
    next_page = (cur_page + 1) % total_pages
    nav_row = [
        InlineKeyboardButton(text="⬅️ قبلی", callback_data=f"btq:page:{cat_key}:{prev_page}"),
        InlineKeyboardButton(text=f"[{cur_page + 1} / {total_pages}]", callback_data="noop"),
        InlineKeyboardButton(text="بعدی ➡️", callback_data=f"btq:page:{cat_key}:{next_page}"),
    ]
    keyboard.append(nav_row)

    # Footer row
    keyboard.append([
        InlineKeyboardButton(text="🔙 منوی فروشگاه", callback_data="btq:hub"),
        InlineKeyboardButton(text="👕 کمد من", callback_data="wdr:view:0"),
        InlineKeyboardButton(text="🚪 بستن", callback_data="btq:close"),
    ])

    player_av = await avatar.get_player_avatar(user_id)
    render_url = avatar.get_render_url(
        player_av.get("equipped_var_ids", []) + [player_av.get("skin_tone", -1)]
    )
    return render_url, "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=keyboard)


async def _build_boutique_item_detail(
    user_id: int,
    var_id: int,
    category: str = "top",
    page: int = 0,
    is_preview: bool = False,
) -> tuple[str, str, InlineKeyboardMarkup]:
    """Generate item detail card with optional on-demand avatar preview."""
    from services import catalog

    player = await game.load_player(user_id)
    if not player:
        player = await game.ensure_player(
            user_id=user_id,
            username="",
            display_name="مسافر",
        )

    it = catalog.get_catalog_item_by_var_id(var_id)
    if not it:
        return (
            "",
            "❌ آیتم مورد نظر یافت نشد.",
            InlineKeyboardMarkup(
                inline_keyboard=[[
                    InlineKeyboardButton(
                        text="🔙 بازگشت به فهرست", callback_data=f"btq:page:{category}:{page}"
                    )
                ]]
            ),
        )

    it_name = it["name"]
    it_gen = it.get("gender", "unisex")
    cat_title = catalog.BOUTIQUE_CATEGORY_TITLES.get(category, it.get("category", "پوشاک"))
    price, price_label = avatar.calculate_item_price(it_gen, player.gender)
    is_owned = await avatar.owns_item(user_id, var_id)

    av = await avatar.get_player_avatar(user_id)
    cur_eq = av.get("equipped_var_ids", [])
    is_equipped = var_id in cur_eq

    if is_equipped:
        status_str = "👕 <b>این لباس هم‌اکنون بر تن کاراکتر شماست.</b>"
    elif is_owned:
        status_str = "✅ <b>این لباس در کمد شما موجود است.</b>"
    else:
        status_str = f"💰 <b>قیمت نهایی برای شما:</b> {price:,} رکسار"

    if is_preview:
        preview_note = "\n<i>👁 تصویر استودیویی محصول</i>\n"
    else:
        preview_note = ""

    lines = [
        "🛍 <b>مشخصات و خرید آیتم بوتیک</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        f"👗 <b>نام لباس:</b> {it_name}",
        f"📂 <b>دسته‌بندی:</b> {cat_title}",
        f"⚧ <b>استایل:</b> {it_gen}",
        f"📦 <b>شناسه اختصاصی:</b> #{var_id}",
        f"🏷 <b>تعرفه قیمت:</b> {price_label}",
        status_str,
        preview_note,
        "━━━━━━━━━━━━━━━━━━━━",
    ]

    render_url = ""
    if is_preview:
        # فقط عکس standalone خودِ آیتم (بدون کاراکتر)
        render_url = avatar.get_render_url([var_id])
    else:
        render_url = avatar.get_render_url(cur_eq + [av.get("skin_tone", -1)])

    keyboard: list[list[InlineKeyboardButton]] = []

    # Action row (Buy or Equip/Unequip)
    if is_owned:
        if is_equipped:
            keyboard.append([
                InlineKeyboardButton(
                    text="❌ درآوردن این لباس از تن",
                    callback_data=f"btq:unequip_it:{var_id}:{category}:{page}",
                )
            ])
        else:
            keyboard.append([
                InlineKeyboardButton(
                    text="👕 تن زدن این لباس روی کاراکتر",
                    callback_data=f"btq:equip_it:{var_id}:{category}:{page}",
                )
            ])
    else:
        keyboard.append([
            InlineKeyboardButton(
                text=f"🛒 خرید این لباس ({price:,} رکسار)",
                callback_data=f"btq:buy_it:{var_id}:{category}:{page}",
            )
        ])

    # Preview toggle row
    if is_preview:
        keyboard.append([
            InlineKeyboardButton(
                text="🔙 بستن عکس محصول",
                callback_data=f"btq:view:{var_id}:{category}:{page}",
            )
        ])
    else:
        keyboard.append([
            InlineKeyboardButton(
                text="👁 عکس استودیویی محصول",
                callback_data=f"btq:prev_it:{var_id}:{category}:{page}",
            )
        ])

    keyboard.append([
        InlineKeyboardButton(
            text="🔙 بازگشت به فهرست آیتم‌ها", callback_data=f"btq:page:{category}:{page}"
        )
    ])

    return render_url, "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=keyboard)


async def _update_boutique_view(
    call: CallbackQuery,
    caption: str,
    markup: InlineKeyboardMarkup,
    media_url: str | None = None,
) -> None:
    """Zero-bandwidth updater: edits caption in-place without downloading new photos."""
    if not call.message:
        return

    if media_url:
        photo_payload = await _render_photo_payload(media_url, "boutique.png")
        with contextlib.suppress(TelegramBadRequest):
            await call.message.edit_media(
                media=InputMediaPhoto(media=photo_payload, caption=caption, parse_mode="HTML"),
                reply_markup=markup,
            )
            return

    if call.message.photo:
        with contextlib.suppress(TelegramBadRequest):
            await call.message.edit_caption(caption=caption, reply_markup=markup, parse_mode="HTML")
            return

    with contextlib.suppress(TelegramBadRequest):
        await call.message.edit_text(text=caption, reply_markup=markup, parse_mode="HTML")


async def _build_wardrobe_card(
    user_id: int,
    index: int = 0,
    *,
    in_group: bool = False,
) -> tuple[str, str, InlineKeyboardMarkup]:
    """Generate media URL, caption, and keyboard for player wardrobe."""
    all_sets = await avatar.list_wardrobe_sets(user_id)
    owned_items = await avatar.list_owned_items(user_id)
    player_av = await avatar.get_player_avatar(user_id)
    equipped_vars = player_av.get("equipped_var_ids", [])
    render_url = await avatar.get_player_render_url(user_id)

    if not all_sets:
        # No full sets, display character avatar with direct item management
        caption = (
            "🧥 <b>کمد لباس شخصی شما</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"👤 <b>اقلام بر تن کاراکتر:</b> {len(equipped_vars)} تکه\n"
            f"👕 <b>تک‌آیتم‌های کمد شما:</b> {len(owned_items)} قلم لباس\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "💡 از دکمه <b>«👕 آیتم‌های کمد من»</b> برای مدیریت لباس‌های خود استفاده کنید."
        )
        custom_url = await avatar.get_customizer_url(user_id) if settings.webapp_url else ""
        buttons: list[list[InlineKeyboardButton]] = [
            [
                InlineKeyboardButton(
                    text=f"👕 آیتم‌های کمد من ({len(owned_items)})",
                    callback_data="wdr:items:0",
                ),
            ],
            [
                InlineKeyboardButton(text="🎨 انتخاب رنگ پوست", callback_data="wdr:skin_menu"),
                InlineKeyboardButton(text="💇‍♀️ آرایشگاه و چهره", callback_data="sal:menu"),
            ],
        ]
        if custom_url:
            buttons.insert(
                0,
                [
                    _mini_app_btn(
                        "📱 مینی‌اپ شخصی‌سازی و طراحی لباس",
                        custom_url,
                        in_group,
                        payload="wardrobe",
                    )
                ],
            )
        buttons.append([
            InlineKeyboardButton(text="🛍 ویترین بوتیک مد", callback_data="btq:open"),
            InlineKeyboardButton(text="🚪 بستن کمد", callback_data="btq:close"),
        ])
        return render_url, caption, InlineKeyboardMarkup(inline_keyboard=buttons)

    total = len(all_sets)
    idx = max(0, min(index, total - 1))
    current_set = all_sets[idx]
    set_id = current_set["set_id"]
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
        f"🧥 <b>کمد لباس و ست‌های شخصی شما</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"👗 <b>ست آماده:</b> {current_set['name']}\n"
        f"📦 <b>تعداد اقلام ست:</b> {current_set['item_count']} تکه\n"
        f"👕 <b>تک‌آیتم‌های مستقل در کمد:</b> {len(owned_items)} قلم\n"
        f"📌 <b>وضعیت:</b> {status_str}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"<i>ست {idx + 1} از {total}</i>"
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
        nav_row = [InlineKeyboardButton(text=f"✨ {total} ست آماده موجود", callback_data="noop")]

    action_row: list[InlineKeyboardButton] = [
        InlineKeyboardButton(
            text=f"👕 آیتم‌های کمد من ({len(owned_items)})",
            callback_data="wdr:items:0",
        )
    ]
    if not is_active:
        action_row.insert(
            0,
            InlineKeyboardButton(
                text="👗 پوشیدن این ست",
                callback_data=f"wdr:equip:{set_id}:{idx}",
            ),
        )

    options_row = [
        InlineKeyboardButton(text="🔄 انتخاب سریع ست", callback_data="wdr:sets_list:0"),
        InlineKeyboardButton(text="💇‍♀️ آرایشگاه و چهره", callback_data="sal:menu"),
    ]

    if settings.webapp_url:
        custom_url = await avatar.get_customizer_url(user_id)
        options_row.insert(
            0,
            _mini_app_btn(
                "📱 مینی‌اپ شخصی‌سازی",
                custom_url,
                in_group,
                payload="wardrobe",
            ),
        )

    bottom_row = [
        InlineKeyboardButton(text="🛍 رفتن به بوتیک", callback_data="btq:open"),
        InlineKeyboardButton(text="🚪 بستن کمد", callback_data="btq:close"),
    ]

    markup = InlineKeyboardMarkup(
        inline_keyboard=[nav_row, action_row, options_row, bottom_row]
    )
    return render_url, caption, markup


async def _build_wardrobe_items_menu(
    user_id: int,
    page: int = 0,
) -> tuple[str, str, InlineKeyboardMarkup]:
    """Generate high-efficiency zero-bandwidth single-item wardrobe manager."""
    owned_items = await avatar.list_owned_items(user_id)
    render_url = await avatar.get_player_render_url(user_id)

    total_items = len(owned_items)
    page_size = 5
    total_pages = max(1, (total_items + page_size - 1) // page_size)
    cur_page = max(0, min(page, total_pages - 1))

    start_idx = cur_page * page_size
    page_items = owned_items[start_idx : start_idx + page_size]

    lines = [
        "👕 <b>کمد تک‌آیتم‌های شخصی شما</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        "در این بخش می‌توانید آیتم‌های کمد را مستقیماً به استایل اضافه کرده یا از تن دربیاورید:",
        f"📄 <b>صفحه {cur_page + 1} از {total_pages}</b> ({total_items} قلم لباس در کمد)",
        "",
    ]

    keyboard: list[list[InlineKeyboardButton]] = []

    for idx, it in enumerate(page_items):
        vid = it["var_id"]
        name = it["name"]
        cat = it["category"]
        is_eq = it["is_equipped"]

        status_text = "✅ <b>بر تن کاراکتر</b>" if is_eq else "📦 <i>در کمد</i>"
        lines.append(f"{idx + 1}. <b>{name}</b> ({cat}) — {status_text}")

        if is_eq:
            btn = InlineKeyboardButton(
                text=f"❌ درآوردن «{name[:16]}»",
                callback_data=f"wdr:it_off:{vid}:{cur_page}",
            )
        else:
            btn = InlineKeyboardButton(
                text=f"➕ تن زدن «{name[:16]}»",
                callback_data=f"wdr:it_on:{vid}:{cur_page}",
            )
        keyboard.append([btn])

    if not page_items:
        lines.append("<i>هنوز لباسی در کمد شما نیست. برای خرید تک‌آیتم‌ها به بوتیک بروید!</i>")

    lines.append("━━━━━━━━━━━━━━━━━━━━")

    # Navigation row
    if total_pages > 1:
        prev_p = (cur_page - 1) % total_pages
        next_p = (cur_page + 1) % total_pages
        keyboard.append([
            InlineKeyboardButton(text="⬅️ قبلی", callback_data=f"wdr:items:{prev_p}"),
            InlineKeyboardButton(text=f"[{cur_page + 1} / {total_pages}]", callback_data="noop"),
            InlineKeyboardButton(text="بعدی ➡️", callback_data=f"wdr:items:{next_p}"),
        ])

    keyboard.append([
        InlineKeyboardButton(text="👗 ست‌های کمد", callback_data="wdr:open:0"),
        InlineKeyboardButton(text="🛍 ویترین بوتیک", callback_data="btq:open"),
    ])
    keyboard.append([
        InlineKeyboardButton(text="🚪 بستن کمد", callback_data="btq:close"),
    ])

    return render_url, "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=keyboard)


async def _build_outfit_switch_menu(
    user_id: int,
    *,
    in_group: bool = False,
) -> tuple[str, str, InlineKeyboardMarkup]:
    """Generate change-outfit menu listing all available owned sets with 1-tap equip."""
    items = await avatar.list_wardrobe_sets(user_id)
    player_av = await avatar.get_player_avatar(user_id)
    equipped_vars = player_av.get("equipped_var_ids", [])
    active_set_id = player_av.get("active_set_id")
    render_url = await avatar.get_player_render_url(user_id)

    clean_eq_set = set(int(v) for v in equipped_vars if int(v) > 0)

    caption = (
        "🔄 <b>منوی تعویض لباس و انتخاب ست آماده</b>\n"
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
            btn_text = f"👗 {s['name']} ({s['item_count']} تکه)"
            cb = f"wdr:equip:{s_id}:{idx}"
        buttons.append([InlineKeyboardButton(text=btn_text, callback_data=cb)])

    buttons.append([
        InlineKeyboardButton(text="👕 مشاهده تک‌آیتم‌های کمد", callback_data="wdr:items:0")
    ])

    if settings.webapp_url:
        custom_url = await avatar.get_customizer_url(user_id)
        buttons.append(
            [
                _mini_app_btn(
                    "📱 طراحی و شخصی‌سازی در مینی‌اپ",
                    custom_url,
                    in_group,
                    payload="wardrobe",
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

@router.message(Command("boutique", "fashion", "shop"))
@router.message(F.text.lower().in_(BOUTIQUE_COMMANDS))
async def handle_boutique_command(message: Message) -> None:
    """Open shop mini-app in Shop Mode (unified with /shop command)."""
    if not message.from_user:
        return
    user_id = message.from_user.id
    user = message.from_user
    is_group = _is_group_chat(message.chat.type)
    try:
        player = await hydrate(user_id, user.full_name, user.username)
        custom_url = await avatar.get_customizer_url(user_id, mode="shop")
        text = (
            "🛍 <b>مرکز خرید و بوتیک مد تیرامیکس (Igris Fashion & Shop)</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"👤 شهروند: {player.display_name}\n"
            "برای ورود به بخش خرید و پرو لباس‌ها، روش مورد نظر خود را انتخاب کنید:\n\n"
            "۱. 📱 <b>مینی‌اپ دیداری و گرافیکی (پیشنهادی):</b>\n"
            "مشاهده زنده لباس‌ها روی مانکن، پرو همزمان و خرید مستقیم در مینی‌اپ لمسی.\n\n"
            "۲. 💬 <b>ویترین متنی داخل تلگرام:</b>\n"
            "مرور سریع کاتالوگ روزانه بر اساس دسته‌بندی (بالاتنه، پایین‌تنه، کفش و اکسسوری).\n\n"
            "۳. 👗 <b>کمد شخصی:</b>\n"
            "مشاهده، پوشیدن و مدیریت لباس‌های خریداری‌شده."
        )
        markup = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    _mini_app_btn(
                        "📱 باز کردن فروشگاه مد (مینی‌اپ)",
                        custom_url,
                        is_group,
                        payload="shop",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="🛍 مشاهده ویترین متنی بوتیک", callback_data="btq:catalog:top:0"
                    ),
                ],
                [
                    InlineKeyboardButton(
                        text="👗 کمد لباس‌های من", callback_data="wdr:view:0"
                    ),
                    InlineKeyboardButton(
                        text="💇‍♀️ آرایشگاه", callback_data="sal:menu"
                    ),
                ],
                [
                    InlineKeyboardButton(text="🚪 بستن", callback_data="btq:close"),
                ],
            ]
        )
        await message.reply(text, reply_markup=markup)
    except Exception as exc:  # noqa: BLE001
        await answer_error(exc, message=message)


@router.message(F.text.lower().in_({"ویترین", "کاتالوگ", "ویترین بوتیک", "کاتالوگ بوتیک"}))
async def handle_boutique_showcase_command(message: Message) -> None:
    """Directly open daily boutique photo & text showcase."""
    if not message.from_user:
        return
    user_id = message.from_user.id
    try:
        render_url, caption, markup = await _build_boutique_catalog(
            user_id=user_id,
            category="top",
            page=0,
            in_group=_is_group_chat(message.chat.type),
        )
        photo_payload = await _render_photo_payload(render_url, "boutique.png")
        await message.reply_photo(photo=photo_payload, caption=caption, reply_markup=markup)
    except Exception as exc:  # noqa: BLE001
        await answer_error(exc, message=message)


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
    render_url, caption, markup = await _build_wardrobe_card(
        user_id=user_id, index=0, in_group=_is_group_chat(message.chat.type)
    )
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
                InlineKeyboardButton(text="🛍 ویترین بوتیک", callback_data="btq:open"),
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
                _mini_app_btn(
                    "📱 مینی‌اپ شخصی‌سازی کاراکتر",
                    custom_url,
                    _is_group_chat(message.chat.type),
                    payload="customizer",
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

    is_group = message.chat.type in ("group", "supergroup")

    if is_group:
        # In groups: use deep link to DM
        caption = (
            "👑 <b>مینی‌اپ شخصی‌سازی و تجهیز کاراکتر ایگریس</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "⚠️ برای استفاده از مینی‌اپ کاستومایز، روی دکمه زیر کلیک کنید تا\n"
            "به فضای خصوصی ربات منتقل شوید.\n\n"
            "در بخش شخصی می‌توانید آواتار خود را با بیش از ۹,۰۰۰ قطعه در ۵ ناحیه بدنه "
            "(سر، بالاتنه، پایین‌تنه، کفش و اکسسوری) به صورت زنده طراحی و ست کنید."
        )
        buttons: list[list[InlineKeyboardButton]] = []
        username = _bot_username()
        if username:
            deep_link = f"https://t.me/{username}?start=customizer"
            buttons.append(
                [
                    InlineKeyboardButton(
                        text="🎨 رفتن به کاستومایز (در DM بات)",
                        url=deep_link,
                    )
                ]
            )
    else:
        # In DM: show webapp button
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
                    _mini_app_btn(
                        "📱 باز کردن مینی‌اپ شخصی‌سازی",
                        custom_url,
                        False,
                        payload="customizer",
                    )
                ]
            )

    buttons.append(
        [
            InlineKeyboardButton(text="👕 کمد لباس من", callback_data="wdr:view:0"),
            InlineKeyboardButton(text="🛍 ویترین بوتیک", callback_data="btq:open"),
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

        total_bought_price = 0
        unowned_vars = []
        if not is_new_player:
            unowned_vars = [v for v in var_ids if not await avatar.owns_item(user_id, v)]
            if unowned_vars:
                from services import catalog
                items_to_add = []
                for v in unowned_vars:
                    it = catalog.get_catalog_item_by_var_id(v)
                    g = it.get("gender", "unisex") if it else "unisex"
                    p, _ = avatar.calculate_item_price(g, player.gender)
                    items_to_add.append((v, it, p))
                    total_bought_price += p

                creds, _ = await economy.balances(user_id)
                if creds < total_bought_price:
                    await message.reply(
                        f"❌ <b>موجودی رکسار شما برای خرید لباس‌های جدید کافی نیست!</b>\n\n"
                        f"مبلغ کل اقلام انتخابی: <b>{total_bought_price:,}</b> رکسار\n"
                        f"موجودی کیف پول شما: <b>{creds:,}</b> رکسار\n\n"
                        "با دستور <code>کار</code> یا برداشت از بانک موجودی خود را افزایش دهید."
                    )
                    return

                await economy.spend(
                    user_id, credits=total_bought_price, kind=ActivityKind.SHOP_BUY
                )
                now_ts = int(time.time())
                async with db.write() as conn:
                    for v, it, p in items_to_add:
                        v_set_id = 1000000 + v
                        title = f"✨ {it['name']}" if it else f"✨ لباس #{v}"
                        it_gen = it.get("gender", "unisex") if it else "unisex"
                        await conn.execute(
                            """
                            INSERT INTO player_wardrobe
                                (user_id, set_id, set_name, gender, item_count,
                                 var_ids, is_plus_size, price_paid, acquired_at)
                            VALUES (?, ?, ?, ?, 1, ?, 0, ?, ?)
                            ON CONFLICT(user_id, set_id) DO UPDATE SET
                                var_ids = excluded.var_ids,
                                acquired_at = excluded.acquired_at
                            """,
                            (user_id, v_set_id, title, it_gen, json.dumps([v]), p, now_ts),
                        )

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
                w_url, w_cap, w_mark = await _build_wardrobe_card(
                    user_id=user_id, index=0, in_group=_is_group_chat(message.chat.type)
                )
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
                f"💰 <b>سرمایه اولیه:</b> ۲۵۰ رکسار هدیه ورود به شهر\n\n"
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
                            callback_data="btq:open",
                        ),
                        InlineKeyboardButton(text="🎒 کمد لباس من", callback_data="wdr:view:0"),
                    ],
                    [
                        _mini_app_btn(
                            "🎨 ویرایش مجدد در مینی‌اپ",
                            custom_url,
                            _is_group_chat(message.chat.type),
                            payload="customizer",
                        )
                    ],
                ]
            )
        else:
            if total_bought_price > 0:
                caption = (
                    "🎉 <b>خرید موفق از مینی‌اپ فروشگاه و بوتیک مد تیرامیکس!</b> 🛍✨\n"
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    f"تعداد <b>{len(unowned_vars)} قلم</b> لباس جدید با موفقیت خریداری شد.\n"
                    f"💰 کل مبلغ پرداختی: <b>{total_bought_price:,}</b> رکسار\n"
                    "👕 تمامی لباس‌ها به کمد دائمی شما اضافه شدند و بر تن نشستند!"
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
                        _mini_app_btn(
                            "🎨 ویرایش مجدد در مینی‌اپ",
                            custom_url,
                            _is_group_chat(message.chat.type),
                            payload="customizer",
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
    """Open shop mini-app in Shop Mode (unified gateway for all boutique buttons)."""
    if not call.from_user or not call.message:
        await call.answer()
        return

    user_id = call.from_user.id
    user = call.from_user
    is_group = _is_group_chat(call.message.chat.type)

    try:
        player = await hydrate(user_id, user.full_name, user.username)
        custom_url = await avatar.get_customizer_url(user_id, mode="shop")
        text = (
            "🛍 <b>مرکز خرید و بوتیک مد تیرامیکس (Igris Fashion & Shop)</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"👤 شهروند: {player.display_name}\n"
            "برای ورود به بخش خرید و پرو لباس‌ها، روش مورد نظر خود را انتخاب کنید:\n\n"
            "۱. 📱 <b>مینی‌اپ دیداری و گرافیکی (پیشنهادی):</b>\n"
            "مشاهده زنده لباس‌ها روی مانکن، پرو همزمان و خرید مستقیم در مینی‌اپ لمسی.\n\n"
            "۲. 💬 <b>ویترین متنی داخل تلگرام:</b>\n"
            "مرور سریع کاتالوگ روزانه بر اساس دسته‌بندی (بالاتنه، پایین‌تنه، کفش و اکسسوری).\n\n"
            "۳. 👗 <b>کمد شخصی:</b>\n"
            "مشاهده، پوشیدن و مدیریت لباس‌های خریداری‌شده."
        )
        markup = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    _mini_app_btn(
                        "📱 باز کردن فروشگاه مد (مینی‌اپ)",
                        custom_url,
                        is_group,
                        payload="shop",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="🛍 مشاهده ویترین متنی بوتیک", callback_data="btq:catalog:top:0"
                    ),
                ],
                [
                    InlineKeyboardButton(
                        text="👗 کمد لباس‌های من", callback_data="wdr:view:0"
                    ),
                    InlineKeyboardButton(
                        text="💇‍♀️ آرایشگاه", callback_data="sal:menu"
                    ),
                ],
                [
                    InlineKeyboardButton(text="🚪 بستن", callback_data="btq:close"),
                ],
            ]
        )

        if call.message.photo:
            with contextlib.suppress(Exception):
                await call.message.delete()
            await call.message.answer(text, reply_markup=markup)
            await call.answer()
            return

        with contextlib.suppress(TelegramBadRequest):
            await call.message.edit_text(text, reply_markup=markup)
            await call.answer()
            return

        with contextlib.suppress(Exception):
            await call.message.delete()
        await call.message.answer(text, reply_markup=markup)
        await call.answer()

    except Exception as exc:  # noqa: BLE001
        logger.exception("Boutique callback error")
        await call.answer(f"❌ خطا: {exc}", show_alert=True)


@router.callback_query(F.data.startswith("btq:"))
async def handle_boutique_callback(call: CallbackQuery) -> None:
    """Handle all boutique interactions with zero image bandwidth waste."""
    if not call.from_user or not call.data or not call.message:
        await call.answer()
        return

    user_id = call.from_user.id
    parts = call.data.split(":")
    action = parts[1] if len(parts) > 1 else ""

    if action in ("open", "main", "hub"):
        await open_boutique_from_callback(call)
        return

    if action in ("catalog", "showcase"):
        cat_key = parts[2] if len(parts) > 2 else "top"
        page = int(parts[3]) if len(parts) > 3 else 0
        render_url, caption, markup = await _build_boutique_catalog(
            user_id=user_id,
            category=cat_key,
            page=page,
            in_group=_is_group_chat(call.message.chat.type),
        )
        if call.message.photo:
            await _update_boutique_view(call, caption=caption, markup=markup, media_url=None)
        else:
            photo_payload = await _render_photo_payload(render_url, "boutique.png")
            with contextlib.suppress(Exception):
                await call.message.delete()
            await call.message.answer_photo(
                photo=photo_payload,
                caption=caption,
                reply_markup=markup,
            )
        await call.answer()
        return

    if action == "close":
        avatar.clear_user_wardrobe_message(user_id)
        await call.answer()
        with contextlib.suppress(TelegramBadRequest):
            await call.message.delete()
        return

    if action == "cat":
        cat_key = parts[2] if len(parts) > 2 else "top"
        if cat_key in ("top", "bottom", "shoes", "acc"):
            _, caption, markup = await _build_boutique_catalog(
                user_id=user_id,
                category=cat_key,
                page=0,
                in_group=_is_group_chat(call.message.chat.type),
            )
            await _update_boutique_view(call, caption=caption, markup=markup, media_url=None)
            await call.answer()
            return

        # Legacy category support for test suites
        render_url, caption, markup = await _build_boutique_card(
            user_id=user_id,
            gender=cat_key,
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

    if action == "page":
        cat_key = parts[2] if len(parts) > 2 else "top"
        page = int(parts[3]) if len(parts) > 3 else 0
        _, caption, markup = await _build_boutique_catalog(
            user_id=user_id,
            category=cat_key,
            page=page,
            in_group=_is_group_chat(call.message.chat.type),
        )
        await _update_boutique_view(call, caption=caption, markup=markup, media_url=None)
        await call.answer()
        return

    if action == "view":
        var_id = int(parts[2]) if len(parts) > 2 else 0
        cat_key = parts[3] if len(parts) > 3 else "top"
        page = int(parts[4]) if len(parts) > 4 else 0
        _, caption, markup = await _build_boutique_item_detail(
            user_id=user_id, var_id=var_id, category=cat_key, page=page, is_preview=False
        )
        await _update_boutique_view(call, caption=caption, markup=markup, media_url=None)
        await call.answer()
        return

    if action == "prev_it":
        var_id = int(parts[2]) if len(parts) > 2 else 0
        cat_key = parts[3] if len(parts) > 3 else "top"
        page = int(parts[4]) if len(parts) > 4 else 0
        render_url, caption, markup = await _build_boutique_item_detail(
            user_id=user_id, var_id=var_id, category=cat_key, page=page, is_preview=True
        )
        await _update_boutique_view(call, caption=caption, markup=markup, media_url=render_url)
        await call.answer("👁 عکس استودیویی محصول بارگذاری شد!")
        return

    if action == "buy_it":
        var_id = int(parts[2]) if len(parts) > 2 else 0
        cat_key = parts[3] if len(parts) > 3 else "top"
        page = int(parts[4]) if len(parts) > 4 else 0
        player = await game.load_player(user_id)
        if not player:
            player = await game.ensure_player(
                user_id=user_id, username="", display_name="مسافر"
            )

        try:
            price, it_info = await avatar.buy_boutique_item(
                user_id=user_id,
                var_id=var_id,
                player_gender=player.gender,
                player_drip=player.drip,
            )
            await call.answer(
                f"🎉 لباس «{it_info['name']}» با {price:,} رکسار خریداری شد و به کمد رفت!",
                show_alert=True,
            )
        except avatar.AlreadyOwned:
            await call.answer("شما قبلاً این لباس را خریده‌اید!", show_alert=True)
        except Exception as exc:
            await call.answer(f"خطا در خرید: {exc}", show_alert=True)

        _, caption, markup = await _build_boutique_item_detail(
            user_id=user_id, var_id=var_id, category=cat_key, page=page, is_preview=False
        )
        await _update_boutique_view(call, caption=caption, markup=markup, media_url=None)
        return

    if action == "equip_it":
        var_id = int(parts[2]) if len(parts) > 2 else 0
        cat_key = parts[3] if len(parts) > 3 else "top"
        page = int(parts[4]) if len(parts) > 4 else 0
        res = await avatar.equip_single_item(user_id, var_id)
        await call.answer(res["message"], show_alert=True)
        render_url, caption, markup = await _build_boutique_item_detail(
            user_id=user_id, var_id=var_id, category=cat_key, page=page, is_preview=True
        )
        await _update_boutique_view(call, caption=caption, markup=markup, media_url=render_url)
        return

    if action == "unequip_it":
        var_id = int(parts[2]) if len(parts) > 2 else 0
        cat_key = parts[3] if len(parts) > 3 else "top"
        page = int(parts[4]) if len(parts) > 4 else 0
        res = await avatar.unequip_single_item(user_id, var_id)
        await call.answer(res["message"], show_alert=True)
        render_url, caption, markup = await _build_boutique_item_detail(
            user_id=user_id, var_id=var_id, category=cat_key, page=page, is_preview=True
        )
        await _update_boutique_view(call, caption=caption, markup=markup, media_url=render_url)
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
                f"🎉 ست «{s_info['name']}» با {price:,} رکسار خریداری شد و به کمد شما رفت!",
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
        render_url, caption, markup = await _build_outfit_switch_menu(
            user_id=user_id, in_group=_is_group_chat(call.message.chat.type)
        )
        photo_payload = await _render_photo_payload(render_url, "wardrobe.png")
        with contextlib.suppress(TelegramBadRequest):
            await call.message.edit_media(
                media=InputMediaPhoto(media=photo_payload, caption=caption, parse_mode="HTML"),
                reply_markup=markup,
            )
        await call.answer()
        return

    if action == "items":
        page = int(parts[2]) if len(parts) > 2 else 0
        render_url, caption, markup = await _build_wardrobe_items_menu(user_id=user_id, page=page)
        photo_payload = await _render_photo_payload(render_url, "wardrobe.png")
        with contextlib.suppress(TelegramBadRequest):
            await call.message.edit_media(
                media=InputMediaPhoto(media=photo_payload, caption=caption, parse_mode="HTML"),
                reply_markup=markup,
            )
        await call.answer()
        return

    if action == "it_on":
        vid = int(parts[2]) if len(parts) > 2 else 0
        page = int(parts[3]) if len(parts) > 3 else 0
        res = await avatar.equip_single_item(user_id, vid)
        await call.answer(res["message"], show_alert=True)
        render_url, caption, markup = await _build_wardrobe_items_menu(user_id=user_id, page=page)
        photo_payload = await _render_photo_payload(render_url, "wardrobe.png")
        with contextlib.suppress(TelegramBadRequest):
            await call.message.edit_media(
                media=InputMediaPhoto(media=photo_payload, caption=caption, parse_mode="HTML"),
                reply_markup=markup,
            )
        return

    if action == "it_off":
        vid = int(parts[2]) if len(parts) > 2 else 0
        page = int(parts[3]) if len(parts) > 3 else 0
        res = await avatar.unequip_single_item(user_id, vid)
        await call.answer(res["message"], show_alert=True)
        render_url, caption, markup = await _build_wardrobe_items_menu(user_id=user_id, page=page)
        photo_payload = await _render_photo_payload(render_url, "wardrobe.png")
        with contextlib.suppress(TelegramBadRequest):
            await call.message.edit_media(
                media=InputMediaPhoto(media=photo_payload, caption=caption, parse_mode="HTML"),
                reply_markup=markup,
            )
        return

    avatar.set_user_wardrobe_message(user_id, call.message.chat.id, call.message.message_id)

    if action in ("open", "nav", "view"):
        idx = int(parts[2]) if len(parts) > 2 else 0
        render_url, caption, markup = await _build_wardrobe_card(
            user_id=user_id, index=idx, in_group=_is_group_chat(call.message.chat.type)
        )
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
            if set_id >= 1000000:
                vid = set_id - 1000000
                res = await avatar.equip_single_item(user_id, vid)
                await call.answer(res["message"], show_alert=True)
            else:
                await avatar.equip_set(user_id, set_id)
                await call.answer("👗 ست لباس با موفقیت بر تن کاراکتر شما نشست!", show_alert=True)
        except Exception as exc:
            await call.answer(f"خطا: {exc}", show_alert=True)

        render_url, caption, markup = await _build_wardrobe_card(
            user_id=user_id, index=0, in_group=_is_group_chat(call.message.chat.type)
        )
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
        render_url, caption, markup = await _build_wardrobe_card(
            user_id=user_id, index=idx, in_group=_is_group_chat(call.message.chat.type)
        )
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
