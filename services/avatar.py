"""Everskies Avatar, Boutique & Wardrobe service.

Handles:
- Dynamic CDN rendering URLs via base64 encoded variation arrays.
- Player avatar persistence (skin tones, plus size, active clothing set).
- Deterministic daily boutique rotation from the 30,561 Everskies catalog.
- Wardrobe management, set previewing on player character, and purchases.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import random
import sqlite3
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from config import ROOT_DIR, settings
from database.connection import db
from models.enums import ActivityKind
from services import economy

logger = logging.getLogger(__name__)

# Skin tone definitions (Standard slim: -1 to -6, Plus size: -7 to -12)
SKIN_TONES_SLIM = [
    (-1, "⚪️ خیلی روشن (Fair)"),
    (-2, "🏻 گندمی روشن (Light)"),
    (-3, "🏼 برنزه طبیعی (Natural)"),
    (-4, "🏽 زیتونی / سبزه (Olive)"),
    (-5, "🏾 سبزه تیره (Deep)"),
    (-6, "🏿 تیره / سیاه (Dark)"),
]

SKIN_MAP_BY_NAME = {
    "pale": -1,
    "fair": -2,
    "natural": -3,
    "tan": -4,
    "dark": -5,
    "deep": -6,
}


class AvatarError(ValueError):
    """Base avatar domain error."""


class AlreadyOwned(AvatarError):
    """Player already owns this set."""


class CatalogNotFound(AvatarError):
    """Catalog database is not found."""


def get_catalog_db_path() -> Path:
    """Find the best path for bot_catalog.db."""
    candidates = [
        settings.catalog_db_path,
        ROOT_DIR / "data" / "bot_catalog.db",
        ROOT_DIR / "bot_catalog.db",
        Path("/tmp/bot_catalog.db"),
    ]
    for p in candidates:
        if p and p.exists():
            return p
    return settings.catalog_db_path


def get_render_url(
    variation_ids: list[int],
    width: int = 400,
    crop: bool = False,
    animated: bool = False,
) -> str:
    """Generate an on-demand Everskies CDN composite PNG URL.

    Zero disk overhead: Telegram downloads directly from Everskies CDN.
    """
    if not variation_ids:
        return ""
    raw_b64 = base64.b64encode(json.dumps(variation_ids).encode("utf-8")).decode("utf-8")
    slug = raw_b64.rstrip("=")
    params: list[str] = []
    if width:
        params.append(f"x={width}")
    if crop:
        params.append("m=1")
    if animated:
        params.append("a=1")
    qs = f"?{'&'.join(params)}" if params else ""
    return f"https://cdn.everskies.com/render/{slug}.png{qs}"


def calculate_set_price(item_count: int) -> int:
    """Fair tiered pricing for clothing sets based on piece count."""
    # Base 1,200 + 250 per item, clamped between 1,500 and 12,000 credits
    return max(1500, min(12000, 1200 + item_count * 250))


async def get_player_avatar(user_id: int) -> dict[str, Any]:
    """Retrieve player avatar state, initializing if first time."""
    async with db.read() as conn:
        cursor = await conn.execute(
            """
            SELECT user_id, skin_tone, is_plus_size, active_set_id,
                   equipped_var_ids, updated_at
            FROM player_avatar WHERE user_id = ?
            """,
            (user_id,),
        )
        row = await cursor.fetchone()
        await cursor.close()

    if row is not None:
        return {
            "user_id": row["user_id"],
            "skin_tone": row["skin_tone"],
            "is_plus_size": row["is_plus_size"],
            "active_set_id": row["active_set_id"],
            "equipped_var_ids": json.loads(row["equipped_var_ids"] or "[]"),
            "updated_at": row["updated_at"],
        }

    # Initialize avatar from players table default skin tone
    skin_tone_id = -1
    async with db.read() as conn:
        cursor = await conn.execute(
            "SELECT skin_tone, gender FROM players WHERE user_id = ?", (user_id,)
        )
        p_row = await cursor.fetchone()
        await cursor.close()

    if p_row and p_row["skin_tone"]:
        skin_tone_id = SKIN_MAP_BY_NAME.get(str(p_row["skin_tone"]).lower(), -1)

    now_ts = int(time.time())
    async with db.write() as conn:
        await conn.execute(
            """
            INSERT OR IGNORE INTO player_avatar
                (user_id, skin_tone, is_plus_size, active_set_id, equipped_var_ids, updated_at)
            VALUES (?, ?, 0, NULL, '[]', ?)
            """,
            (user_id, skin_tone_id, now_ts),
        )

    return {
        "user_id": user_id,
        "skin_tone": skin_tone_id,
        "is_plus_size": 0,
        "active_set_id": None,
        "equipped_var_ids": [],
        "updated_at": now_ts,
    }


async def set_skin_tone(user_id: int, skin_tone: int, is_plus_size: int = 0) -> None:
    """Update avatar base skin tone."""
    await get_player_avatar(user_id)  # Ensure row exists
    now_ts = int(time.time())
    async with db.write() as conn:
        await conn.execute(
            """
            UPDATE player_avatar
            SET skin_tone = ?, is_plus_size = ?, updated_at = ?
            WHERE user_id = ?
            """,
            (skin_tone, is_plus_size, now_ts, user_id),
        )


async def equip_set(user_id: int, set_id: int) -> dict[str, Any]:
    """Equip an owned set from player wardrobe."""
    async with db.read() as conn:
        cursor = await conn.execute(
            """
            SELECT set_id, set_name, var_ids, is_plus_size
            FROM player_wardrobe WHERE user_id = ? AND set_id = ?
            """,
            (user_id, set_id),
        )
        row = await cursor.fetchone()
        await cursor.close()

    if row is None:
        raise AvatarError("این ست لباس در کمد شما یافت نشد!")

    var_ids = json.loads(row["var_ids"] or "[]")
    is_plus = row["is_plus_size"]
    now_ts = int(time.time())

    async with db.write() as conn:
        await conn.execute(
            """
            UPDATE player_avatar
            SET active_set_id = ?, equipped_var_ids = ?, is_plus_size = ?, updated_at = ?
            WHERE user_id = ?
            """,
            (set_id, json.dumps(var_ids), is_plus, now_ts, user_id),
        )

    return {
        "set_id": set_id,
        "set_name": row["set_name"],
        "var_ids": var_ids,
        "is_plus_size": is_plus,
    }


async def unequip(user_id: int) -> None:
    """Take off all equipped clothing, returning to base mannequin."""
    now_ts = int(time.time())
    async with db.write() as conn:
        await conn.execute(
            """
            UPDATE player_avatar
            SET active_set_id = NULL, equipped_var_ids = '[]', updated_at = ?
            WHERE user_id = ?
            """,
            (now_ts, user_id),
        )


async def get_player_render_url(user_id: int, width: int = 400) -> str:
    """Generate the full render URL for the player's active avatar."""
    avatar = await get_player_avatar(user_id)
    skin_tone = avatar["skin_tone"]
    equipped = avatar["equipped_var_ids"]
    full_vars = [skin_tone] + equipped
    return get_render_url(full_vars, width=width)


