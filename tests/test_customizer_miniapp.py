"""Tests for Telegram Mini App character customizer integration."""

import json
import sqlite3
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from config import ROOT_DIR
from database.connection import db
from database.seed import seed_catalog
from handlers.boutique import (
    CUSTOMIZER_COMMANDS,
    handle_customizer_command,
    handle_customizer_webapp_data,
)
from services import avatar, game

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
        g = "female" if i <= 10 else ("male" if i <= 20 else "unisex")
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


async def test_customizer_webapp_file_exists_and_valid():
    """Verify customizer_webapp.html exists, has Telegram SDK and 5 primary zones."""
    html_path = ROOT_DIR / "tools" / "customizer_webapp.html"
    assert html_path.exists(), "customizer_webapp.html must exist"

    content = html_path.read_text(encoding="utf-8")
    assert "telegram-web-app.js" in content
    assert "Head (اجزای سر)" in content
    assert "Top (بالاتنه)" in content
    assert "Bottom (پایین‌تنه)" in content
    assert "Shoes (کفش و پا)" in content
    assert "Extra (اکسسوری)" in content
    assert "RAW_ITEMS" in content
    assert "HAIR_SETS" in content
    assert "tg.sendData" in content or "Telegram.WebApp.sendData" in content


async def test_save_custom_avatar_slim_and_plus():
    """Test saving custom avatar composition and generating valid render URL."""
    test_user_id = 999123
    await game.ensure_player(
        user_id=test_user_id,
        username="tester_customizer",
        display_name="تستر کاستومایز",
    )

    try:
        # 1. Save Slim avatar with 3 custom items
        res_slim = await avatar.save_custom_avatar(
            user_id=test_user_id,
            var_ids=[1001, 1002, 1003],
            skin_tone=-2,
            is_plus_size=0,
        )
        assert res_slim["equipped_var_ids"] == [1001, 1002, 1003]
        assert res_slim["skin_tone"] == -2
        assert res_slim["is_plus_size"] == 0

        url_slim = await avatar.get_player_render_url(test_user_id)
        assert "cdn.everskies.com/render/" in url_slim

        # 2. Save Plus size avatar
        res_plus = await avatar.save_custom_avatar(
            user_id=test_user_id,
            var_ids=[2001, 2002],
            skin_tone=-7,
            is_plus_size=1,
        )
        assert res_plus["equipped_var_ids"] == [2001, 2002]
        assert res_plus["skin_tone"] == -7
        assert res_plus["is_plus_size"] == 1

        url_plus = await avatar.get_player_render_url(test_user_id)
        assert "cdn.everskies.com/render/" in url_plus
    finally:
        async with db.write() as conn:
            await conn.execute("DELETE FROM player_avatar WHERE user_id = ?", (test_user_id,))
            await conn.execute("DELETE FROM inventory WHERE user_id = ?", (test_user_id,))
            await conn.execute("DELETE FROM players WHERE user_id = ?", (test_user_id,))


async def test_handle_customizer_webapp_data_handler():
    """Test receiving WebApp payload and updating avatar."""
    test_user_id = 999124
    await game.ensure_player(
        user_id=test_user_id,
        username="webapp_tester",
        display_name="تستر وب‌اپ",
    )

    try:
        # Mock message with web_app_data
        payload = {
            "skin": 3,
            "body": "slim",
            "items": [101, 102, 103, 104],
        }
        msg = AsyncMock()
        msg.from_user.id = test_user_id
        msg.web_app_data.data = json.dumps(payload)
        msg.reply_photo = AsyncMock()

        await handle_customizer_webapp_data(msg)
        assert msg.reply_photo.called

        av = await avatar.get_player_avatar(test_user_id)
        assert av["equipped_var_ids"] == [101, 102, 103, 104]
        assert av["skin_tone"] == -3  # slim 3 -> -3
        assert av["is_plus_size"] == 0
    finally:
        async with db.write() as conn:
            await conn.execute("DELETE FROM player_avatar WHERE user_id = ?", (test_user_id,))
            await conn.execute("DELETE FROM inventory WHERE user_id = ?", (test_user_id,))
            await conn.execute("DELETE FROM players WHERE user_id = ?", (test_user_id,))


async def test_wear_deep_link_saves_loadout():
    """Dual-path save: /start wear_<b64> must persist the mini-app loadout."""
    import base64

    from handlers.onboarding import _handle_wear_payload

    test_user_id = 999126
    await game.ensure_player(
        user_id=test_user_id,
        username="wear_tester",
        display_name="تستر دیپ‌لینک",
    )

    try:
        payload = {"skin": 2, "body": "slim", "items": [111, 222]}
        encoded = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
        msg = AsyncMock()
        msg.from_user.id = test_user_id
        msg.from_user.username = "wear_tester"
        msg.from_user.full_name = "تستر دیپ‌لینک"
        msg.reply_photo = AsyncMock()
        msg.reply = AsyncMock()

        await _handle_wear_payload(msg, encoded)

        av = await avatar.get_player_avatar(test_user_id)
        assert av["equipped_var_ids"] == [111, 222]
        assert av["skin_tone"] == -2  # slim 2 -> -2
        assert av["is_plus_size"] == 0
    finally:
        async with db.write() as conn:
            await conn.execute("DELETE FROM player_avatar WHERE user_id = ?", (test_user_id,))
            await conn.execute("DELETE FROM inventory WHERE user_id = ?", (test_user_id,))
            await conn.execute("DELETE FROM players WHERE user_id = ?", (test_user_id,))


