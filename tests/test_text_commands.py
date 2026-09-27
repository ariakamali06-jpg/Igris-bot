from __future__ import annotations

from datetime import datetime, timezone
import pytest
from aiogram import Bot, Dispatcher
from aiogram.types import Chat, Message, Update, User

from database.connection import db
from handlers import register_routers

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
async def _setup_db():
    await db.connect()
    from database.seed import seed_catalog
    await seed_catalog()
    yield
    await db.close()


async def test_persian_plain_text_triggers(monkeypatch):
    dp = Dispatcher()
    register_routers(dp)

    replies = []

    async def fake_reply(self, text, **kwargs):
        replies.append(text)
        return self

    async def fake_reply_photo(self, photo, caption=None, **kwargs):
        replies.append(caption or "photo")
        return self

    monkeypatch.setattr(Message, "reply", fake_reply)
    monkeypatch.setattr(Message, "reply_photo", fake_reply_photo)

    bot = Bot(token="987654321:AAFakeTokenForLocalTesting123456789")

    chat = Chat(id=123, type="private")
    user = User(id=123, is_bot=False, first_name="Aria", username="aria")
    now = datetime.now(timezone.utc)

    # 1. Test "پروفایل" (Profile)
    msg1 = Message(message_id=1, date=now, chat=chat, from_user=user, text="پروفایل")
    await dp.feed_update(bot=bot, update=Update(update_id=1, message=msg1))
    assert len(replies) == 1
    assert "لول" in replies[0]

    # 2. Test "شاپ" (Shop)
    msg2 = Message(message_id=2, date=now, chat=chat, from_user=user, text="شاپ")
    await dp.feed_update(bot=bot, update=Update(update_id=2, message=msg2))
    assert len(replies) == 2
    assert "بوتیک خیابانی" in replies[1]

    # 3. Test "کار" (Work)
    msg3 = Message(message_id=3, date=now, chat=chat, from_user=user, text="کار")
    await dp.feed_update(bot=bot, update=Update(update_id=3, message=msg3))
    assert len(replies) == 3

    # 4. Test "راهنما" (Help)
    msg4 = Message(message_id=4, date=now, chat=chat, from_user=user, text="راهنما")
    await dp.feed_update(bot=bot, update=Update(update_id=4, message=msg4))
    assert len(replies) == 4
    assert "Urban Fantasy RPG" in replies[3]
