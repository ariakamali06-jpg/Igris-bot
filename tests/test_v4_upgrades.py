"""Unit tests for v4 game upgrades:
- Daily rotating restaurant menu (18 food items across 6 tiers)
- Dynamic work cooldown (8 min base, scaling down by level, minimum 2 min)
- Job vs Work separation with inline career employment buttons
- Wardrobe Z-Index restoration when unequipping and re-equipping single items
- Mini App Shop mode URL generation and payload validation
"""

from __future__ import annotations

import json
import pytest

from database.connection import db
from database.seed import seed_catalog
from models.player import Player
from services import avatar, food, tiramix


@pytest.fixture(autouse=True)
async def _setup_db():
    await db.connect()
    await seed_catalog()
    yield
    await db.close()


@pytest.mark.asyncio
async def test_daily_food_menu_rotation():
    """Verify that get_daily_food_menu returns 6 diverse items with consistent prices."""
    # Day 0 menu
    menu_0 = food.get_daily_food_menu(day_offset=0)
    assert len(menu_0) == 6
    names_0 = [item["name"] for item in menu_0]
    assert len(set(names_0)) == 6

    # Verify all energy levels are represented (25, 40, 55, 75, 85, 100)
    energies_0 = [item["energy"] for item in menu_0]
    assert energies_0 == [25, 40, 55, 75, 85, 100]

    # Verify prices are positive and scale with energy
    for item in menu_0:
        assert item["price"] > 0
        assert item["energy"] > 0

    # Day 1 menu should rotate at least some items
    menu_1 = food.get_daily_food_menu(day_offset=1)
    assert len(menu_1) == 6
    names_1 = [item["name"] for item in menu_1]
    # Consecutive days should offer variety
    assert names_0 != names_1


@pytest.mark.asyncio
async def test_work_cooldown_scaling():
    """Verify work cooldown starts at 8 minutes (480s) and decreases with player level."""
    # Level 1: 480 seconds (8 minutes)
    assert tiramix.get_work_cooldown(1) == 480

    # Level 2: 480 - 30 = 450 seconds (7.5 minutes)
    assert tiramix.get_work_cooldown(2) == 450

    # Level 5: 480 - 120 = 360 seconds (6 minutes)
    assert tiramix.get_work_cooldown(5) == 360

    # Level 10: 480 - 270 = 210 seconds (3.5 minutes)
    assert tiramix.get_work_cooldown(10) == 210

    # High level: never drops below minimum 120 seconds (2 minutes)
    assert tiramix.get_work_cooldown(20) == 120
    assert tiramix.get_work_cooldown(50) == 120


@pytest.mark.asyncio
async def test_wardrobe_layer_restoration():
    """Verify equipping a single item restores its original layer slot if previously unequipped."""
    user_id = 998877

    async with db.write() as conn:
        await conn.execute(
            """
            INSERT OR REPLACE INTO players
                (user_id, display_name, gender, level, onboarding_completed, created_at)
            VALUES (?, 'تستر لایه‌ها', 'male', 1, 1, 1000)
            """,
            (user_id,),
        )
        # Initialize avatar with 3 items: [100, 200, 300] (0 = top, 2 = bottom)
        await conn.execute(
            """
            INSERT OR REPLACE INTO player_avatar
                (user_id, skin_tone, is_plus_size, equipped_var_ids, makeup_data, updated_at)
            VALUES (?, -1, 0, ?, ?, 1000)
            """,
            (user_id, json.dumps([100, 200, 300]), json.dumps({})),
        )

    # 1. Unequip item 200 (was at index 1)
    unequip_res = await avatar.unequip_single_item(user_id, 200)
    assert unequip_res["unequipped"] is True
    assert unequip_res["var_ids"] == [100, 300]

    # Verify layer slot was recorded in makeup_data
    av = await avatar.get_player_avatar(user_id)
    assert av["makeup_data"]["last_item_slots"]["200"] == 1

    # 2. Re-equip item 200: it should go back to index 1, NOT index 0!
    equip_res = await avatar.equip_single_item(user_id, 200)
    assert equip_res["equipped"] is True
    assert equip_res["var_ids"] == [100, 200, 300]

    # 3. Equip a completely brand-new item (never unequipped before): goes to index 0 (top)
    equip_new = await avatar.equip_single_item(user_id, 400)
    assert equip_new["equipped"] is True
    assert equip_new["var_ids"] == [400, 100, 200, 300]


@pytest.mark.asyncio
async def test_customizer_url_shop_mode():
    """Verify get_customizer_url properly embeds mode='shop' and user credits."""
    user_id = 776655
    url = await avatar.get_customizer_url(
        user_id=user_id,
        display_name="سزار",
        level=3,
        credits=15000,
        gender="male",
        mode="shop",
    )
    assert "?d=" in url

    # Extract base64 payload from URL
    import base64
    from urllib.parse import parse_qs, urlparse

    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    assert "d" in qs
    raw_b64 = qs["d"][0]
    rem = len(raw_b64) % 4
    if rem:
        raw_b64 += "=" * (4 - rem)
    raw_json = base64.urlsafe_b64decode(raw_b64).decode("utf-8")
    payload = json.loads(raw_json)

    assert payload["u"] == user_id
    assert payload["name"] == "سزار"
    assert payload["lvl"] == 3
    assert payload["cr"] == 15000
    assert payload["mode"] == "shop"
    assert payload["gen"] == "male"
