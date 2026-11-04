"""Catalog service for Igris Bot.

Manages granular asset classifications:
- Hair (base, back, front, extensions)
- Eyes & Brows
- Nose & Piercings
- Lips & Mouth
- Makeup (face art, blush, eyeshadow/lashes, freckles/moles, contour)
- Clothing (tops, bottoms, dresses/suits, shoes)
- Accessories (glasses, necklaces, earrings, bags, belts, gloves, etc.)
- Body & Fantasy (wings, tattoos, scars, weapons)
"""

from __future__ import annotations

import hashlib
import json
import logging
import random
import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from config import settings

logger = logging.getLogger(__name__)

DB_PATH = settings.db_path
BOT_CATALOG_PATH = settings.catalog_db_path
EXPLORER_HTML_PATH = Path(__file__).resolve().parent.parent / "tools" / "catalog_explorer.html"

BOUTIQUE_CATEGORY_MAP: dict[str, str] = {
    "top": "clothing_top",
    "bottom": "clothing_bottom",
    "shoes": "shoes",
    "acc": "accessories",
}

BOUTIQUE_CATEGORY_TITLES: dict[str, str] = {
    "top": "👕 بالاتنه (کت، کاپشن، هودی و تیشرت)",
    "bottom": "👖 پایین‌تنه (شلوار، دامن و شلوارک)",
    "shoes": "👟 کفش‌ها (کتونی، چکمه و صندل)",
    "acc": "🎒 اکسسوری (عینک، کیف، شال و کلاه)",
}


