"""بازارچه — player-to-player listings with item escrow.

A listed item leaves the seller's ``inventory`` immediately and only lands
in the buyer's inventory when the sale resolves inside the same
``db.write()`` transaction as both wallet moves, so nothing can be sold
twice or vanish mid-deal.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from config import settings
from database.connection import db
from database.items import ITEMS, ITEMS_BY_ID, ItemDef
from models import ActivityKind
from services import economy
from services.game import GameError

logger = logging.getLogger(__name__)


def find_item(query: str) -> ItemDef | None:
    """Resolve any catalog item by Persian name or id (exact, then unique)."""
    needle = query.strip().casefold()
    if not needle:
        return None
    exact = [
        item
        for item in ITEMS
        if item.id.casefold() == needle or item.name.casefold() == needle
    ]
    if exact:
        return exact[0]
    loose = [
        item
        for item in ITEMS
        if needle in item.id.casefold() or needle in item.name.casefold()
    ]
    return loose[0] if len(loose) == 1 else None


def _item_name(item_id: str) -> str:
    item = ITEMS_BY_ID.get(item_id)
    return item.name if item else item_id


async def browse(limit: int = 20) -> dict[str, Any]:
    """Active listings, newest first."""
    async with db.read() as conn:
        cursor = await conn.execute(
            """
            SELECT l.id, l.item_id, l.price, l.created_at, p.display_name AS seller
            FROM bazaar_listings l
            JOIN players p ON p.user_id = l.seller_id
            WHERE l.status = 'active'
            ORDER BY l.id DESC LIMIT ?
            """,
            (limit,),
        )
        rows = [dict(row) for row in await cursor.fetchall()]
        await cursor.close()

    if not rows:
        return {
            "success": True,
            "message": "🧺 بازارچه خالیه — هیچ آگهی فعالی نیست.",
        }
    lines = [
        f"#{row['id']} · <b>{_item_name(row['item_id'])}</b> — "
        f"{row['price']:,} سکه · فروشنده: {row['seller']}"
        for row in rows
    ]
    body = "\n".join(lines)
    return {
        "success": True,
        "listings": rows,
        "message": (
            "🧺 <b>بازارچه کوچه</b>\n"
            f"{body}\n\n"
            "خرید: <code>بازارچه خرید [ردیف]</code>\n"
            "فروش: <code>بازارچه فروش [کالا] [قیمت]</code> · "
            "لغو: <code>بازارچه لغو [ردیف]</code>"
        ),
    }


async def list_item(user_id: int, query: str, price: int) -> dict[str, Any]:
    """Escrow one owned item into an active listing."""
    if not settings.bazaar_price_min <= price <= settings.bazaar_price_max:
        raise GameError(
            f"قیمت باید بین {settings.bazaar_price_min:,} و "
            f"{settings.bazaar_price_max:,} سکه باشه."
        )
    item = find_item(query)
    if item is None:
        raise GameError("چنین کالایی توی کاتالوگ نیست.")

    async with db.write() as conn:
        cursor = await conn.execute(
            "SELECT COUNT(*) AS n FROM bazaar_listings "
            "WHERE seller_id = ? AND status = 'active'",
            (user_id,),
        )
        count_row = await cursor.fetchone()
        await cursor.close()
        if count_row and int(count_row["n"]) >= settings.bazaar_max_active:
            raise GameError(
                f"همزمان حداکثر {settings.bazaar_max_active} آگهی می‌تونی داشته باشی."
            )

        cursor = await conn.execute(
            "DELETE FROM inventory WHERE user_id = ? AND item_id = ?",
            (user_id, item.id),
        )
        escrowed = cursor.rowcount
        await cursor.close()
        if escrowed == 0:
            raise GameError(f"<b>{item.name}</b> توی کیفت نیست.")

        cursor = await conn.execute(
            "INSERT INTO bazaar_listings (seller_id, item_id, price, created_at) "
            "VALUES (?, ?, ?, ?)",
            (user_id, item.id, price, int(time.time())),
        )
        listing_id = int(cursor.lastrowid or 0)
        await cursor.close()

    return {
        "success": True,
        "listing_id": listing_id,
        "item_id": item.id,
        "price": price,
        "message": (
            f"📋 آگهی #{listing_id} ثبت شد — <b>{item.name}</b> "
            f"به قیمت <b>{price:,}</b> سکه (تا فروش رفتن از کیفته)."
        ),
    }


async def buy_listing(buyer_id: int, listing_id: int) -> dict[str, Any]:
    """Pay the seller and hand the escrowed item over, atomically."""
    async with db.write() as conn:
        cursor = await conn.execute(
            "SELECT * FROM bazaar_listings WHERE id = ?", (listing_id,)
        )
        row = await cursor.fetchone()
        await cursor.close()
        if row is None:
            raise GameError("چنین آگهی‌ای پیدا نشد.")
        if row["status"] != "active":
            raise GameError("این آگهی دیگه فعال نیست.")
        if int(row["seller_id"]) == buyer_id:
            raise GameError("این آگهی خودته؛ از خودت خرید نمی‌شه.")

        price = int(row["price"])
        item_id = str(row["item_id"])
        seller_id = int(row["seller_id"])

        await conn.execute(
            "UPDATE bazaar_listings SET status = 'sold', buyer_id = ?, "
            "resolved_at = ? WHERE id = ?",
            (buyer_id, int(time.time()), listing_id),
        )
        await conn.execute(
            "INSERT INTO inventory (user_id, item_id, acquired_at) VALUES (?, ?, ?)",
            (buyer_id, item_id, int(time.time())),
        )
        await economy.mutate(
            conn,
            buyer_id,
            credits=-price,
            kind=ActivityKind.BAZAAR,
            ref=f"bazaar:buy:{listing_id}",
        )
        await economy.mutate(
            conn,
            seller_id,
            credits=price,
            kind=ActivityKind.BAZAAR,
            ref=f"bazaar:sale:{listing_id}",
        )

    return {
        "success": True,
        "listing_id": listing_id,
        "item_id": item_id,
        "price": price,
        "message": (
            f"🤝 معامله #{listing_id} شد — <b>{_item_name(item_id)}</b> "
            f"رفت خریدار، <b>{price:,}</b> سکه رفت فروشنده."
        ),
    }


async def cancel(user_id: int, listing_id: int) -> dict[str, Any]:
    """Pull an active listing and return the escrowed item."""
    async with db.write() as conn:
        cursor = await conn.execute(
            "SELECT * FROM bazaar_listings WHERE id = ?", (listing_id,)
        )
        row = await cursor.fetchone()
        await cursor.close()
        if row is None:
            raise GameError("چنین آگهی‌ای پیدا نشد.")
        if int(row["seller_id"]) != user_id:
            raise GameError("این آگهی مال تو نیست.")
        if row["status"] != "active":
            raise GameError("این آگهی قبلاً بسته شده.")

        item_id = str(row["item_id"])
        await conn.execute(
            "UPDATE bazaar_listings SET status = 'cancelled', resolved_at = ? "
            "WHERE id = ?",
            (int(time.time()), listing_id),
        )
        await conn.execute(
            "INSERT INTO inventory (user_id, item_id, acquired_at) VALUES (?, ?, ?)",
            (user_id, item_id, int(time.time())),
        )

    return {
        "success": True,
        "listing_id": listing_id,
        "message": (
            f"↩️ آگهی #{listing_id} لغو شد — "
            f"<b>{_item_name(item_id)}</b> برگشت توی کیفت."
        ),
    }
