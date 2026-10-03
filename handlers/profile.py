"""/me character card, inventory browsing and equip/unequip callbacks.

The card is the product's heartbeat: render (cache-aware, <50ms) and post a
512px JPEG straight into the group.  Inventory uses paginated inline
keyboards; every press is acked instantly via ``answerCallbackQuery`` so
Telegram never shows the loading spinner.
"""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import (
    BufferedInputFile,
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
    energy_bar,
    esc,
    hydrate,
)
from handlers.panel import refresh_markup, render_panel
from models import Player, Slot
from services import economy, game
from config import settings

logger = logging.getLogger(__name__)
router = Router(name="profile")


def is_admin_or_owner(user_id: int) -> bool:
    """Check if the user is the project owner (Rex Lapis) or has admin privileges."""
    return settings.is_admin(user_id) or user_id == 5765828495


_INVENTORY_PAGE_SIZE = 6


# ---------------------------------------------------------------------------
# /me & /profile
# ---------------------------------------------------------------------------

PROFILE_COMMANDS = {"پروفایل", "من", "کارت", "کاراکتر", "مشخصات", "profile", "me", "card"}
INVENTORY_COMMANDS = {
    "کمد",
    "کوله",
    "کوله پشتی",
    "کوله_پشتی",
    "وسایل",
    "اینونتوری",
    "inventory",
    "inv",
    "wardrobe",
}


async def _profile_markup(player: Player) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[])


async def _profile_panel(player: Player) -> tuple[str, bytes]:
    """The card's caption + rendered bytes, shared by /me and the callback."""
    import time
    from services import tiramix

    photo = await card_bytes(player)
    credits, shards = await economy.balances(player.user_id)
    now = int(time.time())

    jail_status = f"🔒 حبس در بازداشتگاه ({int((player.is_jailed_until - now)//60)} دقیقه)" if player.is_in_jail(now) else "🟢 آزاد"
    pregnancy_status = "🤰 باردار" if player.is_pregnant(now) else ""
    marital_status = f"💍 متأهل" if player.spouse_id else "مجرد"
    
    clan_name = "ندارد"
    if player.clan_id:
        clan_info = await tiramix.get_player_clan(player.clan_id)
        if clan_info:
            clan_name = f"🛡 {clan_info['name']}"

    pet_status = f"لول {player.pet_level}" if player.pet_level > 0 else "ندارد"
    shield_status = (
        f"فعال ({int((player.shield_until - now) // 60)} دقیقه)"
        if player.shield_until > now
        else "غیرفعال"
    )

    lines = [
        f"🍁 <b>شناسنامه شهروندی شهر تیرامیکس</b>",
        "",
        f"👤 <b>نام:</b> {esc(player.username or player.display_name)} (لول <b>{player.level}</b>)",
        f"🎂 <b>سن:</b> {player.age} سال | ⚧ <b>جنسیت:</b> {player.gender}",
        f"🎓 <b>سطح سواد:</b> {player.education_title}",
        f"💼 <b>شغل:</b> {player.job}",
        f"💍 <b>وضعیت تأهل:</b> {marital_status} | 👶 <b>فرزندان:</b> {player.children_count} {pregnancy_status}",
        f"💰 <b>کیف پول:</b> <b>{credits:,}</b> سکه | 🏦 <b>بانک:</b> <b>{player.bank_balance:,}</b> سکه",
        f"🛡 <b>کلن:</b> {clan_name} | ⚖️ <b>وضعیت قضایی:</b> {jail_status}",
        f"⚔️ قدرت: <b>{player.atk}</b> · 🛡 دفاع: <b>{player.defense}</b> · 💎 استایل: <b>{player.drip}</b>",
        f"🐺 <b>حیوان:</b> {pet_status} | 🤫 <b>خیانت مخفی:</b> {player.affair_count}",
        f"🛡 <b>سپر ضدسرقت:</b> {shield_status}",
    ]

    return "\n".join(lines), photo


async def _send_profile(target: Message, player: Player) -> None:
    caption, photo = await _profile_panel(player)
    # STRICT USER PREFERENCE: NO CLUTTERED BUTTONS UNDER THE CARD
    try:
        await target.reply_photo(
            photo=BufferedInputFile(photo, filename="character.jpg"),
            caption=caption,
            reply_markup=None,
        )
    except Exception:
        if getattr(target, "bot", None):
            await target.bot.send_photo(
                chat_id=target.chat.id,
                photo=BufferedInputFile(photo, filename="character.jpg"),
                caption=caption,
                reply_markup=None,
            )


