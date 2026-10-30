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
        assert "bot" in payload
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


async def test_compact_binary_wear_deep_link():
    """Verify compact binary deep-link w_<b64> saves avatar correctly under 64 chars."""
    import base64

    from aiogram.fsm.context import FSMContext

    from handlers.onboarding import cmd_start_wear

    test_user_id = 999130
    await game.ensure_player(
        user_id=test_user_id,
        username="compact_tester",
        display_name="تستر فشرده",
    )

    try:
        # Binary pack: skin 3, slim, items [4024707, 4027798]
        header = 3 & 0x3F
        bytes_data = bytearray([header])
        for v in [4024707, 4027798]:
            bytes_data.extend([(v >> 16) & 0xFF, (v >> 8) & 0xFF, v & 0xFF])
        b64 = base64.urlsafe_b64encode(bytes(bytes_data)).decode("ascii").rstrip("=")

        assert len(f"w_{b64}") <= 64, "Deep-link start param must be <= 64 characters"

        msg = AsyncMock()
        msg.text = f"/start w_{b64}"
        msg.from_user.id = test_user_id
        msg.from_user.username = "compact_tester"
        msg.from_user.full_name = "تستر فشرده"
        msg.reply_photo = AsyncMock()
        msg.reply = AsyncMock()

        state = AsyncMock(spec=FSMContext)
        await cmd_start_wear(msg, state)

        av = await avatar.get_player_avatar(test_user_id)
        assert av["equipped_var_ids"] == [4024707, 4027798]
        assert av["skin_tone"] == -3
        assert av["is_plus_size"] == 0

        p_res = await game.load_player(test_user_id)
        assert p_res is not None
        assert p_res.onboarding_completed == 1
    finally:
        async with db.write() as conn:
            await conn.execute("DELETE FROM player_avatar WHERE user_id = ?", (test_user_id,))
            await conn.execute("DELETE FROM inventory WHERE user_id = ?", (test_user_id,))
            await conn.execute("DELETE FROM players WHERE user_id = ?", (test_user_id,))


async def test_starter_sets_count_is_valid():
    """Verify curated starter sets exist for each gender from user curated list."""
    female_starters = avatar.get_starter_sets("female")
    male_starters = avatar.get_starter_sets("male")

    assert len(female_starters) >= 4, (
        f"Expected >= 4 female starter sets, got {len(female_starters)}"
    )
    assert len(male_starters) >= 2, (
        f"Expected >= 2 male starter sets, got {len(male_starters)}"
    )

    # Ensure sets 105 and 201 exist with valid piece counts
    set_105 = avatar.get_set_from_catalog(105)
    set_201 = avatar.get_set_from_catalog(201)
    assert set_105 is not None
    assert set_201 is not None
    assert len(set_105["var_ids"]) >= 6
    assert len(set_201["var_ids"]) >= 6


async def test_get_customizer_url_starter_vs_wardrobe_mode():
    """Verify get_customizer_url sets starter mode for new and wardrobe for existing."""
    import base64

    test_user_id = 999139
    await game.ensure_player(test_user_id, "Mode Tester", "modetester")

    try:
        # 1. New player (onboarding_completed == 0) -> mode == 'starter'
        url_new = await avatar.get_customizer_url(test_user_id)
        b64_new = url_new.split("?d=")[1]
        payload_new = json.loads(base64.urlsafe_b64decode(b64_new + "==").decode())
        assert payload_new["mode"] == "starter"
        assert payload_new["owned"] == []

        # 2. Complete onboarding & assign starter set
        await avatar.assign_starter_set(test_user_id, set_id=101, gender="female")
        async with db.write() as conn:
            await conn.execute(
                "UPDATE players SET onboarding_completed = 1 WHERE user_id = ?",
                (test_user_id,),
            )

        # 3. Existing player -> mode == 'wardrobe' with owned items
        url_existing = await avatar.get_customizer_url(test_user_id)
        b64_exist = url_existing.split("?d=")[1]
        payload_exist = json.loads(base64.urlsafe_b64decode(b64_exist + "==").decode())
        assert payload_exist["mode"] == "wardrobe"
        assert len(payload_exist["owned"]) > 0
        assert set(avatar.STARTER_SETS[101]["var_ids"]).issubset(set(payload_exist["owned"]))
        assert len(payload_exist["owned_sets"]) >= 1
    finally:
        async with db.write() as conn:
            await conn.execute("DELETE FROM player_wardrobe WHERE user_id = ?", (test_user_id,))
            await conn.execute("DELETE FROM player_avatar WHERE user_id = ?", (test_user_id,))
            await conn.execute("DELETE FROM players WHERE user_id = ?", (test_user_id,))


