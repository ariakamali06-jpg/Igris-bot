"""Tests for catalog service and granular classification."""

from __future__ import annotations

import json
from pathlib import Path

from services import catalog


def test_init_catalog_schema():
    catalog.init_catalog_schema()
    items = catalog.get_items_by_category("hair", limit=5)
    assert isinstance(items, list)


def test_get_items_by_category():
    items = catalog.get_items_by_category("clothing_top", limit=10)
    assert len(items) > 0
    for it in items:
        assert it["category"] == "clothing_top"


def test_get_items_by_subcategory():
    items = catalog.get_items_by_category("shoes", subcategory="boots", limit=10)
    assert len(items) > 0
    for it in items:
        assert it["category"] == "shoes"
        assert it["subcategory"] == "boots"


def test_search_items():
    res = catalog.search_items("hair", limit=5)
    assert len(res) > 0


def test_import_custom_json(tmp_path: Path):
    sample = [
        {
            "id": 999999,
            "var_id": 9999991,
            "category": "hair",
            "subcategory": "hair_front",
        }
    ]
    json_file = tmp_path / "test_import.json"
    json_file.write_text(json.dumps(sample), encoding="utf-8")

    # Should not crash even if var_id not in DB
    updated = catalog.import_custom_json(json_file)
    assert isinstance(updated, int)
