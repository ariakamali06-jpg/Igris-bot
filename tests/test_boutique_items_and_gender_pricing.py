"""Tests for Granular Single-Item Boutique, Gender-Based Pricing & Bandwidth-Saving."""

from __future__ import annotations

import pytest

from database.connection import db
from database.seed import seed_catalog
from models.enums import ActivityKind
from services import avatar, catalog, economy, game

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
async def _setup_db():
    from handlers import boutique, salon
    boutique.router._parent_router = None
    salon.router._parent_router = None
    await db.connect()
    await seed_catalog()
    catalog.ensure_catalog_items_seeded()
    yield
    boutique.router._parent_router = None
    salon.router._parent_router = None
    await db.close()


async def test_gender_based_dynamic_pricing():
    """Verify same-gender items are 300 coins, opposite-gender 1200 coins, unisex 500 coins."""
    # 1. Female player perspective
    p_girl = "دختر"
    price_f_on_f, lbl_f_on_f = avatar.calculate_item_price("female", p_girl)
    assert price_f_on_f == 300
    assert "تخفیف دخترانه" in lbl_f_on_f

    price_m_on_f, lbl_m_on_f = avatar.calculate_item_price("male", p_girl)
    assert price_m_on_f == 1200
    assert "لوکس پسرانه" in lbl_m_on_f

    price_u_on_f, lbl_u_on_f = avatar.calculate_item_price("unisex", p_girl)
    assert price_u_on_f == 500
    assert "استاندارد" in lbl_u_on_f

    # 2. Male player perspective
    p_boy = "پسر"
    price_m_on_m, lbl_m_on_m = avatar.calculate_item_price("male", p_boy)
    assert price_m_on_m == 300
    assert "تخفیف پسرانه" in lbl_m_on_m

    price_f_on_m, lbl_f_on_m = avatar.calculate_item_price("female", p_boy)
    assert price_f_on_m == 1200
    assert "لوکس دخترانه" in lbl_f_on_m

    price_u_on_m, lbl_u_on_m = avatar.calculate_item_price("unisex", p_boy)
    assert price_u_on_m == 500


async def test_single_item_purchase_and_equip_lifecycle():
    """Verify buying and equipping an individual clothing item (not sets!)."""
    user_id = 999888771
    await game.ensure_player(
        user_id=user_id, username="item_buyer", display_name="Item Buyer"
    )
    # Set player as girl
    async with db.write() as conn:
        await conn.execute("UPDATE players SET gender = 'دختر' WHERE user_id = ?", (user_id,))
    await economy.grant(user_id, credits=10000, kind=ActivityKind.ADMIN)

    # Pick an item from catalog (e.g. Moon crop 1326 or Tulle skirt 1322)
    item = catalog.get_catalog_item_by_var_id(1326)
    assert item is not None
    assert item["var_id"] == 1326

    # 1. Buy single item
    price, it_info = await avatar.buy_boutique_item(
        user_id=user_id,
        var_id=1326,
        player_gender="دختر",
    )
    assert price > 0
    assert it_info["var_id"] == 1326

    # 2. Verify ownership in wardrobe
    assert await avatar.owns_item(user_id, 1326) is True
    wdr = await avatar.list_wardrobe(user_id)
    assert any(1326 in s["var_ids"] for s in wdr)

    # 3. Duplicate purchase must fail
    with pytest.raises(avatar.AlreadyOwned):
        await avatar.buy_boutique_item(user_id, 1326, player_gender="دختر")

    # 4. Equip single item onto avatar
    eq_res = await avatar.equip_single_item(user_id, 1326)
    assert eq_res["equipped"] is True
    av = await avatar.get_player_avatar(user_id)
    assert 1326 in av["equipped_var_ids"]

    # 5. Equipping again indicates already worn
    eq_again = await avatar.equip_single_item(user_id, 1326)
    assert eq_again["equipped"] is False
    assert "هم‌اکنون بر تن" in eq_again["message"]

    # Cleanup
    async with db.write() as conn:
        await conn.execute("DELETE FROM player_wardrobe WHERE user_id = ?", (user_id,))
        await conn.execute("DELETE FROM player_avatar WHERE user_id = ?", (user_id,))
        await conn.execute("DELETE FROM wallets WHERE user_id = ?", (user_id,))
        await conn.execute("DELETE FROM players WHERE user_id = ?", (user_id,))