async def test_get_customizer_url_gender_enforcement():
    """Verify get_customizer_url strictly embeds selected gender into payload."""
    import base64

    # 1. Male gender passed explicitly
    url_male = await avatar.get_customizer_url(user_id=8881, gender="male")
    b64_male = url_male.split("?d=")[1]
    payload_male = json.loads(base64.urlsafe_b64decode(b64_male + "==").decode())
    assert payload_male["gen"] == "male"

    # 2. Female gender passed explicitly
    url_female = await avatar.get_customizer_url(user_id=8882, gender="female")
    b64_female = url_female.split("?d=")[1]
    payload_female = json.loads(base64.urlsafe_b64decode(b64_female + "==").decode())
    assert payload_female["gen"] == "female"


async def test_bottom_wear_enforcement():
    """Verify bottom-wear validation ensures character is never naked without pants/skirt."""
    # 1. has_bottom_item correctly identifies bottoms vs non-bottoms
    assert avatar.has_bottom_item([272755]) is True  # male pants
    assert avatar.has_bottom_item([4027832]) is True  # female skirt
    assert avatar.has_bottom_item([103751]) is True  # dress
    assert avatar.has_bottom_item([]) is False  # empty
    assert avatar.has_bottom_item([4024707]) is False  # hair only

    # 2. ensure_bottom_item equips default bottom if none equipped
    male_ensured = avatar.ensure_bottom_item([4024707], gender="male")
    assert avatar.DEFAULT_STARTER_BOTTOM["male"] in male_ensured
    female_ensured = avatar.ensure_bottom_item([4024707], gender="female")
    assert avatar.DEFAULT_STARTER_BOTTOM["female"] in female_ensured

    # 3. If already has bottom, ensure_bottom_item does not duplicate or alter
    already_good = avatar.ensure_bottom_item([23301], gender="female")
    assert already_good == [23301]


async def test_wardrobe_items_not_deleted_on_unequip():
    """Verify taking off an item in Mini App does not delete it from owned wardrobe."""
    import base64

    test_user_id = 999333
    await game.ensure_player(test_user_id, "Unequip Tester", "unequiptester")

    try:
        # 1. Player is assigned starter set 207 (10 pieces)
        await avatar.assign_starter_set(test_user_id, set_id=207, gender="female")
        async with db.write() as conn:
            await conn.execute(
                "UPDATE players SET onboarding_completed = 1 WHERE user_id = ?",
                (test_user_id,),
            )

        # Starter set has 10 pieces including boots 1748
        st_vars = avatar.STARTER_SETS[207]["var_ids"]
        assert 1748 in st_vars

        # 2. Player opens customizer, unequips boots (1748), and saves 9 pieces
        unequipped_vars = [v for v in st_vars if v != 1748]
        await avatar.save_custom_avatar(
            user_id=test_user_id,
            var_ids=unequipped_vars,
            skin_tone=-1,
            is_plus_size=0,
        )

        # 3. Check customizer URL: boots 1748 MUST STILL be in owned items!
        url = await avatar.get_customizer_url(test_user_id)
        b64_data = url.split("?d=")[1]
        payload = json.loads(base64.urlsafe_b64decode(b64_data + "==").decode())

        assert payload["mode"] == "wardrobe"
        # Equipped items is 9
        assert 1748 not in payload["items"]
        # BUT owned items STILL has 1748 (not deleted)!
        assert 1748 in payload["owned"]
        assert set(st_vars).issubset(set(payload["owned"]))
    finally:
        async with db.write() as conn:
            await conn.execute("DELETE FROM player_wardrobe WHERE user_id = ?", (test_user_id,))
            await conn.execute("DELETE FROM player_avatar WHERE user_id = ?", (test_user_id,))
            await conn.execute("DELETE FROM players WHERE user_id = ?", (test_user_id,))


