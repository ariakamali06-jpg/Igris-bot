import time
import pytest
from aiogram.types import Chat, Message, User
from pydantic import ConfigDict, PrivateAttr

from database.connection import db
from handlers import admin
from services import economy, game


class MutableMessage(Message):
    model_config = ConfigDict(frozen=False)
    _replies: list = PrivateAttr(default_factory=list)

    @property
    def replies(self) -> list:
        return self._replies

    def __init__(self, text: str = "", user: User | None = None, reply_to_message=None) -> None:
        super().__init__(
            message_id=500,
            date=time.time(),
            chat=Chat(id=-1001234567, type="supergroup", title="تیرامیکس سیتی"),
            from_user=user or User(id=5765828495, is_bot=False, first_name="Rex Lapis", username="rexlapis"),
            text=text,
            reply_to_message=reply_to_message,
        )
        self._replies = []

    async def reply(self, text: str, reply_markup=None, **kw):
        self._replies.append({"text": text, "reply_markup": reply_markup})
        return self


@pytest.fixture(autouse=True)
async def _setup_db():
    await db.connect()
    from database.seed import seed_catalog
    await seed_catalog()
    yield
    await db.close()


@pytest.mark.asyncio
async def test_non_admin_cannot_use_cheat():
    normal_user = User(id=8888, is_bot=False, first_name="Normal", username="normal")
    msg = MutableMessage(text="چیت لول 50", user=normal_user)
    await admin.cmd_cheat(msg)
    assert len(msg.replies) == 0  # ignored


@pytest.mark.asyncio
async def test_creator_cheat_self():
    owner = User(id=5765828495, is_bot=False, first_name="Rex Lapis", username="rexlapis")
    await game.ensure_player(5765828495, "Rex Lapis", "rexlapis")

    # 1. Change level
    msg_lvl = MutableMessage(text="چیت لول 15", user=owner)
    await admin.cmd_cheat(msg_lvl)
    assert len(msg_lvl.replies) == 1
    assert "15" in msg_lvl.replies[0]["text"]
    assert "لول" in msg_lvl.replies[0]["text"]

    p = await game.load_player(5765828495)
    assert p.level == 15

    # 2. Change education
    msg_edu = MutableMessage(text="چیت سواد 4", user=owner)
    await admin.cmd_cheat(msg_edu)
    assert "فوق‌لیسانس" in msg_edu.replies[0]["text"]
    p = await game.load_player(5765828495)
    assert p.education_level == 4

    # 3. Change money
    msg_money = MutableMessage(text="چیت پول 75000", user=owner)
    await admin.cmd_cheat(msg_money)
    bal, _ = await economy.balances(5765828495)
    assert bal == 75000


@pytest.mark.asyncio
async def test_creator_cheat_on_others_via_reply():
    owner = User(id=5765828495, is_bot=False, first_name="Rex Lapis", username="rexlapis")
    target_user = User(id=9999, is_bot=False, first_name="Target", username="target")
    await game.ensure_player(9999, "Target", "target")

    reply_to = MutableMessage(text="من یک کاربر عادی هستم", user=target_user)
    msg_cheat = MutableMessage(text="چیت لول 42", user=owner, reply_to_message=reply_to)

    await admin.cmd_cheat(msg_cheat)
    assert len(msg_cheat.replies) == 1
    assert "9999" in msg_cheat.replies[0]["text"]
    assert "42" in msg_cheat.replies[0]["text"]
    assert "لول" in msg_cheat.replies[0]["text"]

    p_target = await game.load_player(9999)
    assert p_target.level == 42


@pytest.mark.asyncio
async def test_creator_cheat_god_mode():
    owner = User(id=5765828495, is_bot=False, first_name="Rex Lapis", username="rexlapis")
    target_user = User(id=7777, is_bot=False, first_name="Noob", username="noob")
    await game.ensure_player(7777, "Noob", "noob")

    reply_to = MutableMessage(text="سلام", user=target_user)
    msg_max = MutableMessage(text="چیت مکس", user=owner, reply_to_message=reply_to)
    await admin.cmd_cheat(msg_max)

    assert "حالت خدا" in msg_max.replies[0]["text"]
    p_god = await game.load_player(7777)
    assert p_god.level == 50
    assert p_god.education_level == 5
    assert p_god.base_atk == 100
    assert p_god.base_def == 100
    assert p_god.energy == 200

    bal, _ = await economy.balances(7777)
    assert bal == 1000000


@pytest.mark.asyncio
async def test_creator_cheat_help():
    owner = User(id=5765828495, is_bot=False, first_name="Rex Lapis", username="rexlapis")
    msg_help = MutableMessage(text="چیت", user=owner)
    await admin.cmd_cheat(msg_help)

    assert len(msg_help.replies) == 1
    assert "پنل کدهای تقلب سازنده" in msg_help.replies[0]["text"]
