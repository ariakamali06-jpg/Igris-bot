"""بازارچه — player-to-player listings with item escrow.

A listed item leaves the seller's ``inventory`` immediately and only lands
in the buyer's inventory when the sale resolves inside the same
``db.write()`` transaction as both wallet moves, so nothing can be sold
twice or vanish mid-deal.
"""

from __future__ import annotations

import contextlib
import json
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
    if item_id.startswith("wdr:"):
        try:
            var_id = int(item_id.split(":")[1])
            from services import catalog
            it = catalog.get_catalog_item_by_var_id(var_id)
            return f"👗 {it['name']}" if it else f"👗 لباس #{var_id}"
        except Exception:
            return f"👗 لباس {item_id}"
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
        f"{row['price']:,} رکسار · فروشنده: {row['seller']}"
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
    """Escrow one owned item (RPG item or wardrobe clothing) into an active listing."""
    if not settings.bazaar_price_min <= price <= settings.bazaar_price_max:
        raise GameError(
            f"قیمت باید بین {settings.bazaar_price_min:,} و "
            f"{settings.bazaar_price_max:,} رکسار باشه."
        )

    clean_query = query.strip()
    is_wdr = clean_query.startswith("wdr:") or (clean_query.isdigit() and len(clean_query) > 3)

    if is_wdr:
        var_id_str = clean_query.replace("wdr:", "")
        try:
            var_id = int(var_id_str)
        except ValueError:
            raise GameError("شناسه لباس نامعتبر است.") from None

        from services import avatar
        if not await avatar.owns_item(user_id, var_id):
            raise GameError("این لباس در کمد شما نیست.")

        # Unequip before taking write lock if equipped
        av = await avatar.get_player_avatar(user_id)
        if var_id in av.get("equipped_var_ids", []):
            with contextlib.suppress(Exception):
                await avatar.unequip_single_item(user_id, var_id)

        item_id = f"wdr:{var_id}"
        item_name = _item_name(item_id)

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

            # Remove from wardrobe using conn directly
            virtual_set_id = 1000000 + var_id
            cur = await conn.execute(
                "DELETE FROM player_wardrobe WHERE user_id = ? AND set_id = ?",
                (user_id, virtual_set_id),
            )
            if cur.rowcount == 0:
                cur = await conn.execute(
                    "SELECT id, var_ids FROM player_wardrobe WHERE user_id = ?",
                    (user_id,),
                )
                rows = await cur.fetchall()
                for r in rows:
                    vids = json.loads(r["var_ids"] or "[]")
                    if var_id in vids:
                        new_vids = [v for v in vids if v != var_id]
                        if new_vids:
                            await conn.execute(
                                "UPDATE player_wardrobe SET var_ids = ? WHERE id = ?",
                                (json.dumps(new_vids), r["id"]),
                            )
                        else:
                            await conn.execute(
                                "DELETE FROM player_wardrobe WHERE id = ?",
                                (r["id"],),
                            )
                        break

            # Ensure items row exists to satisfy foreign key constraint on bazaar_listings(item_id)
            await conn.execute(
                """
                INSERT OR IGNORE INTO items
                (id, name, slot, rarity, atk, defense, drip,
                 price_credits, price_soul_shards, layer_key, description, shop_pool)
                VALUES (?, ?, 'accessory', 'common', 0, 0, 0, 0, 0, ?, 'Wardrobe item', 'rotating')
                """,
                (item_id, item_name, f"wdr_{var_id}"),
            )

            cursor = await conn.execute(
                "INSERT INTO bazaar_listings (seller_id, item_id, price, created_at) "
                "VALUES (?, ?, ?, ?)",
                (user_id, item_id, price, int(time.time())),
            )
            listing_id = int(cursor.lastrowid or 0)
            await cursor.close()

        avatar.invalidate_avatar_cache(user_id)
        return {
            "success": True,
            "listing_id": listing_id,
            "item_id": item_id,
            "price": price,
            "message": (
                f"📋 آگهی #{listing_id} ثبت شد — <b>{_item_name(item_id)}</b> "
                f"به قیمت <b>{price:,}</b> رکسار (تا زمان فروش در بازارچه امانت است)."
            ),
        }

    # 2. Regular RPG inventory item
    item = find_item(clean_query)
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
            f"به قیمت <b>{price:,}</b> رکسار (تا زمان فروش در بازارچه امانت است)."
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

        # Check buyer funds
        cursor = await conn.execute(
            "SELECT credits FROM wallets WHERE user_id = ?", (buyer_id,)
        )
        wrow = await cursor.fetchone()
        await cursor.close()
        buyer_cr = int(wrow["credits"]) if wrow else 0
        if buyer_cr < price:
            raise GameError(
                f"موجودی شما کافی نیست! (قیمت: {price:,} رکسار - موجودی شما: {buyer_cr:,} رکسار)"
            )

        await conn.execute(
            "UPDATE bazaar_listings SET status = 'sold', buyer_id = ?, "
            "resolved_at = ? WHERE id = ?",
            (buyer_id, int(time.time()), listing_id),
        )

        # Grant item to buyer directly on connection
        if item_id.startswith("wdr:"):
            var_id = int(item_id.split(":")[1])
            from services import avatar, catalog
            it_info = catalog.get_catalog_item_by_var_id(var_id)
            item_title = f"✨ {it_info['name']}" if it_info else f"✨ لباس #{var_id}"
            item_gender = it_info.get("gender", "unisex") if it_info else "unisex"
            virtual_set_id = 1000000 + var_id
            now_ts = int(time.time())

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
                (
                    buyer_id,
                    virtual_set_id,
                    item_title,
                    item_gender,
                    json.dumps([var_id]),
                    price,
                    now_ts,
                ),
            )
        else:
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

    if item_id.startswith("wdr:"):
        from services import avatar
        avatar.invalidate_avatar_cache(buyer_id)

    return {
        "success": True,
        "listing_id": listing_id,
        "item_id": item_id,
        "price": price,
        "message": (
            f"🤝 معامله #{listing_id} انجام شد — <b>{_item_name(item_id)}</b> "
            f"به خریدار تحویل داده شد و <b>{price:,}</b> رکسار به فروشنده واریز شد."
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
            raise GameError("این آگهی مال شما نیست.")
        if row["status"] != "active":
            raise GameError("این آگهی قبلاً بسته شده.")

        item_id = str(row["item_id"])
        await conn.execute(
            "UPDATE bazaar_listings SET status = 'cancelled', resolved_at = ? "
            "WHERE id = ?",
            (int(time.time()), listing_id),
        )

        if item_id.startswith("wdr:"):
            var_id = int(item_id.split(":")[1])
            from services import catalog
            it_info = catalog.get_catalog_item_by_var_id(var_id)
            item_title = f"✨ {it_info['name']}" if it_info else f"✨ لباس #{var_id}"
            item_gender = it_info.get("gender", "unisex") if it_info else "unisex"
            virtual_set_id = 1000000 + var_id
            now_ts = int(time.time())

            await conn.execute(
                """
                INSERT INTO player_wardrobe
                    (user_id, set_id, set_name, gender, item_count,
                     var_ids, is_plus_size, price_paid, acquired_at)
                VALUES (?, ?, ?, ?, 1, ?, 0, 0, ?)
                ON CONFLICT(user_id, set_id) DO UPDATE SET
                    var_ids = excluded.var_ids,
                    acquired_at = excluded.acquired_at
                """,
                (user_id, virtual_set_id, item_title, item_gender, json.dumps([var_id]), now_ts),
            )
        else:
            await conn.execute(
                "INSERT INTO inventory (user_id, item_id, acquired_at) VALUES (?, ?, ?)",
                (user_id, item_id, int(time.time())),
            )

    if item_id.startswith("wdr:"):
        from services import avatar
        avatar.invalidate_avatar_cache(user_id)

    return {
        "success": True,
        "listing_id": listing_id,
        "message": (
            f"↩️ آگهی #{listing_id} لغو شد — "
            f"<b>{_item_name(item_id)}</b> به حساب شما بازگردانده شد."
        ),
    }
