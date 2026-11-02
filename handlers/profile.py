"""/me character card, inventory browsing and equip/unequip callbacks.

The card is the product's heartbeat: render (cache-aware, <50ms) and post a
512px JPEG straight into the group.  Inventory uses paginated inline
keyboards; every press is acked instantly via ``answerCallbackQuery`` so
Telegram never shows the loading spinner.
"""

from __future__ import annotations

import contextlib
import logging
from typing import Any

from aiogram import F, Router
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from config import settings
from handlers.common import (
    CommandOrText,
    answer_error,
    avatar_png_to_card_jpeg,
    card_bytes,
    editable_message,
    esc,
    hydrate,
)
from handlers.panel import refresh_markup, render_panel
from models import Player, Slot
from services import avatar, economy, game

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
    "کوله",
    "کوله پشتی",
    "کوله_پشتی",
    "وسایل",
    "اینونتوری",
    "inventory",
    "inv",
}


async def _profile_markup(
    player_or_tab: Any = "main", active_tab: str = "main"
) -> InlineKeyboardMarkup:
    """چیدمان دکمه‌های شیشه‌ای دسته‌بندی شناسنامه شهروندی."""
    tab = (
        active_tab
        if active_tab != "main"
        else (player_or_tab if isinstance(player_or_tab, str) else "main")
    )
    b_main = "• 👤 شناسنامه •" if tab == "main" else "👤 شناسنامه"
    b_stats = "• 📊 مشخصات •" if tab == "stats" else "📊 مشخصات"
    b_job = "• 💼 شغل و تحصیل •" if tab == "job" else "💼 شغل و تحصیل"
    b_assets = "• 🏠 املاک و دارایی •" if tab == "assets" else "🏠 املاک و دارایی"
    b_social = "• 💍 زندگی و کلن •" if tab == "social" else "💍 زندگی و کلن"

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=b_main, callback_data="prof:tab:main"),
                InlineKeyboardButton(text=b_stats, callback_data="prof:tab:stats"),
            ],
            [
                InlineKeyboardButton(text=b_job, callback_data="prof:tab:job"),
                InlineKeyboardButton(text=b_assets, callback_data="prof:tab:assets"),
            ],
            [
                InlineKeyboardButton(text=b_social, callback_data="prof:tab:social"),
                InlineKeyboardButton(text="🎒 کمد من", callback_data="wdr:view:0"),
            ],
            [
                InlineKeyboardButton(text="🍔 سفارش غذا", callback_data="food:menu"),
                InlineKeyboardButton(text="🔄 به‌روزرسانی", callback_data=f"prof:tab:{tab}"),
            ],
        ]
    )