# -----------------------------------------------------------------------------
# Boutique Stock & Rotation
# -----------------------------------------------------------------------------

def get_set_from_catalog(set_id: int) -> dict[str, Any] | None:
    """Fetch single set details from bot_catalog.db."""
    cat_path = get_catalog_db_path()
    if not cat_path.exists():
        return None
    try:
        conn = sqlite3.connect(cat_path)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute(
            "SELECT id, name, gender, item_count, var_ids, is_plus_size FROM bot_sets WHERE id = ?",
            (set_id,),
        )
        row = cur.fetchone()
        cur.close()
        conn.close()
        if not row:
            return None
        var_ids = json.loads(row["var_ids"] or "[]")
        return {
            "id": row["id"],
            "name": row["name"],
            "gender": row["gender"],
            "item_count": row["item_count"],
            "var_ids": var_ids,
            "is_plus_size": row["is_plus_size"],
            "price": calculate_set_price(row["item_count"]),
            "render_url": get_render_url(var_ids, width=400),
        }
    except Exception as exc:
        logger.warning("failed to fetch set %s from catalog: %s", set_id, exc)
        return None


def get_daily_boutique(
    gender: str = "female",
    date_key: str | None = None,
    limit: int = 8,
) -> list[dict[str, Any]]:
    """Deterministically pick daily rotation sets from bot_catalog.db.

    Keyed by calendar date YYYY-MM-DD and gender, ensuring all players
    see identical stock for the day without requiring cron jobs.
    """
    cat_path = get_catalog_db_path()
    if not cat_path.exists():
        logger.warning("bot_catalog.db not found at %s", cat_path)
        return []

    date_key = date_key or datetime.now(UTC).strftime("%Y-%m-%d")
    target_gender = gender if gender in ("female", "male", "unisex") else "female"

    try:
        conn = sqlite3.connect(cat_path)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        # Count total available sets in this category
        cur.execute(
            "SELECT COUNT(*) FROM bot_sets WHERE gender = ?",
            (target_gender,),
        )
        total = cur.fetchone()[0]
        if total == 0:
            cur.close()
            conn.close()
            return []

        # Deterministic seeded selection
        seed_bytes = hashlib.sha256(f"boutique:{date_key}:{target_gender}".encode()).digest()
        rng = random.Random(int.from_bytes(seed_bytes[:8], "big"))
        n_picks = min(limit, total)
        chosen_offsets = sorted(rng.sample(range(total), n_picks))

        picks: list[dict[str, Any]] = []
        for offset in chosen_offsets:
            cur.execute(
                """
                SELECT id, name, gender, item_count, var_ids, is_plus_size
                FROM bot_sets
                WHERE gender = ?
                LIMIT 1 OFFSET ?
                """,
                (target_gender, offset),
            )
            row = cur.fetchone()
            if row:
                var_ids = json.loads(row["var_ids"] or "[]")
                picks.append(
                    {
                        "id": row["id"],
                        "name": row["name"],
                        "gender": row["gender"],
                        "item_count": row["item_count"],
                        "var_ids": var_ids,
                        "is_plus_size": row["is_plus_size"],
                        "price": calculate_set_price(row["item_count"]),
                        "render_url": get_render_url(var_ids, width=400),
                    }
                )

        cur.close()
        conn.close()
        return picks
    except Exception as exc:
        logger.error("error reading daily boutique: %s", exc)
        return []