async def _show_profile(message: Message | None, player: Player) -> None:
    """Render the card into an existing panel (photo-aware, never dead)."""
    if message is None:
        return
    caption, photo = await _profile_panel(player)
    await render_panel(
        message,
        text=caption,
        photo=photo,
        reply_markup=None,
        force_media=True,
    )


@router.message(CommandOrText(["me", "profile", "card"], PROFILE_COMMANDS))
async def cmd_profile(message: Message) -> None:
    user = message.from_user
    if user is None:
        return
    try:
        player = await hydrate(user.id, user.full_name, user.username)
        await _send_profile(message, player)
    except Exception as exc:  # noqa: BLE001 - domain errors surface to chat
        await answer_error(exc, message=message)


def is_creator(user_id: int) -> bool:
    """Check if the user is the bot creator (Rex Lapis) or has admin privileges."""
    return user_id == 5765828495 or settings.is_admin(user_id)


@router.message(CommandOrText(["version", "ver"], {"ورژن", "نسخه"}))
async def cmd_version(message: Message) -> None:
    user_id = message.from_user.id if message.from_user else 0
    if not is_creator(user_id):
        await message.answer("🔒 <i>این دستور محرمانه و مختص سازنده‌ی ربات است.</i>")
        return

    text = (
        "👑 <b>پنل وضعیت نسخه و تغییرات سیستمی (مخصوص سازنده)</b>\n\n"
        "🔖 <b>نسخه فعال:</b> <code>v2.0.0</code> (Tiramix City Life Simulator Edition)\n"
        "⚡️ <b>موتور شبیه‌ساز شهری:</b> شبیه‌ساز جهان اجتماعی تیرامیکس | ۸۵ تست پاس‌شده\n"
        "🏛 <b>سیستم‌های فعال:</b> بانک، وام و گاوصندوق، زندان، دادگاه خانواده با قاضی تصادفی، کلن‌ها، ازدواج و فرزند\n\n"
        "📝 <b>لیست آخرین تغییرات اعمال‌شده در نسخه ۲.۰.۰:</b>\n"
        "├ 🍁 <b>تغییر کامل هویت و تم:</b> مهاجرت به جهان مدرن، لوفای و پاییزی شهر تیرامیکس\n"
        "├ 🎴 <b>شناسنامه تمیز متنی:</b> حذف تمام دکمه‌های شلوغ پروفایل و صدور کارت شهروندی بدون کلید مزاحم\n"
        "├ 🚂 <b>ویزارد ۹ مرحله‌ای ثبت‌نام:</b> سناریوی قطار با ۵ درجه رنگ پوست، ۳ فرم چشم، ۵ رنگ چشم، ۵ مدل مو، ۳ رنگ مو و ۴ میمیک لب\n"
        "├ 💼 <b>خدمات و اقتصاد شهری:</b> پیاده‌سازی دستورات <code>کار</code>، <code>تحصیل</code>، <code>شغل</code>، <code>بانک</code>، <code>کمد</code>، <code>آرایشگاه</code>، <code>زیبایی</code> و <code>کلینیک</code>\n"
        "├ 🛍 <b>دسته‌بندی بوتیک مد:</b> دسته‌بندی اینلاین لباس‌ها (بالاتنه، پایین‌تنه، سر/کلاه، اکسسوری و ویترین روزانه)\n"
        "├ ⚖️ <b>دادگاه خانواده تیرامیکس:</b> طلاق توافقی یا ارجاع به دادگاه با قضاوت تصادفی یکی از اعضای گروه\n"
        "├ 💍 <b>سیستم روابط زناشویی و فرزند:</b> ازدواج، رابطه، شانس بارداری، نقاهت کلینیک و هزینه‌های رشد فرزند\n"
        "├ 🥷 <b>دزدی و مجازات قضایی:</b> غارت ۳۵٪ تا ۸۵٪ موجودی نقد، جریمه ۵ برابری و حبس در بازداشتگاه شهر\n"
        "└ 🛡 <b>سیستم کلن‌ها و سندیکاها:</b> تشکیل کلن‌های محلی درون گروه‌ها با خزانه و اعضای اختصاصی"
    )
    await message.answer(text)


