"""کاسب — the fence's shelf: tools the boutique would never stock.

Stock lives in ``database/items.py`` under ``shop_pool="blackmarket"`` so
the daily boutique rotation never picks it up; purchases reuse
:func:`services.shop.buy_item` for the atomic escrow + ledger handling.
"""

from __future__ import annotations

import logging
from typing import Any

from database.items import ITEMS, ItemDef
from services import shop
from services.game import GameError

logger = logging.getLogger(__name__)

FENCE_POOL = "blackmarket"


def fence_stock() -> list[ItemDef]:
    """Everything on the fence's blanket — a deliberately small shelf."""
    return [item for item in ITEMS if item.shop_pool == FENCE_POOL]


def find_stock(query: str) -> ItemDef | None:
    """Resolve a shelf item by Persian name or id; ``None`` when ambiguous."""
    needle = query.strip().casefold()
    if not needle:
        return None
    stock = fence_stock()
    exact = [
        item
        for item in stock
        if item.id.casefold() == needle or item.name.casefold() == needle
    ]
    if exact:
        return exact[0]
    loose = [
        item
        for item in stock
        if needle in item.id.casefold() or needle in item.name.casefold()
    ]
    return loose[0] if len(loose) == 1 else None


def _price_line(item: ItemDef) -> str:
    return f"• <b>{item.name}</b> — {item.price_credits:,} سکه · <i>{item.description}</i>"


async def shelf() -> dict[str, Any]:
    """The fence's list — names, prices, no pretty shop window."""
    stock = fence_stock()
    lines = [_price_line(item) for item in stock]
    body = "\n".join(lines) if lines else "بسته خالیه؛ امروز جنسی نیومده."
    return {
        "success": True,
        "message": (
            "🕳 <b>بساط کاسب زیر پل</b>\n"
            "کسی اینجا رو نمی‌شناسه، جز آدم‌های بد.\n\n"
            f"{body}\n\n"
            "خرید: <code>کاسب خرید [نام]</code>"
        ),
    }


async def buy(user_id: int, query: str, drip: int) -> dict[str, Any]:
    """Buy one fence item; ownership and funds are enforced by the shop."""
    item = find_stock(query)
    if item is None:
        raise GameError("چنین جنسی توی بساط نیست. اسم رو درست بنویس: <code>کاسب</code>")
    try:
        paid, shards = await shop.buy_item(user_id, item.id, drip)
    except shop.AlreadyOwned:
        return {
            "success": False,
            "message": f"ℹ️ <b>{item.name}</b> رو از قبل داری؛ جنس دسته‌دوم مجانی نمی‌شه.",
        }
    return {
        "success": True,
        "item_id": item.id,
        "paid": paid,
        "shards": shards,
        "message": (
            f"🤝 معامله شد — <b>{item.name}</b> افتاد توی کیفت.\n"
            f"پرداخت: <b>{paid:,}</b> سکه | <i>{item.description}</i>"
        ),
    }
