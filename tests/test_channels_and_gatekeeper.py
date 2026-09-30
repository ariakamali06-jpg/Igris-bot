from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, Chat, Message, User

from config import settings
from database.connection import db
from database.seed import seed_catalog
from handlers.gatekeeper import OnboardingGateMiddleware
from handlers import onboarding
from services import channels, game


@pytest.fixture(autouse=True)
async def _setup_db():
    await db.connect()
    await seed_catalog()
    yield
    await db.close()


@pytest.mark.asyncio
async def test_channels_markup_structure():
    test_channels = [
        {"name": "کار و وار", "url": "https://t.me/kar_O_war", "username": "@kar_O_war"},
        {"name": "API CONFIGG", "url": "https://t.me/APICONFIGG", "username": "@APICONFIGG"},
    ]
    markup = channels.channel_join_markup(test_channels)
    # Rows: 2 channels + 1 verification button
    assert len(markup.inline_keyboard) == 3
    assert markup.inline_keyboard[0][0].url == "https://t.me/kar_O_war"
    assert markup.inline_keyboard[1][0].url == "https://t.me/APICONFIGG"
    assert markup.inline_keyboard[2][0].callback_data == "ob:check_channels"


@pytest.mark.asyncio
async def test_get_missing_channels_flow():
    # 1. When bot is None -> returns empty
    assert await channels.get_missing_channels(None, 12345) == []

    # 2. Mock Bot where user is in only 1 of 3 channels
    mock_bot = MagicMock()
    
    async def mock_get_chat_member(chat_id, user_id):
        m = MagicMock()
        if chat_id == "@kar_O_war":
            m.status = "member"
        else:
            m.status = "left"
        return m

    mock_bot.get_chat_member = AsyncMock(side_effect=mock_get_chat_member)

    missing = await channels.get_missing_channels(mock_bot, 12345)
    assert len(missing) == 2
    missing_usernames = [c["username"] for c in missing]
    assert "@kar_O_war" not in missing_usernames
    assert "@APICONFIGG" in missing_usernames
    assert "@formula_one_farsi" in missing_usernames

    # 3. User joined all channels -> returns empty list
    async def mock_get_chat_member_all(chat_id, user_id):
        m = MagicMock()
        m.status = "member"
        return m

    mock_bot.get_chat_member = AsyncMock(side_effect=mock_get_chat_member_all)
    missing_all = await channels.get_missing_channels(mock_bot, 12345)
    assert len(missing_all) == 0


@pytest.mark.asyncio
async def test_gatekeeper_blocks_unonboarded_group_commands(monkeypatch):
    middleware = OnboardingGateMiddleware()
    user_id = 777001
    user = User(id=user_id, is_bot=False, first_name="NewUser", username="newuser")
    group_chat = Chat(id=-1001234567, type="supergroup", title="Test Group")

    # Ensure player has onboarding_completed = 0
    await game.ensure_player(user_id, "NewUser", "newuser")
    async with db.write() as conn:
        await conn.execute("UPDATE players SET onboarding_completed = 0 WHERE user_id = ?", (user_id,))

    replies = []

    async def mock_reply(self, text, reply_markup=None, **kwargs):
        replies.append({"text": text, "reply_markup": reply_markup})
        return self

    monkeypatch.setattr(Message, "reply", mock_reply)

    msg = Message(
        message_id=50,
        date=datetime.now(timezone.utc),
        chat=group_chat,
        from_user=user,
        text="پروفایل",
    )

    handler_called = False

    async def dummy_handler(event, data):
        nonlocal handler_called
        handler_called = True
        return "OK"

    mock_bot = MagicMock()
    mock_me = MagicMock()
    mock_me.username = "IgrisLifeBot"
    mock_bot.get_me = AsyncMock(return_value=mock_me)

    data = {"bot": mock_bot}
    result = await middleware(dummy_handler, msg, data)

    # Handler must NOT have been called
    assert handler_called is False
    assert result is None
    # Reply prompt was sent with link to PV
    assert len(replies) == 1
    assert "شناسنامه و کاراکتر نساخته‌اید" in replies[0]["text"]
    assert replies[0]["reply_markup"] is not None
    button = replies[0]["reply_markup"].inline_keyboard[0][0]
    assert "start=create" in button.url
    assert "IgrisLifeBot" in button.url


