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
    # Set player as girl and onboarded
    async with db.write() as conn:
        await conn.execute(
            "UPDATE players SET gender = 'دختر', onboarding_completed = 1 WHERE user_id = ?",
            (user_id,),
        )
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

    # 6. Verify item is in list_owned_items, but NOT in list_wardrobe_sets
    owned_items = await avatar.list_owned_items(user_id)
    assert any(it["var_id"] == 1326 for it in owned_items)

    wardrobe_sets = await avatar.list_wardrobe_sets(user_id)
    assert not any(1326 in s["var_ids"] for s in wardrobe_sets)

    # 7. Verify miniapp payload has item in `owned` but NOT in `owned_sets`
    import base64
    import json
    import re
    app_url = await avatar.get_customizer_url(user_id)
    b64_str = app_url.split("?d=")[1]
    payload = json.loads(base64.urlsafe_b64decode(b64_str + "==").decode())
    assert payload["mode"] == "wardrobe"
    assert 1326 in payload["owned"]
    assert not any(1326 in s.get("vars", []) for s in payload["owned_sets"])

    # Verify index.html contains item 1326 mapped to top zone
    import asyncio
    from pathlib import Path
    html = await asyncio.to_thread(Path("index.html").read_text, encoding="utf-8")
    m_raw = re.search(r"const RAW_ITEMS = (\[.*?\]);\n    const STARTER_SETS_INFO", html)
    assert m_raw is not None
    raw_list = json.loads(m_raw.group(1))
    item_entry = next((it for it in raw_list if it["v"] == 1326), None)
    assert item_entry is not None
    assert item_entry["z"] == "top"
    assert item_entry["n"] == "Moon crop"

    # 8. Test unequip_single_item
    uneq_res = await avatar.unequip_single_item(user_id, 1326)
    assert uneq_res["unequipped"] is True
    av = await avatar.get_player_avatar(user_id)
    assert 1326 not in av["equipped_var_ids"]

    # 9. Verify _build_wardrobe_items_menu displays item
    from handlers.boutique import _build_wardrobe_items_menu
    _, items_caption, items_markup = await _build_wardrobe_items_menu(user_id, page=0)
    assert "کمد تک‌آیتم‌های شخصی" in items_caption
    assert any("تن زدن" in btn.text for row in items_markup.inline_keyboard for btn in row)

    # Cleanup
    async with db.write() as conn:
        await conn.execute("DELETE FROM player_wardrobe WHERE user_id = ?", (user_id,))
        await conn.execute("DELETE FROM player_avatar WHERE user_id = ?", (user_id,))
        await conn.execute("DELETE FROM wallets WHERE user_id = ?", (user_id,))
        await conn.execute("DELETE FROM players WHERE user_id = ?", (user_id,))


async def test_starter_sets_exact_counts_and_no_manual_option():
    """Verify girls get 4 sets, boys get 2 sets, and no manual option in Telegram."""
    from aiogram.fsm.context import FSMContext
    from aiogram.fsm.storage.base import StorageKey
    from aiogram.fsm.storage.memory import MemoryStorage

    from handlers import onboarding
    from services import avatar

    # 1. Verify exact counts and no duplicate names
    female_starters = avatar.get_starter_sets("female")
    male_starters = avatar.get_starter_sets("male")

    assert len(female_starters) == 4, f"Expected 4 female starter sets, got {len(female_starters)}"
    assert len(male_starters) == 2, f"Expected 2 male starter sets, got {len(male_starters)}"

    f_names = [s["name"] for s in female_starters]
    assert len(f_names) == len(set(f_names)), f"Duplicate female set names found: {f_names}"

    m_names = [s["name"] for s in male_starters]
    assert len(m_names) == len(set(m_names)), f"Duplicate male set names found: {m_names}"

    # 2. Verify selecting gender goes directly to ready-made starter sets without manual option
    user_id = 999888775
    storage = MemoryStorage()
    key = StorageKey(bot_id=1, chat_id=user_id, user_id=user_id)
    state = FSMContext(storage=storage, key=key)
    await state.set_state(onboarding.OnboardingState.waiting_for_gender)
    await state.update_data(name="SetTester", age=21)

    from aiogram.types import User

    from tests.test_onboarding import FakeCall, MutableMessage

    test_user = User(id=user_id, is_bot=False, first_name="Tester")
    msg = MutableMessage(text="/start", user=test_user)
    call = FakeCall(data="ob:gen:female", message=msg, user=test_user)
    await onboarding.cb_gender(call, state)

    # State must be waiting_for_starter_set directly
    cur_state = await state.get_state()
    assert cur_state == onboarding.OnboardingState.waiting_for_starter_set.state

    # Buttons must contain starter sets and MiniApp, but NO manual option
    last_reply = msg.replies[-1]
    last_text = last_reply.get("text", "")
    last_markup = last_reply.get("reply_markup")
    btn_texts = [btn.text for row in last_markup.inline_keyboard for btn in row]
    assert not any("تنظیم دستی رنگ پوست" in t for t in btn_texts)
    assert not any("انتخاب سنتی" in last_text for _ in [1])
    assert any("مینی‌اپ" in t for t in btn_texts)
    assert sum("Starter Pack" in t for t in btn_texts) == 4


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
