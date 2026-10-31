"""Comprehensive tests for Salon, Barber, Solarium (Paid Skin) and Makeup studio."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from aiogram import Bot, Dispatcher
from aiogram.types import CallbackQuery, Chat, Message, Update, User

from database.connection import db
from database.seed import seed_catalog
from models.enums import ActivityKind
from services import avatar, economy, game, salon

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
async def _setup_db():
    from handlers import boutique
    from handlers import salon as salon_h

    boutique.router._parent_router = None
    salon_h.router._parent_router = None
    await db.connect()
    await seed_catalog()
    yield
    boutique.router._parent_router = None
    salon_h.router._parent_router = None
    await db.close()


async def test_salon_state_retrieval():
    """Verify salon state extracts appearance, makeup, and credits."""
    user_id = 90001
    await game.ensure_player(user_id=user_id, username="solaris", display_name="Solaris")
    await economy.grant(user_id, credits=5000, kind=ActivityKind.ADMIN)

    st = await salon.get_player_salon_state(user_id)
    assert st["user_id"] == user_id
    # Verify credits: initial 250 + 5000 = 5250
    assert st["credits"] == 5250
    assert "render_url" in st
    assert st["skin_tone_id"] in (-1, -2)
    assert "hair_label" in st
    assert "eye_label" in st
    assert "nose_label" in st
    assert "ear_label" in st
    assert "lip_label" in st


async def test_paid_skin_tone_solarium():
    """Verify changing skin tone is paid (500 credits) and syncs both tables."""
    user_id = 90002
    await game.ensure_player(user_id=user_id, username="tanner", display_name="Tanner")

    # 1. Fail when insufficient credits (initial credits: 250 < 500)
    res_fail = await salon.change_skin_tone_paid(user_id, -3)
    assert not res_fail["success"]
    assert "سکه کافی نداری" in res_fail["message"]

    # 2. Add credits and succeed
    await economy.grant(user_id, credits=1000, kind=ActivityKind.ADMIN)
    res_ok = await salon.change_skin_tone_paid(user_id, -3)
    assert res_ok["success"]
    assert res_ok["skin_tone_id"] == -3

    # Check remaining credits: initial 250 + 1000 - 500 = 750
    bal, _ = await economy.balances(user_id)
    assert bal == 750

    # Verify updated state
    st = await salon.get_player_salon_state(user_id)
    assert st["skin_tone_id"] == -3
    assert "برنزه" in st["skin_label"]


async def test_paid_makeup_subsystems():
    """Verify hair, eyes, nose, ears and lips makeup each charge and save accurately."""
    user_id = 90003
    await game.ensure_player(user_id=user_id, username="glam", display_name="Glam")
    await economy.grant(user_id, credits=10000, kind=ActivityKind.ADMIN)

    # Hair (350 credits)
    res_hair = await salon.change_hair_paid(user_id, "hair2", "silver")
    assert res_hair["success"]
    assert res_hair["fee"] == 350

    # Eyes (300 credits)
    res_eye = await salon.change_eyes_paid(user_id, "eyes2_1", "crimson")
    assert res_eye["success"]
    assert res_eye["fee"] == 300

    # Nose (250 credits)
    res_nose = await salon.change_nose_paid(user_id, "nose_diamond_stud")
    assert res_nose["success"]
    assert res_nose["fee"] == 250

    # Ears (250 credits)
    res_ear = await salon.change_ears_paid(user_id, "ear_ruby_drop")
    assert res_ear["success"]
    assert res_ear["fee"] == 250

    # Lips (250 credits)
    res_lip = await salon.change_lips_paid(user_id, "lip_velvet_cherry")
    assert res_lip["success"]
    assert res_lip["fee"] == 250

    # Total spent: 350 + 300 + 250 + 250 + 250 = 1400. Initial 250 + 10000 - 1400 = 8850
    bal, _ = await economy.balances(user_id)
    assert bal == 10250 - 1400

    # Check verified state
    st = await salon.get_player_salon_state(user_id)
    assert st["hair_style"] == "hair2"
    assert st["hair_color"] == "silver"
    assert st["eye_style"] == "eyes2_1"
    assert st["eye_color"] == "crimson"
    assert st["nose_style"] == "nose_diamond_stud"
    assert st["ear_style"] == "ear_ruby_drop"
    assert st["lip_style"] == "lip_velvet_cherry"


async def test_salon_text_commands(monkeypatch):
    """Verify salon, solarium and beauty text commands trigger correctly."""
    from handlers import salon as salon_h
    salon_h.router._parent_router = None

    dp = Dispatcher()
    dp.include_router(salon_h.router)

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

    bot = Bot(token="123456789:ABCdefGHIjklMNOpqrsTUVwxyz123456789")
    user_id = 90004
    await game.ensure_player(
        user_id=user_id, username="barber_tester", display_name="Barber Tester"
    )
    await economy.grant(user_id, credits=5000, kind=ActivityKind.ADMIN)

    tg_user = User(id=user_id, is_bot=False, first_name="Barber", username="barber_tester")
    chat = Chat(id=user_id, type="private")
    now = datetime.now(UTC)

    # 1. "آرایشگاه" text command
    msg1 = Message(
        message_id=1,
        date=now,
        chat=chat,
        from_user=tg_user,
        text="آرایشگاه",
    )
    upd1 = Update(update_id=1, message=msg1)
    await dp.feed_update(bot, upd1)
    assert len(photos_sent) == 1
    assert "سولاریوم" in photos_sent[-1]["caption"]

    # 2. Quick command: "آرایشگاه 3 بلوند"
    msg2 = Message(
        message_id=2,
        date=now,
        chat=chat,
        from_user=tg_user,
        text="آرایشگاه 3 بلوند",
    )
    upd2 = Update(update_id=2, message=msg2)
    await dp.feed_update(bot, upd2)
    assert len(replies) >= 1

    st2 = await salon.get_player_salon_state(user_id)
    assert st2["hair_style"] == "hair3"
    assert st2["hair_color"] == "blonde"

    # 3. Quick solarium command: "سولاریوم 4"
    msg3 = Message(
        message_id=3,
        date=now,
        chat=chat,
        from_user=tg_user,
        text="سولاریوم 4",
    )
    upd3 = Update(update_id=3, message=msg3)
    await dp.feed_update(bot, upd3)

    st3 = await salon.get_player_salon_state(user_id)
    assert st3["skin_tone_id"] == -4


async def test_salon_callbacks_navigation(monkeypatch):
    """Verify salon callbacks navigate and edit views without spam."""
    from handlers import salon as salon_h
    salon_h.router._parent_router = None

    dp = Dispatcher()
    dp.include_router(salon_h.router)

    media_edits = []

    async def fake_edit_media(self, media, reply_markup=None, **kwargs):
        media_edits.append({"media": media, "markup": reply_markup})
        return self

    monkeypatch.setattr(Message, "edit_media", fake_edit_media)
    monkeypatch.setattr(CallbackQuery, "answer", AsyncMock())

    bot = Bot(token="123456789:ABCdefGHIjklMNOpqrsTUVwxyz123456789")
    user_id = 90005
    await game.ensure_player(user_id=user_id, username="cb_tester", display_name="CB Tester")
    await economy.grant(user_id, credits=5000, kind=ActivityKind.ADMIN)

    tg_user = User(id=user_id, is_bot=False, first_name="CB", username="cb_tester")
    chat = Chat(id=user_id, type="private")
    now = datetime.now(UTC)
    base_msg = Message(message_id=10, date=now, chat=chat, from_user=tg_user, text="dummy")

    # 1. Open Solarium menu
    call_skin = CallbackQuery(
        id="q1",
        from_user=tg_user,
        chat_instance="1",
        data="sal:skin",
        message=base_msg,
    )
    await dp.feed_update(bot, Update(update_id=10, callback_query=call_skin))
    assert len(media_edits) == 1
    assert "سولاریوم" in media_edits[-1]["media"].caption

    # 2. Click paid skin tone -2
    call_set_skin = CallbackQuery(
        id="q2",
        from_user=tg_user,
        chat_instance="1",
        data="sal:set_skin:-2",
        message=base_msg,
    )
    await dp.feed_update(bot, Update(update_id=11, callback_query=call_set_skin))
    st = await salon.get_player_salon_state(user_id)
    assert st["skin_tone_id"] == -2

    # 3. Open Makeup hub
    call_mu = CallbackQuery(
        id="q3",
        from_user=tg_user,
        chat_instance="1",
        data="sal:makeup",
        message=base_msg,
    )
    await dp.feed_update(bot, Update(update_id=12, callback_query=call_mu))
    assert "میکاپ" in media_edits[-1]["media"].caption

    # 4. Open nose sub-menu & choose petite
    call_mu_nose = CallbackQuery(
        id="q4",
        from_user=tg_user,
        chat_instance="1",
        data="sal:mu:nose",
        message=base_msg,
    )
    await dp.feed_update(bot, Update(update_id=13, callback_query=call_mu_nose))
    assert "بینی" in media_edits[-1]["media"].caption

    call_set_nose = CallbackQuery(
        id="q5",
        from_user=tg_user,
        chat_instance="1",
        data="sal:set_nose:nose_petite",
        message=base_msg,
    )
    await dp.feed_update(bot, Update(update_id=14, callback_query=call_set_nose))
    st_nose = await salon.get_player_salon_state(user_id)
    assert st_nose["nose_style"] == "nose_petite"


async def test_salon_real_hair_equipped_and_preserved_in_wardrobe():
    """Verify changing hair in salon puts real Everskies IDs on avatar and wardrobe preserves it."""
    from services import avatar

    user_id = 90006
    await game.ensure_player(
        user_id=user_id, username="barber_avatar", display_name="Barber Avatar"
    )
    async with db.write() as conn:
        await conn.execute("UPDATE players SET gender = 'پسر' WHERE user_id = ?", (user_id,))
    await economy.grant(user_id, credits=5000, kind=ActivityKind.ADMIN)

    # 1. Equip boy hairstyle in salon: hair_eboy (6494)
    res_hair = await salon.change_hair_paid(user_id, "hair_eboy")
    assert res_hair["success"]

    av = await avatar.get_player_avatar(user_id)
    assert 6494 in av["equipped_var_ids"]

    # 2. Player changes clothes in wardrobe (saving only shirt and pants)
    await avatar.save_custom_avatar(
        user_id=user_id,
        var_ids=[33817, 116590],  # only clothing items
        skin_tone=-1,
        is_plus_size=0,
    )

    # 3. Avatar must still have hair 6494 preserved!
    av2 = await avatar.get_player_avatar(user_id)
    assert 6494 in av2["equipped_var_ids"]
    assert 33817 in av2["equipped_var_ids"]
    assert 116590 in av2["equipped_var_ids"]


async def test_salon_changing_hair_does_not_strip_jacket_or_clothing():
    """Verify changing hair NEVER removes cropped sweater 103734 or clothes (no naked bug)."""
    user_id = 90007
    await game.ensure_player(
        user_id=user_id, username="daisy_girl", display_name="Daisy Girl"
    )
    async with db.write() as conn:
        await conn.execute("UPDATE players SET gender = 'دختر' WHERE user_id = ?", (user_id,))
    await economy.grant(user_id, credits=5000, kind=ActivityKind.ADMIN)

    # 1. Player is wearing Set 105 which has cropped sweater 103734 and dress 103733
    await avatar.assign_starter_set(user_id, set_id=105, gender="female")
    av = await avatar.get_player_avatar(user_id)
    assert 103734 in av["equipped_var_ids"]  # Cropped sweater
    assert 103733 in av["equipped_var_ids"]  # Dress
    assert 103736 in av["equipped_var_ids"]  # Knee socks

    # 2. Player changes hairstyle in salon to hair_angel_curls (2161)
    res_hair = await salon.change_hair_paid(user_id, "hair_angel_curls")
    assert res_hair["success"]

    # 3. VERIFY CROPPED SWEATER AND DRESS AND SOCKS ARE 100% PRESERVED!
    av_after = await avatar.get_player_avatar(user_id)
    assert 2161 in av_after["equipped_var_ids"]
    assert 103734 in av_after["equipped_var_ids"], "Cropped sweater was wrongly removed!"
    assert 103733 in av_after["equipped_var_ids"], "Dress was wrongly removed!"
    assert 103736 in av_after["equipped_var_ids"], "Socks were wrongly removed!"

    # 4. Player changes eye style in salon
    res_eye = await salon.change_eyes_paid(user_id, "eyes1_1")
    assert res_eye["success"]
    av_after_eyes = await avatar.get_player_avatar(user_id)
    assert 1702 in av_after_eyes["equipped_var_ids"]
    assert 103734 in av_after_eyes["equipped_var_ids"]
    assert 103736 in av_after_eyes["equipped_var_ids"]


async def test_switching_sets_does_not_merge_or_stack_clothes():
    """Verify equipping a different set cleanly replaces previous clothing with zero stacking."""
    user_id = 90008
    await game.ensure_player(
        user_id=user_id, username="switch_boy", display_name="Switch Boy"
    )
    async with db.write() as conn:
        await conn.execute("UPDATE players SET gender = 'پسر' WHERE user_id = ?", (user_id,))

    # 1. Player owns Set 201 and Set 203 in wardrobe
    set201_vars = avatar.STARTER_SETS[201]["var_ids"]  # has sweater 33817, pants 116590, hair 6494
    set203_vars = avatar.STARTER_SETS[203]["var_ids"]  # has turtleneck 321598, wool pants 321613

    await avatar.add_set_to_wardrobe(
        user_id=user_id,
        set_id=201,
        set_name="Starter Pack (Boys) #1",
        gender="male",
        var_ids=set201_vars,
    )
    await avatar.add_set_to_wardrobe(
        user_id=user_id,
        set_id=203,
        set_name="Starter Pack (Boys) #2",
        gender="male",
        var_ids=set203_vars,
    )

    # 2. Equip Set 201
    await avatar.equip_set(user_id, 201)
    av1 = await avatar.get_player_avatar(user_id)
    assert av1["active_set_id"] == 201
    assert 33817 in av1["equipped_var_ids"]
    assert 116590 in av1["equipped_var_ids"]
    assert 321598 not in av1["equipped_var_ids"]  # no set 203 sweater

    # 3. Equip Set 203
    await avatar.equip_set(user_id, 203)
    av2 = await avatar.get_player_avatar(user_id)
    assert av2["active_set_id"] == 203
    assert 321598 in av2["equipped_var_ids"]  # Set 203 turtleneck equipped
    assert 321613 in av2["equipped_var_ids"]  # Set 203 pants equipped

    # CRITICAL ASSERTION: ZERO ITEMS OF SET 201 REMAIN! NO MERGING!
    assert 33817 not in av2["equipped_var_ids"], "Old sweater from Set 201 merged into Set 203!"
    assert 116590 not in av2["equipped_var_ids"], "Old pants from Set 201 merged into Set 203!"
    assert 28144 not in av2["equipped_var_ids"], "Old boots from Set 201 merged into Set 203!"

    # 4. Check list_wardrobe: only the 2 owned sets exist, NO ghost duplicate set 9999!
    wdr = await avatar.list_wardrobe(user_id)
    set_ids = [s["set_id"] for s in wdr]
    assert 9999 not in set_ids, "Phantom duplicate set 9999 should not exist!"
    assert 203 in set_ids
    assert 201 in set_ids
    # Set 203 is at index 0 because it is currently active
    assert wdr[0]["set_id"] == 203
