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
from handlers.common import CommandOrText, answer_error, editable_message, hydrate
from handlers.panel import render_panel
from models.enums import Slot
from models.player import Player
from services import economy, shop
from services.shop import AlreadyOwned

logger = logging.getLogger(__name__)
router = Router(name="shop")

_PAGE_SIZE = 5


SHOP_COMMANDS = {"شاپ", "فروشگاه", "خرید", "بازار", "shop", "store", "market"}


async def _shop_page(player: Player, page: int, category: str = "all") -> tuple[str, InlineKeyboardMarkup]:
    if category == "all":
        stock = await shop.today_stock()
        cat_title = "ویترین روزانه شهر تیرامیکس"
    elif category == "body":
        stock = shop.slot_catalog(Slot.BODY)
        cat_title = "دسته پیراهن و بالاتنه"
    elif category == "legs":
        stock = shop.slot_catalog(Slot.LEGS)
        cat_title = "دسته شلوار و پایین‌تنه"
    elif category == "head":
        stock = shop.slot_catalog(Slot.HEAD)
        cat_title = "دسته کلاه و سر"
    else:
        stock = shop.slot_catalog(Slot.WEAPON) + shop.slot_catalog(Slot.ACCESSORY)
        cat_title = "دسته سلاح و اکسسوری"

    pages = max(1, (len(stock) + _PAGE_SIZE - 1) // _PAGE_SIZE)
    page = max(0, min(page, pages - 1))
    slice_ = stock[page * _PAGE_SIZE : (page + 1) * _PAGE_SIZE]

    owned_row = await _owned_ids(player.user_id)
    owned = set(owned_row)

    credits, shards = await economy.balances(player.user_id)
    discount = int(economy.drip_discount(player.drip) * 100)

    lines = [
        f"🏪 <b>بوتیک خیابانی و مرکز مد تیرامیکس</b> — {cat_title}",
        f"💰 موجودی: <b>{credits:,}</b> سکه · 💎 <b>{shards}</b> شارد روح"
        + (f" · ✂️ تخفیف استایل: <b>{discount}%</b>" if discount else ""),
        "",
    ]

    buttons: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(text="👕 بالاتنه", callback_data="shop:cat:body:0"),
            InlineKeyboardButton(text="👖 شلوار/دامن", callback_data="shop:cat:legs:0"),
        ],
        [
            InlineKeyboardButton(text="🎩 کلاه/سر", callback_data="shop:cat:head:0"),
            InlineKeyboardButton(text="🗡 اکسسوری", callback_data="shop:cat:acc:0"),
        ],
        [
            InlineKeyboardButton(text="🏪 ویترین روزانه (همه)", callback_data="shop:cat:all:0"),
        ],
    ]

    for item in slice_:
        lines.append(shop.format_item_line(item, player.drip))
        if item.id in owned:
            buttons.append(
                [
                    InlineKeyboardButton(
                        text=f"✅ {item.name} (داریش)",
                        callback_data=f"shop:own:{item.id}",
                    )
                ]
            )
        else:
            price, shard_price = await shop.price_for(item.id, player.drip)
            label = "رایگان"
            if price:
                label = f"{price:,} سکه"
            if shard_price:
                label += f" + {shard_price} شارد"
            buttons.append(
                [
                    InlineKeyboardButton(
                        text=f"🛍 {item.name} — {label}",
                        callback_data=f"shop:buy:{item.id}:{page}:{category}",
                    )
                ]
            )

    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="◀️", callback_data=f"shop:cat:{category}:{page - 1}"))
    nav.append(
        InlineKeyboardButton(text=f"{page + 1}/{pages}", callback_data="shop:noop")
    )
    if page < pages - 1:
        nav.append(InlineKeyboardButton(text="▶️", callback_data=f"shop:cat:{category}:{page + 1}"))
    if nav:
        buttons.append(nav)
    buttons.append(
        [InlineKeyboardButton(text="🏠 کارت من", callback_data="act:me")]
    )

    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=buttons)


async def _owned_ids(user_id: int) -> list[str]:
    from database.connection import db

    rows = await db.fetchall(
        "SELECT item_id FROM inventory WHERE user_id = ?", (user_id,)
    )
    return [row["item_id"] for row in rows]


@router.message(CommandOrText(["shop", "store", "market"], SHOP_COMMANDS))
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
            await call.answer("این آیتم رو قبلاً خریدی!", show_alert=True)
            return

        if action == "cat":
            category = payload[1] if len(payload) > 1 else "all"
            page = int(payload[2]) if len(payload) > 2 else 0
            await call.answer()
            player = await hydrate(user.id, user.full_name, user.username)
            text, markup = await _shop_page(player, page, category=category)
            await render_panel(
                editable_message(call), text=text, reply_markup=markup
            )
            return

        if action == "buy":
            item_id = payload[1]
            page = int(payload[2]) if len(payload) > 2 else 0
            category = payload[3] if len(payload) > 3 else "all"
            player = await hydrate(user.id, user.full_name, user.username)
            economy.require_ready(user.id, "shop", settings.cooldown_shop)
            credits_paid, shards_paid = await shop.buy_item(
                player.user_id, item_id, player.drip
            )
            await call.answer(
                f"خریداری شد! ✅ −{credits_paid:,} سکه"
                + (f" −{shards_paid} شارد" if shards_paid else ""),
                show_alert=True,
            )
            # Re-render the page so the bought row flips to "owned".
            fresh = await hydrate(user.id, user.full_name, user.username)
            text, markup = await _shop_page(fresh, page, category=category)
            await render_panel(
                editable_message(call), text=text, reply_markup=markup
            )
            return

        # Plain pagination (payload[0] is a page number).
        page = int(action)
        await call.answer()  # instant ack before the stock queries
        player = await hydrate(user.id, user.full_name, user.username)
        text, markup = await _shop_page(player, page)
        await render_panel(
            editable_message(call), text=text, reply_markup=markup
        )
    except AlreadyOwned:
        await call.answer("این آیتم رو قبلاً گرفتی!", show_alert=True)
    except Exception as exc:  # noqa: BLE001
        await answer_error(exc, callback=call)