@pytest.mark.asyncio
async def test_gatekeeper_allows_onboarded_group_commands():
    middleware = OnboardingGateMiddleware()
    user_id = 777002
    user = User(id=user_id, is_bot=False, first_name="ProPlayer", username="proplayer")
    group_chat = Chat(id=-1001234567, type="supergroup", title="Test Group")

    # Set player onboarding_completed = 1
    await game.ensure_player(user_id, "ProPlayer", "proplayer")
    async with db.write() as conn:
        await conn.execute("UPDATE players SET onboarding_completed = 1 WHERE user_id = ?", (user_id,))

    msg = Message(
        message_id=51,
        date=datetime.now(timezone.utc),
        chat=group_chat,
        from_user=user,
        text="کار",
    )

    handler_called = False

    async def dummy_handler(event, data):
        nonlocal handler_called
        handler_called = True
        return "SUCCESS"

    result = await middleware(dummy_handler, msg, {})

    assert handler_called is True
    assert result == "SUCCESS"


@pytest.mark.asyncio
async def test_gatekeeper_blocks_unonboarded_group_callback():
    middleware = OnboardingGateMiddleware()
    user_id = 777003
    user = User(id=user_id, is_bot=False, first_name="Guest", username="guest")
    group_chat = Chat(id=-1001234567, type="group", title="Test Group")

    await game.ensure_player(user_id, "Guest", "guest")
    async with db.write() as conn:
        await conn.execute("UPDATE players SET onboarding_completed = 0 WHERE user_id = ?", (user_id,))

    parent_msg = Message(
        message_id=52,
        date=datetime.now(timezone.utc),
        chat=group_chat,
        from_user=user,
        text="Some panel",
    )

    call = MagicMock(spec=CallbackQuery)
    call.message = parent_msg
    call.from_user = user
    call.answer = AsyncMock()

    handler_called = False

    async def dummy_handler(event, data):
        nonlocal handler_called
        handler_called = True
        return "OK"

    result = await middleware(dummy_handler, call, {})
    assert handler_called is False
    assert result is None
    call.answer.assert_called_once()
    assert "کاراکتر نساخته‌اید" in call.answer.call_args[0][0]


@pytest.mark.asyncio
async def test_onboarding_channel_verification_flow(monkeypatch):
    user_id = 777004
    user = User(id=user_id, is_bot=False, first_name="ChannelTester", username="tester")
    storage = MemoryStorage()
    state = FSMContext(storage=storage, key=StorageKey(bot_id=1, chat_id=user_id, user_id=user_id))

    await game.ensure_player(user_id, "ChannelTester", "tester")
    async with db.write() as conn:
        await conn.execute("UPDATE players SET onboarding_completed = 0 WHERE user_id = ?", (user_id,))

    # Mock get_missing_channels to return 1 missing channel
    missing_mock = [
        {"name": "کار و وار (ربات ایگریس)", "url": "https://t.me/kar_O_war", "username": "@kar_O_war"}
    ]
    monkeypatch.setattr(onboarding, "get_missing_channels", AsyncMock(return_value=missing_mock))

    replies = []

    async def mock_reply(self, text, reply_markup=None, **kwargs):
        replies.append({"text": text, "reply_markup": reply_markup})
        return self

    monkeypatch.setattr(Message, "reply", mock_reply)

    msg = Message(
        message_id=60,
        date=datetime.now(timezone.utc),
        chat=Chat(id=user_id, type="private"),
        from_user=user,
        text="/start",
    )

    await onboarding.cmd_start(msg, state)

    # Must reply with channel join buttons because 1 channel is missing
    assert len(replies) == 1
    assert "عضو شوید" in replies[0]["text"]
    assert replies[0]["reply_markup"] is not None
    # 1 channel + 1 verification button = 2 rows
    assert len(replies[0]["reply_markup"].inline_keyboard) == 2
    assert await state.get_state() is None

    # Now simulate user tapping "ob:check_channels" after joining all channels:
    monkeypatch.setattr(onboarding, "get_missing_channels", AsyncMock(return_value=[]))
    call = MagicMock(spec=CallbackQuery)
    call.from_user = user
    call.bot = MagicMock()
    call.message = msg
    call.data = "ob:check_channels"
    call.answer = AsyncMock()

    await onboarding.cb_check_channels(call, state)

    # Verification successful -> transitioned to waiting_for_name!
    assert await state.get_state() == onboarding.OnboardingState.waiting_for_name.state
    call.answer.assert_called_once()
    assert "تأیید شد" in call.answer.call_args[0][0]