async def test_cmd_start_delegation_without_payload():
    """Verify /start without payload properly triggers player onboarding wizard or profile."""
    from aiogram.fsm.context import FSMContext
    from aiogram.fsm.storage.base import StorageKey
    from aiogram.fsm.storage.memory import MemoryStorage

    from handlers.onboarding import cmd_start_wear

    user_id = 999888773
    storage = MemoryStorage()
    key = StorageKey(bot_id=1, chat_id=user_id, user_id=user_id)
    state = FSMContext(storage=storage, key=key)

    # 1. Message with plain '/start'
    class DummyMsg:
        text = "/start"
        from_user = type("U", (), {"id": user_id, "full_name": "Newbie", "username": "newbie"})()
        replies = []
        async def reply(self, text, **kwargs):
            self.replies.append((text, kwargs))
            return self

    msg = DummyMsg()
    # Should delegate to cmd_start without raising exception or returning early
    await cmd_start_wear(msg, state)
    # New player gets channel join check or name wizard
    data = await state.get_data()
    # Either tracked_msg_ids (channel check) or wizard state was initiated
    assert "tracked_msg_ids" in data or len(msg.replies) > 0


async def test_zero_bandwidth_boutique_text_catalog():
    """Verify boutique generates lightweight text catalog with no premature images."""
    user_id = 999888772
    await game.ensure_player(
        user_id=user_id, username="catalog_user", display_name="Catalog User"
    )
    from handlers.boutique import _build_boutique_catalog, _build_boutique_item_detail

    # 1. Build catalog page
    render_url, caption, markup = await _build_boutique_catalog(
        user_id=user_id, category="top", page=0
    )
    assert "بوتیک خیابانی مد و لباس" in caption
    assert "بالاتنه" in caption
    assert "امتیاز جنسیت" in caption
    assert len(markup.inline_keyboard) >= 3

    # 2. Build detail view without preview (is_preview=False)
    # Renders default avatar URL only, text is detailed
    r_url, detail_caption, detail_markup = await _build_boutique_item_detail(
        user_id=user_id, var_id=1326, category="top", page=0, is_preview=False
    )
    assert "مشخصات و خرید آیتم" in detail_caption
    assert "#1326" in detail_caption
    assert any("خرید این لباس" in btn.text for row in detail_markup.inline_keyboard for btn in row)
    assert any("عکس استودیویی" in btn.text for row in detail_markup.inline_keyboard for btn in row)

    # 3. Build detail view with preview (is_preview=True)
    r_url_prev, prev_caption, prev_markup = await _build_boutique_item_detail(
        user_id=user_id, var_id=1326, category="top", page=0, is_preview=True
    )
    import base64

    slug = r_url_prev.split("/render/")[1].split(".png")[0]
    decoded = base64.b64decode(slug + "==").decode()
    assert "1326" in decoded
    assert "تصویر استودیویی" in prev_caption
    assert any("بستن عکس محصول" in btn.text for row in prev_markup.inline_keyboard for btn in row)

    # Cleanup
    async with db.write() as conn:
        await conn.execute("DELETE FROM player_wardrobe WHERE user_id = ?", (user_id,))
        await conn.execute("DELETE FROM player_avatar WHERE user_id = ?", (user_id,))
        await conn.execute("DELETE FROM wallets WHERE user_id = ?", (user_id,))
        await conn.execute("DELETE FROM players WHERE user_id = ?", (user_id,))