# -----------------------------------------------------------------------------
# Purchases & Wardrobe
# -----------------------------------------------------------------------------

async def owns_set(user_id: int, set_id: int) -> bool:
    """Check if player already owns the set in their wardrobe."""
    async with db.read() as conn:
        cursor = await conn.execute(
            "SELECT 1 FROM player_wardrobe WHERE user_id = ? AND set_id = ?",
            (user_id, set_id),
        )
        row = await cursor.fetchone()
        await cursor.close()
        return row is not None


async def buy_boutique_set(
    user_id: int,
    set_id: int,
    player_drip: int = 0,
) -> tuple[int, dict[str, Any]]:
    """Atomically purchase a set from boutique and add to player_wardrobe."""
    if await owns_set(user_id, set_id):
        raise AlreadyOwned("شما قبلاً این ست را خریداری کرده‌اید!")

    set_info = get_set_from_catalog(set_id)
    if set_info is None:
        raise AvatarError("ست مورد نظر در کاتالوگ لباس یافت نشد!")

    raw_price = set_info["price"]
    discount = economy.drip_discount(player_drip)
    final_price = max(100, int(raw_price * (1.0 - discount)))

    now_ts = int(time.time())

    # First deduct credits via economy.spend (manages its own write transaction)
    await economy.spend(user_id, credits=final_price, kind=ActivityKind.SHOP_BUY)

    # Then insert into wardrobe
    async with db.write() as conn:
        await conn.execute(
            """
            INSERT INTO player_wardrobe
                (user_id, set_id, set_name, gender, item_count,
                 var_ids, is_plus_size, price_paid, acquired_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                set_info["id"],
                set_info["name"],
                set_info["gender"],
                set_info["item_count"],
                json.dumps(set_info["var_ids"]),
                set_info["is_plus_size"],
                final_price,
                now_ts,
            ),
        )

    logger.info(
        "user %s purchased set %s (%s) for %d credits",
        user_id,
        set_id,
        set_info["name"],
        final_price,
    )
    return final_price, set_info


async def list_wardrobe(user_id: int) -> list[dict[str, Any]]:
    """List all sets owned by the player."""
    async with db.read() as conn:
        cursor = await conn.execute(
            """
            SELECT id, set_id, set_name, gender, item_count, var_ids,
                   is_plus_size, price_paid, acquired_at
            FROM player_wardrobe
            WHERE user_id = ?
            ORDER BY acquired_at DESC
            """,
            (user_id,),
        )
        rows = await cursor.fetchall()
        await cursor.close()

    result: list[dict[str, Any]] = []
    for r in rows:
        var_ids = json.loads(r["var_ids"] or "[]")
        result.append(
            {
                "id": r["id"],
                "set_id": r["set_id"],
                "name": r["set_name"],
                "gender": r["gender"],
                "item_count": r["item_count"],
                "var_ids": var_ids,
                "is_plus_size": r["is_plus_size"],
                "price_paid": r["price_paid"],
                "acquired_at": r["acquired_at"],
                "render_url": get_render_url(var_ids, width=400),
            }
        )
    return result
