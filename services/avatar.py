"""Everskies Avatar, Boutique & Wardrobe service.

Handles:
- Dynamic CDN rendering URLs via base64 encoded variation arrays.
- Player avatar persistence (skin tones, plus size, active clothing set).
- Deterministic daily boutique rotation from the 30,561 Everskies catalog.
- Wardrobe management, set previewing on player character, and purchases.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import logging
import random
import sqlite3
import time
import urllib.request
from collections import OrderedDict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from PIL import Image

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
    width: int | None = None,
    crop: bool = False,
    animated: bool = False,
) -> str:
    """Generate an on-demand Everskies CDN composite PNG URL.

    Zero disk overhead: Telegram downloads directly from Everskies CDN.
    Uses m=0 by default to preserve the full 221x340 canvas.
    Omits 'x' parameter because Everskies CDN interprets 'x' as an internal
    downscale factor that collapses pixel art into a 7px smear.

    CRITICAL LAYERING SPECIFICATION:
    In Everskies CDN, the variation array is composited back-to-front (in reverse):
    - Index 0 is the FOREGROUND / TOPMOST layer (accessories, hair, clothes).
    - Later indices are drawn BEHIND earlier indices.
    - The LAST index is the BACKGROUND / BOTTOM-MOST layer (body mannequin).
    Base body / skin tone variations have NEGATIVE IDs (-1 to -12).
    Therefore, all negative IDs must be placed at the END of the array
    so the body is underneath and all clothing/items sit on top of the body!
    """
    if not variation_ids:
        return ""

    pos_ids = [vid for vid in variation_ids if vid >= 0]
    neg_ids = [vid for vid in variation_ids if vid < 0]
    ordered_ids = pos_ids + neg_ids

    raw_b64 = base64.b64encode(json.dumps(ordered_ids).encode("utf-8")).decode("utf-8")
    slug = raw_b64.rstrip("=")
    params: list[str] = []
    if crop:
        params.append("m=1")
    else:
        params.append("m=0")
    if animated:
        params.append("a=1")
    qs = f"?{'&'.join(params)}" if params else ""
    return f"https://cdn.everskies.com/render/{slug}.png{qs}"


# In-memory LRU cache for 4x upscaled avatar PNG bytes (zero disk overhead)
_AVATAR_HD_CACHE: OrderedDict[str, bytes] = OrderedDict()
_MAX_HD_CACHE: int = 150


async def fetch_upscaled_avatar(render_url: str, scale: int = 4) -> bytes | None:
    """Fetch avatar PNG from Everskies CDN and upscale using Nearest Neighbor.

    Upscaling 4x (from 221x340 to 884x1360) ensures pixel art remains razor-sharp
    and crisp on modern high-DPI screens without Telegram's bilinear smoothing.
    Zero disk overhead: streams and resizes entirely in-memory using BytesIO.
    """
    if not render_url:
        return None

    cache_key = f"{render_url}_x{scale}"
    if cache_key in _AVATAR_HD_CACHE:
        _AVATAR_HD_CACHE.move_to_end(cache_key)
        return _AVATAR_HD_CACHE[cache_key]

    def _sync_fetch_and_scale() -> bytes | None:
        try:
            req = urllib.request.Request(
                render_url,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = resp.read()
            if not data:
                return None
            img = Image.open(io.BytesIO(data))
            new_w = img.size[0] * scale
            new_h = img.size[1] * scale
            hd_img = img.resize((new_w, new_h), Image.Resampling.NEAREST)
            out = io.BytesIO()
            hd_img.save(out, format="PNG")
            res = out.getvalue()
            return res
        except Exception as exc:
            logger.warning("Failed to upscale avatar (%s): %s", render_url, exc)
            return None

    res = await asyncio.to_thread(_sync_fetch_and_scale)
    if res is not None:
        _AVATAR_HD_CACHE[cache_key] = res
        if len(_AVATAR_HD_CACHE) > _MAX_HD_CACHE:
            _AVATAR_HD_CACHE.popitem(last=False)
    return res


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
                   equipped_var_ids, makeup_data, updated_at
            FROM player_avatar WHERE user_id = ?
            """,
            (user_id,),
        )
        row = await cursor.fetchone()
        await cursor.close()

    if row is not None:
        keys = row.keys() if hasattr(row, "keys") else []
        makeup_raw = row["makeup_data"] if "makeup_data" in keys else "{}"
        return {
            "user_id": row["user_id"],
            "skin_tone": row["skin_tone"],
            "is_plus_size": row["is_plus_size"],
            "active_set_id": row["active_set_id"],
            "equipped_var_ids": json.loads(row["equipped_var_ids"] or "[]"),
            "makeup_data": json.loads(makeup_raw or "{}"),
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
                (user_id, skin_tone, is_plus_size, active_set_id,
                 equipped_var_ids, makeup_data, updated_at)
            VALUES (?, ?, 0, NULL, '[]', '{}', ?)
            """,
            (user_id, skin_tone_id, now_ts),
        )

    return {
        "user_id": user_id,
        "skin_tone": skin_tone_id,
        "is_plus_size": 0,
        "active_set_id": None,
        "equipped_var_ids": [],
        "makeup_data": {},
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


async def save_custom_avatar(
    user_id: int,
    var_ids: list[int],
    skin_tone: int = -1,
    is_plus_size: int = 0,
) -> dict[str, Any]:
    """Save player custom avatar composition from customizer / Mini App."""
    await get_player_avatar(user_id)  # ensure row exists
    now_ts = int(time.time())
    async with db.write() as conn:
        await conn.execute(
            """
            UPDATE player_avatar
            SET equipped_var_ids = ?,
                skin_tone = ?,
                is_plus_size = ?,
                updated_at = ?
            WHERE user_id = ?
            """,
            (json.dumps(var_ids), skin_tone, is_plus_size, now_ts, user_id),
        )
    return {
        "user_id": user_id,
        "equipped_var_ids": var_ids,
        "skin_tone": skin_tone,
        "is_plus_size": is_plus_size,
    }


async def get_player_render_url(user_id: int, width: int | None = None) -> str:
    """Generate the full render URL for the player's active avatar."""
    avatar = await get_player_avatar(user_id)
    skin_tone = avatar["skin_tone"]
    is_plus = avatar.get("is_plus_size", 0)
    equipped = avatar["equipped_var_ids"]
    if is_plus:
        plus_head_map = {-7: -13, -8: -14, -9: -15, -10: -16, -11: -17, -6: -12}
        head_id = plus_head_map.get(skin_tone, -13)
        full_vars = equipped + [skin_tone, head_id]
    else:
        full_vars = equipped + [skin_tone]
    return get_render_url(full_vars, width=width)


# -----------------------------------------------------------------------------
# Starter Sets for Character Creation
# -----------------------------------------------------------------------------

STARTER_SETS: dict[int, dict[str, Any]] = {
    101: {
        "id": 101,
        "name": "🌸 استایل کژوال دخترانه (Casual Girl)",
        "gender": "female",
        "item_count": 8,
        "var_ids": [4024707, 4027798, 4027823, 4027826, 4027832, 4027838, 4027839, 4027840],
        "is_plus_size": 0,
        "price": 0,
    },
    102: {
        "id": 102,
        "name": "✨ یونیفرم کالج و مدرسه (School Time)",
        "gender": "female",
        "item_count": 8,
        "var_ids": [3623948, 3623958, 3623964, 3623978, 3623984, 3623991, 3624003, 3624005],
        "is_plus_size": 0,
        "price": 0,
    },
    103: {
        "id": 103,
        "name": "🎀 استایل شیک آکادمی (Academy Chic)",
        "gender": "female",
        "item_count": 12,
        "var_ids": [
            297694, 297700, 297703, 297709, 297713, 297719,
            297725, 298928, 297737, 297743, 298590, 298591,
        ],
        "is_plus_size": 0,
        "price": 0,
    },
    104: {
        "id": 104,
        "name": "🏙 استایل خیابانی شیک (Urban Chic)",
        "gender": "female",
        "item_count": 11,
        "var_ids": [
            4582094, 4582095, 4582116, 4584726, 4584745, 4584760,
            4584761, 4584787, 4584788, 4586437, 4586447,
        ],
        "is_plus_size": 0,
        "price": 0,
    },
    201: {
        "id": 201,
        "name": "🕶 استریت‌ویر مدرن (Modern Streetwear)",
        "gender": "male",
        "item_count": 13,
        "var_ids": [
            272737, 272738, 272740, 272744, 272746, 272749,
            272751, 272753, 272755, 272757, 272758, 272759, 274743,
        ],
        "is_plus_size": 0,
        "price": 0,
    },
    202: {
        "id": 202,
        "name": "⚡️ استایل فشن شهری (Urban Fashion)",
        "gender": "male",
        "item_count": 11,
        "var_ids": [
            499821, 499824, 499827, 499833, 499834, 499835,
            499836, 499838, 499843, 499846, 499847,
        ],
        "is_plus_size": 0,
        "price": 0,
    },
    203: {
        "id": 203,
        "name": "🎩 پالتو و کت شیک زمستانی (Winter Coat)",
        "gender": "male",
        "item_count": 12,
        "var_ids": [
            321579, 321585, 321592, 321598, 321599, 321604,
            321610, 321613, 321614, 321620, 321626, 321632,
        ],
        "is_plus_size": 0,
        "price": 0,
    },
    204: {
        "id": 204,
        "name": "✨ استایل ترندی کره‌ای (Trendy K-Style)",
        "gender": "male",
        "item_count": 9,
        "var_ids": [
            502323, 502324, 502329, 502333, 502335,
            502339, 502342, 502343, 502353,
        ],
        "is_plus_size": 0,
        "price": 0,
    },
}


# -----------------------------------------------------------------------------
# Boutique Stock & Rotation
# -----------------------------------------------------------------------------

def get_set_from_catalog(set_id: int) -> dict[str, Any] | None:
    """Fetch single set details from bot_catalog.db or starter sets."""
    if set_id in STARTER_SETS:
        s = dict(STARTER_SETS[set_id])
        s["render_url"] = get_render_url(s["var_ids"])
        return s

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
            "render_url": get_render_url(var_ids),
        }
    except Exception as exc:
        logger.warning("failed to fetch set %s from catalog: %s", set_id, exc)
        return None


