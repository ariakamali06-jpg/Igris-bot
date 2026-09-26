"""Idempotent catalog seeder.

``database/items.ITEMS`` is the single source of truth for gear; this module
mirrors it into the ``items`` table so SQL joins (inventory, shop, loadout)
can resolve stats without going through Python.  Safe to run on every boot:
upserts by id, and removes rows that no longer exist in the catalog so a
balance patch can never leave orphaned gear behind.
"""

from __future__ import annotations

import logging

from database.connection import db
from database.items import ITEMS

logger = logging.getLogger(__name__)


async def seed_catalog() -> int:
    """Upsert the item catalog; returns the number of rows written."""
    kept = 0
    async with db.write() as conn:
        for item in ITEMS:
            await conn.execute(
                """
                INSERT INTO items (id, name, slot, rarity, atk, defense, drip,
                                   price_credits, price_soul_shards, layer_key,
                                   description, shop_pool)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (id) DO UPDATE SET
                    name = excluded.name,
                    slot = excluded.slot,
                    rarity = excluded.rarity,
                    atk = excluded.atk,
                    defense = excluded.defense,
                    drip = excluded.drip,
                    price_credits = excluded.price_credits,
                    price_soul_shards = excluded.price_soul_shards,
                    layer_key = excluded.layer_key,
                    description = excluded.description,
                    shop_pool = excluded.shop_pool
                """,
                (
                    item.id,
                    item.name,
                    item.slot.value,
                    item.rarity.value,
                    item.atk,
                    item.defense,
                    item.drip,
                    item.price_credits,
                    item.price_soul_shards,
                    item.layer_key,
                    item.description,
                    item.shop_pool,
                ),
            )
            kept += 1

        # Drop catalog rows that were removed/renamed upstream. Players keep
        # inventory rows only for live items (FK cascade handles true deletes;
        # this guards against hand-edited catalogs).
        catalog_ids = {item.id for item in ITEMS}
        cursor = await conn.execute("SELECT id FROM items")
        existing = {row["id"] for row in await cursor.fetchall()}
        await cursor.close()
        for stale in existing - catalog_ids:
            await conn.execute("DELETE FROM items WHERE id = ?", (stale,))
            logger.info("removed stale catalog item %s", stale)

    logger.info("catalog seeded: %d items", kept)
    return kept


async def catalog_count() -> int:
    row = await db.fetchone("SELECT COUNT(*) AS n FROM items")
    return row["n"] if row else 0
