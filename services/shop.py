"""Boutique & Black Market: deterministic daily rotation + purchases.

Rotation is *seeded by the calendar date*, so every player in every group
sees the same stock for the day, the stock survives restarts without a cron
job, and the ``shop_rotation`` table is only a cache of
``seed = date + slot`` rather than a mutable source of truth.
"""

from __future__ import annotations

import hashlib
import logging
import random
import sqlite3
from datetime import UTC, datetime

from config import settings
from database.connection import db
from database.items import ITEMS, ITEMS_BY_ID, ItemDef
from models import ActivityKind, Rarity, Slot
from services import economy
from services.game import now

logger = logging.getLogger(__name__)


class AlreadyOwned(ValueError):
    """Player already has this item; not a failure worth a traceback."""


def rotation_key(dt: datetime | None = None) -> str:
    """Stable shop key for a calendar day (UTC)."""
    today = dt or datetime.now(UTC)
    return today.strftime("%Y-%m-%d")


def _pick_rotation(key: str) -> list[ItemDef]:
    """Seeded selection of ``shop_rotation_size`` items from rotating pools."""
    digest = hashlib.sha256(key.encode("utf-8")).digest()
    rng = random.Random(int.from_bytes(digest[:8], "big"))

    rotating = [item for item in ITEMS if item.shop_pool == "rotating"]
    permanent = [item for item in ITEMS if item.shop_pool == "permanent"]
    # 2/3 rotating (the "drops"), 1/3 permanent staples restocked daily.
    n_rotating = min(len(rotating), max(1, settings.shop_rotation_size * 2 // 3))
    n_permanent = max(0, settings.shop_rotation_size - n_rotating)

    picks = rng.sample(rotating, n_rotating)
    if permanent and n_permanent:
        picks += rng.sample(permanent, min(n_permanent, len(permanent)))
    rng.shuffle(picks)
    return picks


async def today_stock(key: str | None = None) -> list[ItemDef]:
    """Return (and cache) today's rotating stock."""
    key = key or rotation_key()

    async with db.read() as conn:
        cursor = await conn.execute(
            """
            SELECT i.id FROM shop_rotation r
            JOIN items i ON i.id = r.item_id
            WHERE r.rotation_key = ? ORDER BY r.slot
            """,
            (key,),
        )
        rows = list(await cursor.fetchall())
        await cursor.close()

    if rows:
        found = [ITEMS_BY_ID[row["id"]] for row in rows if row["id"] in ITEMS_BY_ID]
        if len(found) == len(rows):
            return found

    picks = _pick_rotation(key)
    async with db.write() as conn:
        await conn.execute(
            "DELETE FROM shop_rotation WHERE rotation_key = ?", (key,)
        )
        for slot_index, item in enumerate(picks):
            await conn.execute(
                """
                INSERT OR IGNORE INTO shop_rotation (rotation_key, slot, item_id)
                VALUES (?, ?, ?)
                """,
                (key, slot_index, item.id),
            )
    logger.info("rotated shop stock for %s (%d items)", key, len(picks))
    return picks


async def buy_item(user_id: int, item_id: str, drip: int) -> tuple[int, int]:
    """Purchase + grant + ledger, atomically. Returns (credits paid, shards paid).

    Raises :class:`services.game.InsufficientFunds` (rolls everything back)
    if the wallet cannot cover the discounted price.
    """
    item = ITEMS_BY_ID.get(item_id)
    if item is None:
        raise ValueError(f"unknown item {item_id}")
    if item.shop_pool == "starter":
        raise ValueError("starter items are free — they're already yours")

    credits_paid = economy.discounted_price(item.price_credits, drip)
    shards_paid = item.price_soul_shards

    async with db.write() as conn:
        cursor = await conn.execute(
            "SELECT 1 FROM inventory WHERE user_id = ? AND item_id = ?",
            (user_id, item_id),
        )
        already_owned = await cursor.fetchone() is not None
        await cursor.close()
        if already_owned:
            raise AlreadyOwned(item_id)

        await economy.mutate(
            conn,
            user_id,
            credits=-credits_paid,
            shards=-shards_paid,
            kind=ActivityKind.SHOP_BUY,
            ref=f"buy:{item_id}",
        )
        await conn.execute(
            """
            INSERT INTO inventory (user_id, item_id, acquired_at) VALUES (?, ?, ?)
            ON CONFLICT (user_id, item_id) DO NOTHING
            """,
            (user_id, item_id, now()),
        )
        await conn.execute(
            """
            INSERT INTO purchases (user_id, item_id, price_credits, price_shards,
                                   created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (user_id, item_id, credits_paid, shards_paid, now()),
        )
    return credits_paid, shards_paid


async def price_for(item_id: str, drip: int) -> tuple[int, int]:
    """Live (discounted) prices for rendering shop buttons."""
    item = ITEMS_BY_ID.get(item_id)
    if item is None:
        return 0, 0
    return economy.discounted_price(item.price_credits, drip), item.price_soul_shards


def rarity_badge(rarity: Rarity | str) -> str:
    value = Rarity(rarity)
    return {
        Rarity.COMMON: "▫️",
        Rarity.RARE: "🔹",
        Rarity.EPIC: "🔸",
        Rarity.LEGENDARY: "👑",
    }[value]


def format_item_line(row: sqlite3.Row | ItemDef, drip: int = 0) -> str:
    """One shop/inventory line: badge, name, stat deltas, price."""
    if isinstance(row, ItemDef):
        name, rarity, atk, defense, drip_stats = (
            row.name, row.rarity.value, row.atk, row.defense, row.drip,
        )
        price_credits, price_shards = row.price_credits, row.price_soul_shards
    else:
        name, rarity, atk, defense, drip_stats = (
            row["name"], row["rarity"], row["atk"], row["defense"], row["drip"],
        )
        price_credits, price_shards = row["price_credits"], row["price_soul_shards"]

    stats = " ".join(
        part
        for part in (
            f"قدرت+{atk}" if atk else "",
            f"دفاع+{defense}" if defense else "",
            f"استایل+{drip_stats}" if drip_stats else "",
        )
        if part
    ) or "تزئینی"

    paid = economy.discounted_price(price_credits, drip)
    price_bits: list[str] = []
    if price_credits:
        price_bits.append(f"{paid:,} سکه" + (" ✂️" if paid < price_credits else ""))
    if price_shards:
        price_bits.append(f"{price_shards} شارد")
    price = " · ".join(price_bits) if price_bits else "رایگان"

    return f"{rarity_badge(rarity)} <b>{name}</b> — {stats} · {price}"


def slot_catalog(slot: Slot) -> list[ItemDef]:
    return [item for item in ITEMS if item.slot == slot]


__all__ = [
    "AlreadyOwned",
    "buy_item",
    "format_item_line",
    "price_for",
    "rotation_key",
    "rarity_badge",
    "slot_catalog",
    "today_stock",
]
