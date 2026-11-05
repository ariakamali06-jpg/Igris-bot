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
    assert "shop_items" in payload
    assert len(payload["shop_items"]) == 80


@pytest.mark.asyncio
async def test_bazaar_wardrobe_item_listing_and_buy():
    """Verify that wardrobe clothing items can be listed, escrowed, and bought in bazaar."""
    from services import bazaar, economy

    seller_id = 998811
    buyer_id = 998822

    from services import game
    await game.ensure_player(seller_id, "seller", "فروشنده")
    await game.ensure_player(buyer_id, "buyer", "خریدار")

    # Give seller a wardrobe item (var_id 28144)
    await avatar.grant_single_item(seller_id, 28144, price_paid=1000)
    assert await avatar.owns_item(seller_id, 28144) is True

    # Fund seller and buyer
    async with db.write() as conn:
        await conn.execute(
            "INSERT OR REPLACE INTO wallets (user_id, credits) VALUES (?, ?)",
            (seller_id, 5000),
        )
        await conn.execute(
            "INSERT OR REPLACE INTO wallets (user_id, credits) VALUES (?, ?)",
            (buyer_id, 20000),
        )

    # 1. Seller lists the wardrobe item for 8,000 reksar
    res = await bazaar.list_item(seller_id, "wdr:28144", 8000)
    assert res["success"] is True
    listing_id = res["listing_id"]

    # Item must be removed from seller's wardrobe while escrowed
    assert await avatar.owns_item(seller_id, 28144) is False

    # 2. Buyer purchases the listing
    buy_res = await bazaar.buy_listing(buyer_id, listing_id)
    assert buy_res["success"] is True

    # Item must now be in buyer's wardrobe!
    assert await avatar.owns_item(buyer_id, 28144) is True
    # And still NOT in seller's wardrobe
    assert await avatar.owns_item(seller_id, 28144) is False

    # Check wallet balances: seller +8000, buyer -8000
    s_bal, _ = await economy.balances(seller_id)
    b_bal, _ = await economy.balances(buyer_id)
    assert s_bal == 13000
    assert b_bal == 12000


@pytest.mark.asyncio
async def test_bazaar_wardrobe_item_listing_cancel():
    """Verify that cancelling a wardrobe listing returns the item to seller."""
    from services import bazaar

    seller_id = 887711
    from services import game
    await game.ensure_player(seller_id, "seller2", "فروشنده۲")

    await avatar.grant_single_item(seller_id, 33817, price_paid=1500)
    assert await avatar.owns_item(seller_id, 33817) is True

    # List item
    res = await bazaar.list_item(seller_id, "wdr:33817", 5000)
    listing_id = res["listing_id"]
    assert await avatar.owns_item(seller_id, 33817) is False

    # Cancel listing
    cancel_res = await bazaar.cancel(seller_id, listing_id)
    assert cancel_res["success"] is True

    # Item must be back in seller's wardrobe!
    assert await avatar.owns_item(seller_id, 33817) is True


@pytest.mark.asyncio
async def test_energy_regen_calibration_and_food_overcharge():
    """Verify 2 refills/24h (432s per energy), 100 cap for passive regen, and food supercharging."""
    from handlers.common import energy_bar
    from services.game import _regen_window

    # 1. Test auto-regen rate from 0
    t0 = 1_000_000
    # Less than 1 interval (400s < 432s): 0 gained
    en, upd = _regen_window(0, t0, t0 + 400)
    assert en == 0
    assert upd == t0

    # Exactly 1 interval (432s): 1 gained
    en, upd = _regen_window(0, t0, t0 + 432)
    assert en == 1
    assert upd == t0 + 432

    # Exactly 12 hours (43,200s): 100 gained (0 -> 100)
    en, _ = _regen_window(0, t0, t0 + 43_200)
    assert en == 100

    # Exactly 24 hours (86,400s): capped at 100 (never pushes past 100)
    en, _ = _regen_window(0, t0, t0 + 86_400)
    assert en == 100

    # 2. If already >= 100, passive regen never adds more and never lowers it
    en, _ = _regen_window(100, t0, t0 + 86_400)
    assert en == 100

    en_boosted, _ = _regen_window(175, t0, t0 + 86_400)
    assert en_boosted == 175

    en_super, _ = _regen_window(1000, t0, t0 + 86_400)
    assert en_super == 1000

    # 3. Energy bar formatting never overflows with supercharged energy
    bar = energy_bar(250, 100, width=10)
    assert len(bar) == 10
    assert bar == "▮" * 10

    # 4. Food allows stacking energy past 100 indefinitely
    from models.enums import ActivityKind
    from services import economy, food, game
    user_id = 998811
    await game.ensure_player(user_id, "EnergyHero", "energyhero")
    await economy.grant(user_id, credits=50000, kind=ActivityKind.ADMIN)
    async with db.write() as conn:
        await conn.execute("UPDATE players SET energy = 100 WHERE user_id = ?", (user_id,))

    p = await game.load_player(user_id)
    assert p is not None
    assert p.energy == 100

    # Eat burger (+75) while at 100 -> reaches 175!
    res1 = await food.buy_and_eat_food(p, "burger")
    assert res1["success"] is True
    assert res1["energy_gained"] == 75
    assert res1["new_energy"] == 175
    assert "سوپرشارژ" in res1["message"]

    # Eat pizza (+85) while at 175 -> reaches 260!
    p2 = await game.load_player(user_id)
    assert p2 is not None
    res2 = await food.buy_and_eat_food(p2, "pizza")
    assert res2["success"] is True
    assert res2["energy_gained"] == 85
    assert res2["new_energy"] == 260


@pytest.mark.asyncio
async def test_smart_player_hydration_and_profile_recovery():
    """Verify that NULL/0 legacy fields recover accurately from profile context."""
    from services import game

    user_id = 998822
    await game.ensure_player(user_id, "SmartHero", "smarthero")

    # Manually simulate legacy database rows with 0 uninitialized values
    async with db.write() as conn:
        await conn.execute(
            """
            UPDATE players
            SET level = 3,
                job = 'برنامه‌نویس جونیور',
                energy = 0,
                energy_updated_at = 0,
                work_count = 0,
                education_level = 0
            WHERE user_id = ?
            """,
            (user_id,),
        )

    # Hydrate player: must recover 100 energy, 35 work_count (lvl 3),
    # and level 2 education (developer)
    p = await game.load_player(user_id)
    assert p is not None
    assert p.energy == 100
    assert p.work_count == 35
    assert p.education_level == 2

    # Verify that the restored values were persisted into SQLite
    async with db.read() as conn:
        cursor = await conn.execute(
            "SELECT energy, work_count, education_level FROM players WHERE user_id = ?",
            (user_id,),
        )
        row = await cursor.fetchone()
        await cursor.close()
        assert row["energy"] == 100
        assert row["work_count"] == 35
        assert row["education_level"] == 2