def get_starter_sets(gender: str = "male") -> list[dict[str, Any]]:
    """Return curated, high-detail starter sets for onboarding."""
    target_gender = "female" if gender in ("female", "دختر") else "male"
    res = []
    for s in STARTER_SETS.values():
        if s["gender"] == target_gender:
            item = dict(s)
            item["render_url"] = get_render_url(item["var_ids"])
            res.append(item)
    return res


async def assign_starter_set(
    user_id: int,
    set_id: int,
    gender: str = "male",
    skin_tone: int = -1,
    makeup_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Wipe legacy items and assign new starter set to wardrobe and active avatar."""
    if makeup_data is None:
        makeup_data = {}

    set_info = get_set_from_catalog(set_id)
    if not set_info:
        starters = get_starter_sets(gender)
        set_info = next(
            (s for s in starters if s["id"] == set_id),
            starters[0] if starters else None,
        )

    if not set_info:
        raise ValueError(f"Unknown starter set id: {set_id}")

    now_ts = int(time.time())
    var_ids = set_info["var_ids"]
    var_ids_json = json.dumps(var_ids)
    makeup_json = json.dumps(makeup_data)

    async with db.write() as conn:
        # Wipe old legacy clothes completely
        await conn.execute("DELETE FROM loadout WHERE user_id = ?", (user_id,))
        await conn.execute(
            "DELETE FROM inventory WHERE user_id = ? AND "
            "item_id IN ('fitted_tee', 'street_slacks')",
            (user_id,),
        )
        # Clear any prior wardrobe entries for this player
        await conn.execute("DELETE FROM player_wardrobe WHERE user_id = ?", (user_id,))

        # Add starter set to player_wardrobe (free)
        await conn.execute(
            """
            INSERT OR REPLACE INTO player_wardrobe
                (user_id, set_id, set_name, gender, item_count,
                 var_ids, is_plus_size, price_paid, acquired_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?)
            """,
            (
                user_id,
                set_info["id"],
                set_info["name"],
                set_info["gender"],
                set_info["item_count"],
                var_ids_json,
                set_info.get("is_plus_size", 0),
                now_ts,
            ),
        )

        # Equip starter set and save makeup / skin tone in player_avatar
        await conn.execute(
            """
            INSERT INTO player_avatar
                (user_id, skin_tone, is_plus_size, active_set_id,
                 equipped_var_ids, makeup_data, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                skin_tone = excluded.skin_tone,
                is_plus_size = excluded.is_plus_size,
                active_set_id = excluded.active_set_id,
                equipped_var_ids = excluded.equipped_var_ids,
                makeup_data = excluded.makeup_data,
                updated_at = excluded.updated_at
            """,
            (
                user_id,
                skin_tone,
                set_info.get("is_plus_size", 0),
                set_info["id"],
                var_ids_json,
                makeup_json,
                now_ts,
            ),
        )

    return {
        "user_id": user_id,
        "set_id": set_info["id"],
        "name": set_info["name"],
        "var_ids": var_ids,
        "skin_tone": skin_tone,
        "makeup_data": makeup_data,
        "render_url": get_render_url(var_ids + [skin_tone]),
    }


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
                        "render_url": get_render_url(var_ids),
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
                "render_url": get_render_url(var_ids),
            }
        )
    return result


async def get_customizer_url(
    user_id: int,
    display_name: str | None = None,
    level: int | None = None,
    credits: int | None = None,
    gender: str | None = None,
    skin_tone: int | None = None,
    is_plus_size: int | None = None,
    var_ids: list[int] | None = None,
) -> str:
    """Generate personalized Telegram Mini App URL carrying user loadout."""
    base_url = settings.webapp_url.rstrip("/")

    # If parameters not provided, load from database
    if var_ids is None or skin_tone is None or is_plus_size is None:
        try:
            player_av = await get_player_avatar(user_id)
            if var_ids is None:
                var_ids = list(player_av.get("equipped_var_ids", []))
            if skin_tone is None:
                skin_tone = int(player_av.get("skin_tone", -1))
            if is_plus_size is None:
                is_plus_size = int(player_av.get("is_plus_size", 0))
        except Exception:
            var_ids = var_ids or []
            skin_tone = skin_tone or -1
            is_plus_size = is_plus_size or 0

    if display_name is None or level is None or gender is None:
        try:
            from services import game

            player = await game.load_player(user_id)
            if player is not None:
                display_name = display_name or player.display_name
                level = level or player.level
                gender = gender or player.gender
        except Exception:
            pass

    if credits is None:
        try:
            credits_val, _ = await economy.balances(user_id)
            credits = credits_val
        except Exception:
            credits = 0

    is_plus = bool(is_plus_size)
    if is_plus:
        plus_skin_map = {-7: 1, -8: 2, -9: 3, -10: 4, -11: 5, -6: 6}
        skin_idx = plus_skin_map.get(skin_tone, 1)
    else:
        slim_skin_map = {-1: 1, -2: 2, -3: 3, -4: 4, -5: 5}
        skin_idx = slim_skin_map.get(skin_tone, 1)

    gen_str = "female" if (gender and ("دختر" in gender or gender == "female")) else "male"

    payload = {
        "u": user_id,
        "name": display_name or "مسافر",
        "lvl": level or 1,
        "cr": credits or 0,
        "gen": gen_str,
        "skin": skin_idx,
        "body": "plus" if is_plus else "slim",
        "items": var_ids or [],
        "bot": settings.bot_username or "hermesforrexbot",
    }

    raw_json = json.dumps(payload, separators=(",", ":"))
    b64_data = base64.urlsafe_b64encode(raw_json.encode("utf-8")).decode("utf-8").rstrip("=")

    separator = "&" if "?" in base_url else "?"
    return f"{base_url}{separator}d={b64_data}"