@router.callback_query(F.data == "act:me")
async def cb_profile(call: CallbackQuery) -> None:
    await call.answer()  # instant ack before the slow render
    user = call.from_user
    message = editable_message(call)
    if user is None or message is None:
        return
    try:
        player = await hydrate(user.id, user.full_name, user.username)
        await _show_profile(message, player)
    except Exception as exc:  # noqa: BLE001
        await answer_error(exc, callback=call)


# ---------------------------------------------------------------------------
# Inventory browser
# ---------------------------------------------------------------------------


async def _inventory_markup(
    player: Player, page: int, total: int
) -> InlineKeyboardMarkup:
    rows = await game.list_inventory(player.user_id)
    pages = max(1, (len(rows) + _INVENTORY_PAGE_SIZE - 1) // _INVENTORY_PAGE_SIZE)
    page = max(0, min(page, pages - 1))
    slice_ = rows[page * _INVENTORY_PAGE_SIZE : (page + 1) * _INVENTORY_PAGE_SIZE]

    buttons: list[list[InlineKeyboardButton]] = []
    for row in slice_:
        marker = "✅ " if row["equipped"] else ""
        buttons.append(
            [
                InlineKeyboardButton(
                    text=f"{marker}{row['name']}",
                    callback_data=f"item:{row['id']}:{page}",
                )
            ]
        )

    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(
            InlineKeyboardButton(text="◀️", callback_data=f"inv:{page - 1}")
        )
    nav.append(
        InlineKeyboardButton(text=f"{page + 1}/{pages}", callback_data="inv:noop")
    )
    if page < pages - 1:
        nav.append(
            InlineKeyboardButton(text="▶️", callback_data=f"inv:{page + 1}")
        )
    buttons.append(nav)
    buttons.append(
        [InlineKeyboardButton(text="🏠 کارت من", callback_data="act:me")]
    )
    return InlineKeyboardMarkup(inline_keyboard=buttons)


@router.callback_query(F.data.startswith("inv:"))
async def cb_inventory(call: CallbackQuery) -> None:
    await call.answer()
    user = call.from_user
    if user is None or call.data is None:
        return
    try:
        page = int(call.data.split(":", 1)[1])
    except ValueError:
        page = 0
    try:
        player = await hydrate(user.id, user.full_name, user.username)
        markup = await _inventory_markup(player, page, 0)
        text = (
            f"🎒 <b>کوله‌پشتی</b> — {esc(player.display_tag)}\n"
            f"⚔️ قدرت: <b>{player.atk}</b> · 🛡 دفاع: <b>{player.defense}</b> · 💎 استایل: <b>{player.drip}</b>"
        )
        await render_panel(
            editable_message(call), text=text, reply_markup=markup
        )
    except Exception as exc:  # noqa: BLE001
        await answer_error(exc, callback=call)


@router.callback_query(F.data.startswith("item:"))
async def cb_item_detail(call: CallbackQuery) -> None:
    """Detail view for one item: stats, flavor, equip/unequip toggle."""
    await call.answer()
    user = call.from_user
    if user is None or call.data is None:
        return
    parts = call.data.split(":")
    item_id = parts[1]
    page = parts[2] if len(parts) > 2 else "0"

    try:
        player = await hydrate(user.id, user.full_name, user.username)
        from database.items import ITEMS_BY_ID

        item = ITEMS_BY_ID.get(item_id)
        if item is None:
            await answer_error(game.GameError("آیتم یافت نشد"), callback=call)
            return

        equipped = player.loadout.get(item.slot.value) == item_id
        lines = [
            f"<b>{esc(item.name)}</b> · {item.rarity.label} · {item.slot.emoji} {item.slot.label}",
            esc(item.description),
            "",
        ]
        if item.atk:
            lines.append(f"⚔️ قدرت: +{item.atk}")
        if item.defense:
            lines.append(f"🛡 دفاع: +{item.defense}")
        if item.drip:
            lines.append(f"💎 استایل: +{item.drip}")
        if not (item.atk or item.defense or item.drip):
            lines.append("فقط تزئینی و ظاهری ✨")

        buttons = [
            [
                InlineKeyboardButton(
                    text="➖ خلع سلاح / برداشتن" if equipped else "➕ تجهیز / استفاده",
                    callback_data=(
                        f"uneq:{item.slot.value}:{page}:{item_id}"
                        if equipped
                        else f"eq:{item_id}:{page}"
                    ),
                )
            ],
            [
                InlineKeyboardButton(
                    text="◀️ کوله‌پشتی", callback_data=f"inv:{page}"
                ),
                InlineKeyboardButton(text="🏠 کارت من", callback_data="act:me"),
            ],
        ]
        await render_panel(
            editable_message(call),
            text="\n".join(lines),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        )
    except Exception as exc:  # noqa: BLE001
        await answer_error(exc, callback=call)


@router.callback_query(F.data.startswith("eq:"))
async def cb_equip(call: CallbackQuery) -> None:
    user = call.from_user
    if user is None or call.data is None:
        return
    parts = call.data.split(":")
    item_id, page = parts[1], parts[2] if len(parts) > 2 else "0"
    try:
        await game.equip_item(user.id, item_id)
        await call.answer("آیتم با موفقیت تنت شد ✅")
        from database.items import ITEMS_BY_ID

        player = await hydrate(user.id, user.full_name, user.username)
        item = ITEMS_BY_ID.get(item_id)
        if item is not None:
            buttons = [
                [
                    InlineKeyboardButton(
                        text="➖ خلع سلاح / برداشتن",
                        callback_data=f"uneq:{item.slot.value}:{page}:{item_id}",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="◀️ کوله‌پشتی", callback_data=f"inv:{page}"
                    ),
                    InlineKeyboardButton(text="🏠 کارت من", callback_data="act:me"),
                ],
            ]
            msg = editable_message(call)
            if msg and msg.photo:
                photo = await card_bytes(player)
                await render_panel(
                    msg,
                    text=msg.caption,
                    photo=photo,
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
                    force_media=True,
                )
            else:
                await refresh_markup(
                    msg,
                    InlineKeyboardMarkup(inline_keyboard=buttons),
                )
        else:
            await refresh_markup(
                editable_message(call),
                await _inventory_markup(player, int(page or 0), 0),
            )
    except Exception as exc:  # noqa: BLE001
        await answer_error(exc, callback=call)


@router.callback_query(F.data.startswith("uneq:"))
async def cb_unequip(call: CallbackQuery) -> None:
    user = call.from_user
    if user is None or call.data is None:
        return
    parts = call.data.split(":")
    slot_value = parts[1]
    page = parts[2] if len(parts) > 2 else "0"
    item_id = parts[3] if len(parts) > 3 else None
    try:
        await game.unequip_item(user.id, Slot(slot_value))
        await call.answer("آیتم از تنت خارج شد و رفت توی کوله ➖")
        from database.items import ITEMS_BY_ID

        player = await hydrate(user.id, user.full_name, user.username)
        item = ITEMS_BY_ID.get(item_id) if item_id else None
        if item is not None:
            buttons = [
                [
                    InlineKeyboardButton(
                        text="➕ تجهیز / استفاده",
                        callback_data=f"eq:{item_id}:{page}",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="◀️ کوله‌پشتی", callback_data=f"inv:{page}"
                    ),
                    InlineKeyboardButton(text="🏠 کارت من", callback_data="act:me"),
                ],
            ]
            msg = editable_message(call)
            if msg and msg.photo:
                photo = await card_bytes(player)
                await render_panel(
                    msg,
                    text=msg.caption,
                    photo=photo,
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
                    force_media=True,
                )
            else:
                await refresh_markup(
                    msg,
                    InlineKeyboardMarkup(inline_keyboard=buttons),
                )
        else:
            await refresh_markup(
                editable_message(call),
                await _inventory_markup(player, int(page or 0), 0),
            )
    except Exception as exc:  # noqa: BLE001
        await answer_error(exc, callback=call)


# ---------------------------------------------------------------------------
# /inventory command alias
# ---------------------------------------------------------------------------


@router.message(CommandOrText(["inventory", "inv"], INVENTORY_COMMANDS))
async def cmd_inventory(message: Message) -> None:
    user = message.from_user
    if user is None:
        return
    try:
        player = await hydrate(user.id, user.full_name, user.username)
        markup = await _inventory_markup(player, 0, 0)
        await message.reply(
            f"🎒 <b>کوله‌پشتی</b> — {esc(player.display_tag)}",
            reply_markup=markup,
        )
    except Exception as exc:  # noqa: BLE001
        await answer_error(exc, message=message)


@router.callback_query(F.data == "inv:noop")
async def cb_inventory_noop(call: CallbackQuery) -> None:
    await call.answer()


@router.callback_query(F.data == "act:bal")
async def cb_balance(call: CallbackQuery) -> None:
    user = call.from_user
    if user is None:
        return
    try:
        credits, shards = await economy.balances(user.id)
        await call.answer(
            f"💰 {credits:,} سکه · 💎 {shards} شارد روح", show_alert=True
        )
    except Exception as exc:  # noqa: BLE001
        await answer_error(exc, callback=call)


# ---------------------------------------------------------------------------
# Tiramix City Services: Barber, Beauty Surgery & Clinic
# ---------------------------------------------------------------------------

@router.message(CommandOrText(["barber"], {"آرایشگاه", "سلمونی", "مو"}))
async def cmd_barber(message: Message) -> None:
    user = message.from_user
    if user is None:
        return
    try:
        player = await hydrate(user.id, user.full_name, user.username)
        from services import tiramix
        text = (
            "✂️ <b>به آرایشگاه و سالن پیرایش تیرامیکس خوش آمدید!</b>\n\n"
            f"👤 مدل موی فعلی: <b>{player.hair_style}</b> ({player.hair_color})\n"
            f"💵 هزینه تغییر مدل و رنگ مو: <b>{tiramix.HAIRSTYLE_FEES:,}</b> سکه\n\n"
            "برای تغییر فوری مدل مو به همراه رنگ دلخواه، از دستور زیر استفاده کنید:\n"
            "<code>آرایشگاه [مدل 1 تا 5] [مشکی/نقره‌ای/قهوه‌ای]</code>\n"
            "<i>مثال: آرایشگاه 2 مشکی</i>"
        )
        parts = (message.text or "").strip().split()
        if len(parts) >= 3:
            s_num = parts[1]
            c_fa = parts[2]
            color_map = {"مشکی": "black", "نقره‌ای": "silver", "نقره": "silver", "قهوه‌ای": "brown", "قهوه": "brown"}
            c_key = color_map.get(c_fa, "black")
            s_key = f"hair{s_num}" if s_num in ("1", "2", "3", "4", "5") else "hair1"
            res = await tiramix.update_hairstyle(player, s_key, c_key)
            await message.reply(res["message"])
            return

        await message.reply(text)
    except Exception as exc:  # noqa: BLE001
        await answer_error(exc, message=message)


@router.message(CommandOrText(["beauty"], {"زیبایی", "جراحی", "عمل"}))
async def cmd_beauty(message: Message) -> None:
    user = message.from_user
    if user is None:
        return
    try:
        player = await hydrate(user.id, user.full_name, user.username)
        from services import tiramix
        text = (
            "💎 <b>به کلینیک فوق تخصصی زیبایی و جراحی پلاستیک تیرامیکس خوش آمدید!</b>\n\n"
            f"💵 هزینه جراحی کامل چهره: <b>{tiramix.SURGERY_FEES:,}</b> سکه\n\n"
            "برای جراحی و تغییر چشم و دهان از فرمول زیر استفاده کنید:\n"
            "<code>زیبایی [چشم 1 تا 4] [رنگ] [لبخند 1 تا 4]</code>\n"
            "<i>رنگ‌های مجاز: آبی، سبز، عسلی، بنفش، مشکی</i>\n"
            "<i>مثال: زیبایی 2 آبی 1</i>"
        )
        parts = (message.text or "").strip().split()
        if len(parts) >= 4:
            e_num = parts[1] if parts[1] in ("1", "2", "3", "4") else "1"
            col_fa = parts[2]
            m_num = parts[3] if parts[3] in ("1", "2", "3", "4") else "1"
            color_map = {"آبی": "blue", "سبز": "green", "عسلی": "amber", "بنفش": "violet", "مشکی": "black"}
            col_key = color_map.get(col_fa, "blue")
            e_key = f"eyes{e_num}_1"
            m_key = f"mouth{m_num}_1"
            res = await tiramix.update_facial_surgery(player, e_key, col_key, m_key)
            await message.reply(res["message"])
            return

        await message.reply(text)
    except Exception as exc:  # noqa: BLE001
        await answer_error(exc, message=message)


@router.message(CommandOrText(["clinic"], {"کلینیک", "بیمارستان", "درمانگاه", "دکتر"}))
async def cmd_clinic(message: Message) -> None:
    user = message.from_user
    if user is None:
        return
    try:
        player = await hydrate(user.id, user.full_name, user.username)
        from services import tiramix
        res = await tiramix.clinic_service(player)
        await message.reply(res["message"])
    except Exception as exc:  # noqa: BLE001
        await answer_error(exc, message=message)