async def _profile_panel(player: Player, active_tab: str = "main") -> tuple[str, bytes]:
    """
    تولید متن دسته‌بندی‌شده شناسنامه شهروندی و دریافت تصویر زنده آواتار کاراکتر.
    """
    import time

    from services import properties, tiramix

    # ۱. دریافت تصویر آواتار اوراسکایز با کیفیت ۴ برابری و پس‌زمینه شفاف
    photo: bytes | None = None
    try:
        render_url = await avatar.get_player_render_url(player.user_id)
        if render_url:
            png_bytes = await avatar.fetch_upscaled_avatar(render_url, scale=4)
            if png_bytes:
                photo = avatar_png_to_card_jpeg(png_bytes)
    except Exception as exc:
        logger.warning(
            "خطا در دریافت آواتار زنده اوراسکایز برای بازیکن %s: %s",
            player.user_id,
            exc,
        )

    # ۲. فال‌بک امن: در صورت نبود عکس، از کارت محلی کامپوزیتور استفاده می‌شود
    if not photo:
        photo = await card_bytes(player)

    credits, _ = await economy.balances(player.user_id)
    now = int(time.time())

    jail_rem = int((player.is_jailed_until - now) // 60)
    jail_status = (
        f"🔒 حبس در بازداشتگاه ({jail_rem} دقیقه)"
        if player.is_in_jail(now)
        else "🟢 آزاد"
    )
    pregnancy_status = "🤰 باردار" if player.is_pregnant(now) else ""
    marital_status = "💍 متأهل" if player.spouse_id else "مجرد"

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

    children_str = f"👶 <b>فرزندان:</b> {player.children_count} {pregnancy_status}".strip()

    job_info = tiramix.JOBS_CATALOG.get(player.job)
    job_wage = job_info["wage"] if job_info else 120

    p_name = esc(player.username or player.display_name)
    if active_tab == "stats":
        lines = [
            "📊 <b>آمار مبارزه، مشخصات فیزیکی و توانمندی‌ها</b>",
            "━━━━━━━━━━━━━━━━━━━━",
            f"👤 <b>شهروند:</b> {p_name} (لول <b>{player.level}</b>)",
            f"⚔️ <b>قدرت تهاجمی (ATK):</b> <b>{player.atk}</b>",
            f"🛡 <b>دفاع و استقامت (DEF):</b> <b>{player.defense}</b>",
            f"💎 <b>امتیاز استایل شهری (DRIP):</b> <b>{player.drip}</b>",
            f"⚡️ <b>انرژی فعلی:</b> <b>{player.energy}/100</b>",
            f"🐺 <b>حیوان دست‌آموز (پت):</b> {pet_status}",
            f"🛡 <b>سپر ضدسرقت:</b> {shield_status}",
            f"⚖️ <b>وضعیت قضایی:</b> {jail_status}",
            "━━━━━━━━━━━━━━━━━━━━",
            "💡 <i>برای دیدن سایر اطلاعات، از دکمه‌های زیر استفاده کنید.</i>",
        ]
    elif active_tab == "job":
        lines = [
            "💼 <b>وضعیت شغلی و پیشرفت تحصیلی</b>",
            "━━━━━━━━━━━━━━━━━━━━",
            f"👤 <b>شهروند:</b> {p_name}",
            f"💼 <b>عنوان شغل:</b> <b>{player.job}</b>",
            f"💵 <b>دستمزد پایه:</b> <b>{job_wage:,}</b> رکسار در ساعت",
            f"🔨 <b>شیفت‌های کاری انجام‌شده:</b> <b>{player.work_count}</b> بار",
            f"🎓 <b>مدرک تحصیلی:</b> <b>{player.education_title}</b>",
            f"📚 <b>وضعیت دوره:</b> {player.education_progress_text}",
            "━━━━━━━━━━━━━━━━━━━━",
            "💡 <i>جهت کار: <code>کار</code> · ارتقای مدرک: <code>تحصیل</code></i>",
        ]
    elif active_tab == "assets":
        owned_props = await properties.owned_rows(player.user_id, properties.KIND_PROPERTY)
        owned_workers = await properties.owned_rows(player.user_id, properties.KIND_WORKER)
        accrued = 0
        for r in owned_props:
            entry = properties._entry_or_default(properties.KIND_PROPERTY, r["name"])
            accrued += properties.pending_income(
                entry["income"], r["level"], r["last_collect"], now
            )
        for r in owned_workers:
            entry = properties._entry_or_default(properties.KIND_WORKER, r["name"])
            accrued += properties.pending_income(
                entry["income"], r["level"], r["last_collect"], now
            )

        prop_names = "، ".join(r["name"] for r in owned_props) if owned_props else "ندارد"
        worker_names = "، ".join(r["name"] for r in owned_workers) if owned_workers else "ندارد"

        lines = [
            "🏠 <b>املاک، دارایی‌ها و نیروهای تحت امر</b>",
            "━━━━━━━━━━━━━━━━━━━━",
            f"🏢 <b>املاک شخصی ({len(owned_props)} ملک):</b> {prop_names}",
            f"👥 <b>نیروها و نوچه‌ها ({len(owned_workers)} نفر):</b> {worker_names}",
            f"💰 <b>درآمد منفعل انباشته:</b> <b>{accrued:,}</b> رکسار",
            f"🏦 <b>سپرده گاوصندوق بانک:</b> <b>{player.bank_balance:,}</b> رکسار",
            f"💵 <b>پول نقد کیف پول:</b> <b>{credits:,}</b> رکسار",
            "━━━━━━━━━━━━━━━━━━━━",
            "💡 <i>جهت خرید: دستور <code>املاک</code> یا <code>نوچه</code></i>",
        ]
    elif active_tab == "social":
        lines = [
            "💍 <b>زندگی اجتماعی، خانواده و سندیکا</b>",
            "━━━━━━━━━━━━━━━━━━━━",
            f"👤 <b>شهروند:</b> {p_name}",
            f"💍 <b>وضعیت تأهل:</b> {marital_status}",
            f"{children_str}",
            f"🛡 <b>کلن و سندیکا:</b> {clan_name}",
            f"🤫 <b>سوابق خیانت مخفی:</b> {player.affair_count} بار",
            f"⚖️ <b>وضعیت آزادی:</b> {jail_status}",
            "━━━━━━━━━━━━━━━━━━━━",
            "💡 <i>دستورات ازدواج، طلاق و کلن در راهنما در دسترس است.</i>",
        ]
    else:  # main summary tab
        lines = [
            "🍁 <b>شناسنامه شهروندی شهر تیرامیکس</b>",
            "━━━━━━━━━━━━━━━━━━━━",
            f"👤 <b>نام:</b> {p_name} (لول <b>{player.level}</b>)",
            f"🎂 <b>سن:</b> {player.age} سال | ⚧ <b>جنسیت:</b> {player.gender}",
            f"⚡️ <b>سطح انرژی:</b> <b>{player.energy}/100</b>",
            f"💼 <b>شغل:</b> {player.job} | 🎓 <b>مدرک:</b> {player.education_title}",
            (
                f"💰 <b>کیف پول:</b> <b>{credits:,}</b> رکسار | "
                f"🏦 <b>بانک:</b> <b>{player.bank_balance:,}</b> رکسار"
            ),
            "━━━━━━━━━━━━━━━━━━━━",
            "👇 <i>برای مشاهده جزئیات بیشتر، بخش مورد نظر را لمس کنید:</i>",
        ]

    return "\n".join(lines), photo


async def _send_profile(target: Message, player: Player) -> None:
    """ارسال کارت شناسنامه شهروندی به صورت عکس شفاف با کپشن دسته‌بندی‌شده و دکمه‌های شیشه‌ای."""
    caption, photo = await _profile_panel(player, active_tab="main")
    markup = await _profile_markup(active_tab="main")
    try:
        await target.reply_photo(
            photo=BufferedInputFile(photo, filename="character.png"),
            caption=caption,
            reply_markup=markup,
        )
    except Exception:
        if getattr(target, "bot", None):
            await target.bot.send_photo(
                chat_id=target.chat.id,
                photo=BufferedInputFile(photo, filename="character.png"),
                caption=caption,
                reply_markup=markup,
            )


async def _show_profile(message: Message | None, player: Player) -> None:
    """Render the card into an existing panel (photo-aware, never dead)."""
    if message is None:
        return
    caption, photo = await _profile_panel(player, active_tab="main")
    markup = await _profile_markup(active_tab="main")
    await render_panel(
        message,
        text=caption,
        photo=photo,
        reply_markup=markup,
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
        "🏛 <b>سیستم‌های فعال:</b> بانک، وام و گاوصندوق، زندان، دادگاه خانواده، کلن‌ها، ازدواج\n\n"
        "📝 <b>لیست آخرین تغییرات اعمال‌شده در نسخه ۲.۰.۰:</b>\n"
        "├ 🍁 <b>تغییر کامل هویت و تم:</b> مهاجرت به جهان مدرن، لوفای و پاییزی شهر تیرامیکس\n"
        "├ 🎴 <b>شناسنامه تمیز متنی:</b> حذف تمام دکمه‌های شلوغ پروفایل و صدور کارت شهروندی\n"
        "├ 🚂 <b>ویزارد ۹ مرحله‌ای ثبت‌نام:</b> سناریوی قطار با شخصی‌سازی رنگ پوست، چشم، مو و لب\n"
        "├ 💼 <b>خدمات و اقتصاد شهری:</b> دستورات کار، تحصیل، شغل، بانک، کمد، آرایشگاه، کلینیک\n"
        "├ 🛍 <b>دسته‌بندی بوتیک مد:</b> دسته‌بندی اینلاین لباس‌ها و ویترین روزانه\n"
        "├ ⚖️ <b>دادگاه خانواده تیرامیکس:</b> طلاق توافقی یا ارجاع به دادگاه با قضاوت تصادفی\n"
        "├ 💍 <b>روابط زناشویی و فرزند:</b> ازدواج، رابطه، شانس بارداری و نقاهت کلینیک\n"
        "├ 🥷 <b>دزدی و مجازات قضایی:</b> غارت موجودی نقد، جریمه ۵ برابری و حبس در بازداشتگاه\n"
        "└ 🛡 <b>سیستم کلن‌ها و سندیکاها:</b> تشکیل کلن‌های محلی درون گروه‌ها با خزانه و اعضا"
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


@router.callback_query(F.data.startswith("prof:tab:"))
async def cb_profile_tab(call: CallbackQuery) -> None:
    """تغییر دسته‌بندی و تب اطلاعات شناسنامه شهروندی با دکمه‌های شیشه‌ای."""
    user = call.from_user
    if user is None:
        return
    parts = (call.data or "").split(":")
    tab = parts[2] if len(parts) > 2 else "main"

    try:
        player = await hydrate(user.id, user.full_name, user.username)
        caption, _ = await _profile_panel(player, active_tab=tab)
        markup = await _profile_markup(active_tab=tab)

        msg = editable_message(call)
        if msg:
            if msg.photo or msg.caption:
                with contextlib.suppress(Exception):
                    await msg.edit_caption(
                        caption=caption, reply_markup=markup, parse_mode="HTML"
                    )
            else:
                with contextlib.suppress(Exception):
                    await msg.edit_text(text=caption, reply_markup=markup, parse_mode="HTML")
        await call.answer()
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
        stats_str = (
            f"⚔️ قدرت: <b>{player.atk}</b> · "
            f"🛡 دفاع: <b>{player.defense}</b> · "
            f"💎 استایل: <b>{player.drip}</b>"
        )
        text = f"🎒 <b>کوله‌پشتی</b> — {esc(player.display_tag)}\n{stats_str}"
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
            f"💰 {credits:,} رکسار", show_alert=True
        )
    except Exception as exc:  # noqa: BLE001
        await answer_error(exc, callback=call)


# ---------------------------------------------------------------------------
# Tiramix City Services: Clinic
# (Barber, Solarium & Makeup are managed in handlers.salon)
# ---------------------------------------------------------------------------

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

