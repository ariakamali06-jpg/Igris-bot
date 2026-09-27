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

logger = logging.getLogger(__name__)
router = Router(name="profile")

_INVENTORY_PAGE_SIZE = 6


# ---------------------------------------------------------------------------
# /me & /profile
# ---------------------------------------------------------------------------

PROFILE_COMMANDS = {"پروفایل", "من", "کارت", "کاراکتر", "مشخصات", "profile", "me", "card"}
INVENTORY_COMMANDS = {"کوله", "کیف", "اینونتوری", "وسایل", "inventory", "inv"}


async def _profile_markup(player: Player) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(text="🎒 کوله‌پشتی", callback_data="inv:0"),
            InlineKeyboardButton(text="🏪 فروشگاه", callback_data="shop:0"),
        ],
        [
            InlineKeyboardButton(text="⚡ کار کردن", callback_data="act:work"),
            InlineKeyboardButton(text="💰 موجودی", callback_data="act:bal"),
        ],
        [
            InlineKeyboardButton(text="📖 راهنمای بازی", callback_data="act:help"),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _profile_panel(player: Player) -> tuple[str, bytes]:
    """The card's caption + rendered bytes, shared by /me and the callback."""
    photo = await card_bytes(player)
    stats = player.stats
    credits, shards = await economy.balances(player.user_id)
    caption = (
        f"🎴 <b>{esc(player.username or player.display_name)}</b> — لول <b>{player.level}</b>\n"
        f"⚡ {energy_bar(player.energy, stats.max_energy)} {player.energy}/{stats.max_energy}\n"
        f"⚔️ قدرت: <b>{player.atk}</b> · 🛡 دفاع: <b>{player.defense}</b> · 💎 استایل: <b>{player.drip}</b>\n"
        f"💰 موجودی: <b>{credits:,}</b> سکه · 💎 <b>{shards}</b> شارد روح\n"
        f"✨ پیشرفت: <b>{player.exp}/{game.exp_to_next(player.level)}</b> EXP"
    )
    return caption, photo


async def _send_profile(target: Message, player: Player) -> None:
    caption, photo = await _profile_panel(player)
    await target.reply_photo(
        photo=BufferedInputFile(photo, filename="character.jpg"),
        caption=caption,
        reply_markup=await _profile_markup(player),
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
        reply_markup=await _profile_markup(player),
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
        await call.answer("تجهیز شد ✅")
        from database.items import ITEMS_BY_ID

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
            await refresh_markup(
                editable_message(call),
                InlineKeyboardMarkup(inline_keyboard=buttons),
            )
        else:
            player = await hydrate(user.id, user.full_name, user.username)
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
        await call.answer("خلع سلاح شد ➖")
        from database.items import ITEMS_BY_ID

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
            await refresh_markup(
                editable_message(call),
                InlineKeyboardMarkup(inline_keyboard=buttons),
            )
        else:
            player = await hydrate(user.id, user.full_name, user.username)
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
