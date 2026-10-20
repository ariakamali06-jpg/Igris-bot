"""Tests for Phase 4: Everskies Daily Boutique, Wardrobe, and Avatar System."""

from __future__ import annotations

import base64
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from aiogram import Bot, Dispatcher
from aiogram.types import CallbackQuery, Chat, Message, Update, User

from database.connection import db
from database.seed import seed_catalog
from models.enums import ActivityKind
from services import avatar, economy, game

pytestmark = pytest.mark.asyncio


def _ensure_test_catalog():
    p = Path("/tmp/bot_catalog.db")
    if p.exists():
        p.unlink()
    conn = sqlite3.connect(p)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS bot_sets (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            gender TEXT NOT NULL,
            item_count INTEGER NOT NULL,
            var_ids TEXT NOT NULL,
            is_plus_size INTEGER NOT NULL DEFAULT 0
        )
    """)
    for i in range(1, 31):
        if i <= 10:
            g = "female"
        elif i <= 20:
            g = "male"
        else:
            g = "unisex"
        conn.execute(
            "INSERT INTO bot_sets VALUES (?, ?, ?, ?, ?, ?)",
            (i, f"Set {i}", g, 3, f"[{1000 + i}, {2000 + i}]", 0),
        )
    conn.commit()
    conn.close()


@pytest.fixture(autouse=True)
async def _setup_db():
    _ensure_test_catalog()
    from handlers import boutique, salon
    boutique.router._parent_router = None
    salon.router._parent_router = None
    await db.connect()
    await seed_catalog()
    yield
    boutique.router._parent_router = None
    salon.router._parent_router = None
    await db.close()


async def test_cdn_render_url():
    """Verify that Everskies CDN composite URLs are base64-encoded and formatted."""
    vars_ = [-1, 100, 200]
    url = avatar.get_render_url(vars_, width=400, crop=True)
    assert url.startswith("https://cdn.everskies.com/render/")
    assert "x=400" in url
    assert "m=1" in url

    # Decode slug
    slug = url.split("/render/")[1].split(".png")[0]
    rem = len(slug) % 4
    if rem:
        slug += "=" * (4 - rem)
    decoded = json.loads(base64.b64decode(slug).decode("utf-8"))
    assert decoded == vars_


async def test_daily_boutique_rotation_deterministic():
    """Verify deterministic 8-set daily selection across categories."""
    date_key = "2026-10-20"

    sets_f1 = avatar.get_daily_boutique("female", date_key=date_key)
    sets_f2 = avatar.get_daily_boutique("female", date_key=date_key)
    assert len(sets_f1) == 8
    assert [s["id"] for s in sets_f1] == [s["id"] for s in sets_f2]

    sets_m = avatar.get_daily_boutique("male", date_key=date_key)
    assert len(sets_m) == 8
    assert all(s["gender"] == "male" for s in sets_m)

    sets_u = avatar.get_daily_boutique("unisex", date_key=date_key)
    assert len(sets_u) == 8
    assert all(s["gender"] == "unisex" for s in sets_u)


async def test_avatar_db_and_wardrobe_lifecycle():
    """Full lifecycle: init avatar -> buy set -> equip -> unequip -> change skin."""
    user_id = 888777111
    await game.ensure_player(
        user_id=user_id,
        username="fashion_king",
        display_name="سلطان مد",
    )
    # Grant credits for shopping
    await economy.grant(user_id, credits=50000, kind=ActivityKind.ADMIN)

    # 1. Default avatar
    av = await avatar.get_player_avatar(user_id)
    assert av["user_id"] == user_id
    assert av["skin_tone"] in (-1, -2, -3, -4, -5, -6)
    assert av["active_set_id"] is None
    assert av["equipped_var_ids"] == []

    # 2. Daily sets
    daily_sets = avatar.get_daily_boutique("female")
    assert len(daily_sets) == 8
    chosen_set = daily_sets[0]
    set_id = chosen_set["id"]

    # 3. Buy set
    price, s_info = await avatar.buy_boutique_set(user_id, set_id, player_drip=0)
    assert price > 0
    assert s_info["id"] == set_id
    assert await avatar.owns_set(user_id, set_id) is True

    # 4. Duplicate purchase should fail
    with pytest.raises(avatar.AlreadyOwned):
        await avatar.buy_boutique_set(user_id, set_id, player_drip=0)

    # 5. Wardrobe listing
    wdr = await avatar.list_wardrobe(user_id)
    assert len(wdr) == 1
    assert wdr[0]["set_id"] == set_id

    # 6. Equip set
    equipped = await avatar.equip_set(user_id, set_id)
    assert equipped["set_id"] == set_id
    av_equipped = await avatar.get_player_avatar(user_id)
    assert av_equipped["active_set_id"] == set_id
    assert av_equipped["equipped_var_ids"] == chosen_set["var_ids"]

    # 7. Render URL contains both skin tone and item var_ids
    render_url = await avatar.get_player_render_url(user_id)
    assert "https://cdn.everskies.com/render/" in render_url

    # 8. Change skin tone
    await avatar.set_skin_tone(user_id, -4)
    av_new_skin = await avatar.get_player_avatar(user_id)
    assert av_new_skin["skin_tone"] == -4

    # 9. Unequip
    await avatar.unequip(user_id)
    av_unequipped = await avatar.get_player_avatar(user_id)
    assert av_unequipped["active_set_id"] is None
    assert av_unequipped["equipped_var_ids"] == []

    # Cleanup test user
    async with db.write() as conn:
        await conn.execute("DELETE FROM player_wardrobe WHERE user_id = ?", (user_id,))
        await conn.execute("DELETE FROM player_avatar WHERE user_id = ?", (user_id,))
        await conn.execute("DELETE FROM wallets WHERE user_id = ?", (user_id,))
        await conn.execute("DELETE FROM players WHERE user_id = ?", (user_id,))


async def test_boutique_and_wardrobe_text_commands(monkeypatch):
    """Verify Persian plain text triggers for boutique and wardrobe."""
    from handlers import boutique
    boutique.router._parent_router = None
    dp = Dispatcher()
    dp.include_router(boutique.router)

    photos_sent = []
    replies = []

    async def fake_reply(self, text, **kwargs):
        replies.append(text)
        return self

    async def fake_reply_photo(self, photo, caption=None, reply_markup=None, **kwargs):
        photos_sent.append({"photo": photo, "caption": caption, "markup": reply_markup})
        return self

    monkeypatch.setattr(Message, "reply", fake_reply)
    monkeypatch.setattr(Message, "reply_photo", fake_reply_photo)

    bot = Bot(token="123456789:***")
    chat = Chat(id=777, type="private")
    user = User(id=777, is_bot=False, first_name="Aria", username="aria")
    now = datetime.now(UTC)

    # 1. Trigger "بوتیک"
    msg_btq = Message(message_id=1, date=now, chat=chat, from_user=user, text="بوتیک")
    await dp.feed_update(bot=bot, update=Update(update_id=1, message=msg_btq))
    assert len(photos_sent) == 1
    assert "بوتیک" in photos_sent[-1]["caption"]
    assert "https://cdn.everskies.com/render/" in photos_sent[-1]["photo"]

    # 2. Trigger "کمد"
    msg_wdr = Message(message_id=2, date=now, chat=chat, from_user=user, text="کمد")
    await dp.feed_update(bot=bot, update=Update(update_id=2, message=msg_wdr))
    assert len(photos_sent) == 2
    assert "کمد لباس" in photos_sent[-1]["caption"]

    # 3. Trigger "آواتار"
    msg_av = Message(message_id=3, date=now, chat=chat, from_user=user, text="آواتار")
    await dp.feed_update(bot=bot, update=Update(update_id=3, message=msg_av))
    assert len(photos_sent) == 3
    assert "پوست" in photos_sent[-1]["caption"]

    # Cleanup
    async with db.write() as conn:
        await conn.execute("DELETE FROM player_wardrobe WHERE user_id = ?", (user.id,))
        await conn.execute("DELETE FROM player_avatar WHERE user_id = ?", (user.id,))
        await conn.execute("DELETE FROM wallets WHERE user_id = ?", (user.id,))
        await conn.execute("DELETE FROM players WHERE user_id = ?", (user.id,))


async def test_boutique_carousel_callbacks(monkeypatch):
    """Verify single-message edit_media navigation and interaction callbacks."""
    from handlers import boutique
    boutique.router._parent_router = None
    dp = Dispatcher()
    dp.include_router(boutique.router)

    media_edits = []

    async def fake_edit_media(self, media, reply_markup=None, **kwargs):
        media_edits.append({"media": media, "markup": reply_markup})
        return self

    monkeypatch.setattr(Message, "edit_media", fake_edit_media)
    monkeypatch.setattr(CallbackQuery, "answer", AsyncMock())

    bot = Bot(token="123456789:***")
    chat = Chat(id=555, type="private")
    user = User(id=555, is_bot=False, first_name="Aria", username="aria")
    now = datetime.now(UTC)

    # Ensure player and credits
    await game.ensure_player(user_id=user.id, username="aria", display_name="Aria")
    await economy.grant(user.id, credits=50000, kind=ActivityKind.ADMIN)

    dummy_msg = Message(message_id=10, date=now, chat=chat, from_user=user, text="dummy")

    # 1. Navigation callback: btq:nav:female:1:0
    call_nav = CallbackQuery(
        id="c1",
        from_user=user,
        chat_instance="ci",
        message=dummy_msg,
        data="btq:nav:female:1:0",
    )
    await dp.feed_update(bot=bot, update=Update(update_id=10, callback_query=call_nav))
    assert len(media_edits) == 1
    assert "بوتیک" in media_edits[-1]["media"].caption

    # 2. Preview toggle callback: btq:prev:female:1:1
    call_prev = CallbackQuery(
        id="c2",
        from_user=user,
        chat_instance="ci",
        message=dummy_msg,
        data="btq:prev:female:1:1",
    )
    await dp.feed_update(bot=bot, update=Update(update_id=11, callback_query=call_prev))
    assert len(media_edits) == 2
    assert "پیش‌نمایش" in media_edits[-1]["media"].caption

    # 3. Category switch: btq:cat:male
    call_cat = CallbackQuery(
        id="c3",
        from_user=user,
        chat_instance="ci",
        message=dummy_msg,
        data="btq:cat:male",
    )
    await dp.feed_update(bot=bot, update=Update(update_id=12, callback_query=call_cat))
    assert len(media_edits) == 3

    # Cleanup
    async with db.write() as conn:
        await conn.execute("DELETE FROM player_wardrobe WHERE user_id = ?", (user.id,))
        await conn.execute("DELETE FROM player_avatar WHERE user_id = ?", (user.id,))
        await conn.execute("DELETE FROM wallets WHERE user_id = ?", (user.id,))
        await conn.execute("DELETE FROM players WHERE user_id = ?", (user.id,))
