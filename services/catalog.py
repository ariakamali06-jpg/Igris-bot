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

import json
import logging
import sqlite3
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "game.db"


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