async def test_wear_deep_link_plus_size():
    """Plus-size payload ('body': 'plus') maps to plus skin IDs."""
    import base64

    from handlers.onboarding import _handle_wear_payload

    test_user_id = 999127
    await game.ensure_player(
        user_id=test_user_id,
        username="wear_plus_tester",
        display_name="تستر پلاس",
    )

    try:
        payload = {"skin": 1, "body": "plus", "items": [333]}
        encoded = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
        msg = AsyncMock()
        msg.from_user.id = test_user_id
        msg.from_user.username = "wear_plus_tester"
        msg.from_user.full_name = "تستر پلاس"
        msg.reply_photo = AsyncMock()
        msg.reply = AsyncMock()

        await _handle_wear_payload(msg, encoded)

        av = await avatar.get_player_avatar(test_user_id)
        assert av["equipped_var_ids"] == [333]
        assert av["skin_tone"] == -7  # plus 1 -> -7
        assert av["is_plus_size"] == 1
    finally:
        async with db.write() as conn:
            await conn.execute("DELETE FROM player_avatar WHERE user_id = ?", (test_user_id,))
            await conn.execute("DELETE FROM inventory WHERE user_id = ?", (test_user_id,))
            await conn.execute("DELETE FROM players WHERE user_id = ?", (test_user_id,))


async def test_handle_customizer_command():
    """Verify customizer launcher command responds with photo and webapp markup."""
    assert "کاستومایز" in CUSTOMIZER_COMMANDS
    assert "app" in CUSTOMIZER_COMMANDS

    test_user_id = 999125
    await game.ensure_player(
        user_id=test_user_id,
        username="cmd_tester",
        display_name="تستر کامند",
    )
    try:
        msg = AsyncMock()
        msg.from_user.id = test_user_id
        msg.from_user.username = "cmd_tester"
        msg.from_user.full_name = "تستر کامند"
        msg.reply_photo = AsyncMock()

        await handle_customizer_command(msg)
        assert msg.reply_photo.called
        call_kwargs = msg.reply_photo.call_args[1]
        assert "مینی‌اپ شخصی‌سازی" in call_kwargs["caption"]
        markup = call_kwargs["reply_markup"]
        assert markup.inline_keyboard[0][0].web_app is not None
        assert "github.io" in markup.inline_keyboard[0][0].web_app.url
        assert "?d=" in markup.inline_keyboard[0][0].web_app.url
    finally:
        async with db.write() as conn:
            await conn.execute("DELETE FROM player_avatar WHERE user_id = ?", (test_user_id,))
            await conn.execute("DELETE FROM inventory WHERE user_id = ?", (test_user_id,))
            await conn.execute("DELETE FROM players WHERE user_id = ?", (test_user_id,))


async def test_get_customizer_url_payload_structure():
    """Verify get_customizer_url embeds full player stats and equipped items."""
    import base64
    test_user_id = 999128
    player = await game.ensure_player(
        user_id=test_user_id,
        username="url_tester",
        display_name="تستر لینک",
    )
    try:
        await avatar.save_custom_avatar(
            user_id=test_user_id,
            var_ids=[555, 666],
            skin_tone=-3,
            is_plus_size=0,
        )
        url = await avatar.get_customizer_url(test_user_id)
        assert "?d=" in url
        b64_part = url.split("?d=")[1]
        padded = b64_part + "=" * (-len(b64_part) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))

        assert payload["u"] == test_user_id
        assert payload["name"] == "تستر لینک"
        assert payload["lvl"] == player.level
        assert payload["items"] == [555, 666]
        assert payload["skin"] == 3
        assert payload["body"] == "slim"
        assert payload["bot"] == "hermesforrexbot"
    finally:
        async with db.write() as conn:
            await conn.execute("DELETE FROM player_avatar WHERE user_id = ?", (test_user_id,))
            await conn.execute("DELETE FROM inventory WHERE user_id = ?", (test_user_id,))
            await conn.execute("DELETE FROM players WHERE user_id = ?", (test_user_id,))


async def test_wear_deep_link_completes_onboarding_for_new_player():
    """When a new player saves from mini-app, onboarding_completed becomes 1."""
    import base64

    from handlers.onboarding import _handle_wear_payload

    test_user_id = 999129
    # Fresh player with onboarding_completed = 0
    await game.ensure_player(
        user_id=test_user_id,
        username="fresh_citizen",
        display_name="شهروند تازه",
    )
    p_before = await game.load_player(test_user_id)
    assert p_before.onboarding_completed == 0

    try:
        payload = {"skin": 1, "body": "slim", "items": [101, 202]}
        encoded = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
        msg = AsyncMock()
        msg.from_user.id = test_user_id
        msg.from_user.username = "fresh_citizen"
        msg.from_user.full_name = "شهروند تازه"
        msg.reply_photo = AsyncMock()
        msg.reply = AsyncMock()

        await _handle_wear_payload(msg, encoded)

        p_after = await game.load_player(test_user_id)
        assert p_after.onboarding_completed == 1

        av = await avatar.get_player_avatar(test_user_id)
        assert av["equipped_var_ids"] == [101, 202]

        # Verify congratulations caption was sent
        assert msg.reply.called or msg.reply_photo.called
        call_kwargs = msg.reply.call_args[1] if msg.reply.called else msg.reply_photo.call_args[1]
        caption_text = msg.reply.call_args[0][0] if msg.reply.called else call_kwargs["caption"]
        assert "شناسنامه شهروندی تیرامیکس صادر شد" in caption_text
    finally:
        async with db.write() as conn:
            await conn.execute("DELETE FROM player_avatar WHERE user_id = ?", (test_user_id,))
            await conn.execute("DELETE FROM inventory WHERE user_id = ?", (test_user_id,))
            await conn.execute("DELETE FROM players WHERE user_id = ?", (test_user_id,))