async def test_outfit_switch_menu_and_custom_preset_saving():
    """Verify change-outfit menu lists all owned sets and custom preset saving works."""
    from handlers.boutique import _build_outfit_switch_menu

    test_user_id = 999334
    await game.ensure_player(test_user_id, "Switch Tester", "switchtester")

    try:
        # 1. Assign starter set
        await avatar.assign_starter_set(test_user_id, set_id=201, gender="male")

        # 2. Add custom preset set
        await avatar.add_set_to_wardrobe(
            user_id=test_user_id,
            set_id=10555,
            set_name="✨ تیپ شیک خیابانی",
            gender="male",
            var_ids=[28144, 33817, 116590],
        )

        # 3. Build outfit switcher menu
        render_url, caption, markup = await _build_outfit_switch_menu(test_user_id)
        assert "منوی تعویض لباس" in caption
        assert render_url.startswith("https://cdn.everskies.com/render/")

        # Verify buttons list the sets
        btn_texts = [
            b.text for row in markup.inline_keyboard for b in row if b.text
        ]
        assert any("تیپ شیک خیابانی" in t for t in btn_texts)
        assert any("Starter Pack (Boys) #1" in t for t in btn_texts)

        # 4. Verify list_wardrobe puts active look at index 0
        wdr = await avatar.list_wardrobe(test_user_id)
        assert len(wdr) >= 2
        # Current active look is index 0
        assert wdr[0]["set_id"] in (201, 9999)
    finally:
        async with db.write() as conn:
            await conn.execute("DELETE FROM player_wardrobe WHERE user_id = ?", (test_user_id,))
            await conn.execute("DELETE FROM player_avatar WHERE user_id = ?", (test_user_id,))
            await conn.execute("DELETE FROM players WHERE user_id = ?", (test_user_id,))


async def test_wardrobe_message_live_refresh_on_miniapp_save():
    """Verify that if player has an open wardrobe card, Mini App save refreshes it in-place."""
    import base64
    from unittest.mock import AsyncMock, MagicMock

    from handlers.onboarding import _handle_compact_wear_payload

    test_user_id = 999335
    await game.ensure_player(test_user_id, "Live Refresh Tester", "liverefresh")

    try:
        # Mock active wardrobe message tracking
        avatar.set_user_wardrobe_message(test_user_id, chat_id=-10012345, message_id=888)

        # Mock incoming message with bot instance
        msg = AsyncMock()
        msg.from_user.id = test_user_id
        msg.from_user.full_name = "Live Refresh Tester"
        msg.from_user.username = "liverefresh"
        msg.reply_photo = AsyncMock()
        msg.reply = AsyncMock()
        mock_bot = MagicMock()
        mock_bot.edit_message_media = AsyncMock()
        msg.bot = mock_bot

        # Save new look via compact payload
        header = 1  # slim, skin 1, female 0
        raw_bytes = bytearray([header])
        for v in [28144, 33817, 116590]:
            raw_bytes.extend([(v >> 16) & 0xFF, (v >> 8) & 0xFF, v & 0xFF])
        b64 = base64.urlsafe_b64encode(bytes(raw_bytes)).decode().rstrip("=")

        await _handle_compact_wear_payload(msg, b64)

        # Verify bot.edit_message_media was called on the tracked wardrobe message!
        assert mock_bot.edit_message_media.called
        call_kwargs = mock_bot.edit_message_media.call_args.kwargs
        assert call_kwargs["chat_id"] == -10012345
        assert call_kwargs["message_id"] == 888
    finally:
        avatar.clear_user_wardrobe_message(test_user_id)
        async with db.write() as conn:
            await conn.execute("DELETE FROM player_wardrobe WHERE user_id = ?", (test_user_id,))
            await conn.execute("DELETE FROM player_avatar WHERE user_id = ?", (test_user_id,))
            await conn.execute("DELETE FROM players WHERE user_id = ?", (test_user_id,))
