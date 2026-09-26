"""/shop — daily rotating boutique + permanent staples, buy via inline keys.

Stock is date-seeded (see ``services/shop.py``), so the handler is mostly
presentation: render today's picks, attach a Buy button per item, and settle
purchases inside a single transaction (debit + grant + ledger + purchase row).
"""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from config import settings
from handlers.common import answer_error, editable_message, hydrate
from handlers.panel import render_panel
from models import Player
from services import economy, shop
from services.shop import AlreadyOwned

logger = logging.getLogger(__name__)
router = Router(name="shop")

_PAGE_SIZE = 5


async def _shop_page(player: Player, page: int) -> tuple[str, InlineKeyboardMarkup]:
    stock = await shop.today_stock()
    pages = max(1, (len(stock) + _PAGE_SIZE - 1) // _PAGE_SIZE)
    page = max(0, min(page, pages - 1))
    slice_ = stock[page * _PAGE_SIZE : (page + 1) * _PAGE_SIZE]

    owned_row = await _owned_ids(player.user_id)
    owned = set(owned_row)

    credits, shards = await economy.balances(player.user_id)
    discount = int(economy.drip_discount(player.drip) * 100)

    lines = [
        "🏪 <b>Boutique</b> — rotating stock, resets 00:00 UTC",
        f"💰 {credits:,}cr · 💎 {shards}◆"
        + (f" · ✂️ DRIP −{discount}% off" if discount else ""),
        "",
    ]
    buttons: list[list[InlineKeyboardButton]] = []
    for item in slice_:
        lines.append(shop.format_item_line(item, player.drip))
        if item.id in owned:
            buttons.append(
                [
                    InlineKeyboardButton(
                        text=f"✅ {item.name} (owned)",
                        callback_data=f"shop:own:{item.id}",
                    )
                ]
            )
        else:
            price, shard_price = await shop.price_for(item.id, player.drip)
            label = "FREE"
            if price:
                label = f"{price:,}cr"
            if shard_price:
                label += f" +{shard_price}◆"
            buttons.append(
                [
                    InlineKeyboardButton(
                        text=f"🛍 {item.name} — {label}",
                        callback_data=f"shop:buy:{item.id}:{page}",
                    )
                ]
            )

    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="◀️", callback_data=f"shop:{page - 1}"))
    nav.append(
        InlineKeyboardButton(text=f"{page + 1}/{pages}", callback_data="shop:noop")
    )
    if page < pages - 1:
        nav.append(InlineKeyboardButton(text="▶️", callback_data=f"shop:{page + 1}"))
    if nav:
        buttons.append(nav)
    buttons.append(
        [InlineKeyboardButton(text="🏠 Back to card", callback_data="act:me")]
    )
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=buttons)


async def _owned_ids(user_id: int) -> list[str]:
    from database.connection import db

    rows = await db.fetchall(
        "SELECT item_id FROM inventory WHERE user_id = ?", (user_id,)
    )
    return [row["item_id"] for row in rows]


@router.message(Command("shop", "store", "market"))
async def cmd_shop(message: Message) -> None:
    user = message.from_user
    if user is None:
        return
    try:
        player = await hydrate(user.id, user.full_name, user.username)
        text, markup = await _shop_page(player, 0)
        await message.reply(text, reply_markup=markup)
    except Exception as exc:  # noqa: BLE001
        await answer_error(exc, message=message)


@router.callback_query(F.data.startswith("shop:"))
async def cb_shop(call: CallbackQuery) -> None:
    user = call.from_user
    if user is None or call.data is None:
        return
    payload = call.data.split(":")[1:]
    action = payload[0] if payload else "0"

    try:
        if action == "noop":
            await call.answer()
            return

        if action == "own":
            await call.answer("Already in your closet.", show_alert=True)
            return

        if action == "buy":
            item_id = payload[1]
            page = int(payload[2]) if len(payload) > 2 else 0
            player = await hydrate(user.id, user.full_name, user.username)
            economy.require_ready(user.id, "shop", settings.cooldown_shop)
            credits_paid, shards_paid = await shop.buy_item(
                player.user_id, item_id, player.drip
            )
            await call.answer(
                f"Purchased! −{credits_paid:,}cr"
                + (f" −{shards_paid}◆" if shards_paid else ""),
                show_alert=True,
            )
            # Re-render the page so the bought row flips to "owned".
            fresh = await hydrate(user.id, user.full_name, user.username)
            text, markup = await _shop_page(fresh, page)
            # The card this hangs off is a photo message: render_panel picks
            # edit_caption vs edit_text, a bare edit_text broke the button.
            await render_panel(
                editable_message(call), text=text, reply_markup=markup
            )
            return

        # Plain pagination (payload[0] is a page number).
        page = int(action)
        player = await hydrate(user.id, user.full_name, user.username)
        await call.answer()  # instant ack before the stock queries
        text, markup = await _shop_page(player, page)
        await render_panel(
            editable_message(call), text=text, reply_markup=markup
        )
    except AlreadyOwned:
        await call.answer("Already yours.", show_alert=True)
    except Exception as exc:  # noqa: BLE001
        await answer_error(exc, callback=call)