def init_catalog_schema() -> None:
    """Ensure catalog_items table exists with appropriate indices."""
    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        c.execute("""
        CREATE TABLE IF NOT EXISTS catalog_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id INTEGER NOT NULL,
            var_id INTEGER NOT NULL UNIQUE,
            name TEXT NOT NULL,
            category TEXT NOT NULL,
            subcategory TEXT NOT NULL,
            gender TEXT NOT NULL DEFAULT 'unisex',
            set_name TEXT NOT NULL DEFAULT '',
            image_id TEXT NOT NULL DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_cat_sub ON catalog_items(category, subcategory);")
        c.execute("CREATE INDEX IF NOT EXISTS idx_cat_var ON catalog_items(var_id);")
        conn.commit()


def ensure_catalog_items_seeded() -> int:
    """Idempotently seed 9,000+ items into catalog_items table from catalog_explorer if empty."""
    init_catalog_schema()
    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM catalog_items")
        cnt = c.fetchone()[0]
        if cnt >= 1000:
            return cnt

    if not EXPLORER_HTML_PATH.exists():
        logger.warning("Explorer HTML not found at %s for catalog seeding", EXPLORER_HTML_PATH)
        return 0

    try:
        with open(EXPLORER_HTML_PATH, encoding="utf-8") as f:
            html = f.read()
        m = re.search(r"const RAW_ITEMS = (\[.*?\]);", html)
        if not m:
            return 0
        raw = json.loads(m.group(1))

        set_genders: dict[str, str] = {}
        if BOT_CATALOG_PATH.exists():
            with sqlite3.connect(BOT_CATALOG_PATH) as conn_sets:
                c_sets = conn_sets.cursor()
                c_sets.execute("SELECT name, gender FROM bot_sets")
                for r in c_sets.fetchall():
                    if r[0]:
                        set_genders[r[0].strip().lower()] = r[1]

        to_insert = []
        for it in raw:
            st = it.get("st", "").strip()
            g = set_genders.get(st.lower(), it.get("g", "unisex"))
            to_insert.append((
                int(it.get("id", 0)),
                int(it.get("v", 0)),
                str(it.get("n", "Item")),
                str(it.get("c", "general")),
                str(it.get("s", "misc")),
                str(g),
                st,
                str(it.get("img", "")),
            ))

        with sqlite3.connect(DB_PATH) as conn:
            conn.executemany("""
            INSERT OR REPLACE INTO catalog_items
                (item_id, var_id, name, category, subcategory, gender, set_name, image_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, to_insert)
            conn.commit()

        logger.info("Successfully seeded %d catalog items into game.db", len(to_insert))
        return len(to_insert)
    except Exception as exc:
        logger.error("Failed to seed catalog items: %s", exc)
        return 0


def get_daily_boutique_items(
    category_key: str = "top",
    date_key: str | None = None,
    page: int = 0,
    page_size: int = 5,
) -> dict[str, Any]:
    """Deterministically select 20 daily items per clothing category for zero-data shopping."""
    ensure_catalog_items_seeded()
    db_cat = BOUTIQUE_CATEGORY_MAP.get(category_key, "clothing_top")
    date_key = date_key or datetime.now(UTC).strftime("%Y-%m-%d")

    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM catalog_items WHERE category = ?", (db_cat,))
        total_in_cat = c.fetchone()[0]

        if total_in_cat == 0:
            return {
                "items": [],
                "page": 0,
                "total_pages": 1,
                "total_items": 0,
                "category_key": category_key,
                "title": BOUTIQUE_CATEGORY_TITLES.get(category_key, "پوشاک"),
            }

        # Seeded daily pool of 20 items
        daily_limit = 20
        n_picks = min(daily_limit, total_in_cat)
        seed_bytes = hashlib.sha256(f"boutique_items:{date_key}:{category_key}".encode()).digest()
        rng = random.Random(int.from_bytes(seed_bytes[:8], "big"))
        chosen_offsets = sorted(rng.sample(range(total_in_cat), n_picks))

        # Paginate chosen offsets
        total_pages = max(1, (len(chosen_offsets) + page_size - 1) // page_size)
        cur_page = max(0, min(page, total_pages - 1))
        page_offsets = chosen_offsets[cur_page * page_size : (cur_page + 1) * page_size]

        items = []
        for offset in page_offsets:
            c.execute(
                """
                SELECT id, item_id, var_id, name, category, subcategory, gender, set_name
                FROM catalog_items
                WHERE category = ?
                ORDER BY id ASC
                LIMIT 1 OFFSET ?
                """,
                (db_cat, offset),
            )
            row = c.fetchone()
            if row:
                items.append(dict(row))

        return {
            "items": items,
            "page": cur_page,
            "total_pages": total_pages,
            "total_items": len(chosen_offsets),
            "category_key": category_key,
            "title": BOUTIQUE_CATEGORY_TITLES.get(category_key, "پوشاک"),
        }


def get_catalog_item_by_var_id(var_id: int) -> dict[str, Any] | None:
    """Fetch single catalog item by its variation ID."""
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute(
            """
            SELECT id, item_id, var_id, name, category, subcategory, gender, set_name
            FROM catalog_items
            WHERE var_id = ?
            LIMIT 1
            """,
            (var_id,),
        )
        row = c.fetchone()
        return dict(row) if row else None


def get_items_by_category(
    category: str,
    subcategory: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Retrieve catalog items filtered by category and optional subcategory."""
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        if subcategory and subcategory != "all":
            query = (
                "SELECT * FROM catalog_items WHERE category = ? AND subcategory = ? "
                "ORDER BY id ASC LIMIT ?"
            )
            c.execute(query, (category, subcategory, limit))
        else:
            c.execute(
                "SELECT * FROM catalog_items WHERE category = ? ORDER BY id ASC LIMIT ?",
                (category, limit),
            )
        rows = c.fetchall()
        return [dict(r) for r in rows]


def search_items(
    query: str,
    category: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Search items by name, set_name or var_id."""
    like_q = f"%{query}%"
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        if category and category != "all":
            sql = (
                "SELECT * FROM catalog_items "
                "WHERE category = ? AND "
                "(name LIKE ? OR set_name LIKE ? OR CAST(var_id AS TEXT) LIKE ?) "
                "ORDER BY id ASC LIMIT ?"
            )
            c.execute(sql, (category, like_q, like_q, like_q, limit))
        else:
            sql = (
                "SELECT * FROM catalog_items "
                "WHERE name LIKE ? OR set_name LIKE ? OR CAST(var_id AS TEXT) LIKE ? "
                "ORDER BY id ASC LIMIT ?"
            )
            c.execute(sql, (like_q, like_q, like_q, limit))
        rows = c.fetchall()
        return [dict(r) for r in rows]


def import_custom_json(json_path: str | Path) -> int:
    """Import categorized items from JSON file exported by catalog_explorer.html."""
    p = Path(json_path)
    if not p.exists():
        logger.error("File %s does not exist", p)
        return 0

    with open(p, encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, list):
        return 0

    updated = 0
    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        for it in data:
            v_id = it.get("var_id")
            cat = it.get("category")
            sub = it.get("subcategory")
            if v_id and cat and sub:
                c.execute(
                    """
                    UPDATE catalog_items
                    SET category = ?, subcategory = ?
                    WHERE var_id = ?
                    """,
                    (cat, sub, v_id),
                )
                if c.rowcount > 0:
                    updated += 1
        conn.commit()

    logger.info("Imported and updated %d items from %s", updated, p)
    return updated
