"""Tests for Tiramix Social interactions: Marriage, Divorce with Judge, Theft, Intimacy, Clans."""

import pytest
import time
from aiogram.types import Chat, Message, User
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.storage.base import StorageKey
from pydantic import ConfigDict, PrivateAttr

from database.connection import db
from database.seed import seed_catalog
from handlers import social
from models.enums import ActivityKind
from models.player import Player
from services import economy, game, tiramix


class MutableMessage(Message):
    model_config = ConfigDict(frozen=False)
    _replies: list = PrivateAttr(default_factory=list)

    @property
    def replies(self) -> list:
        return self._replies

    def __init__(self, text: str = "", user: User | None = None, reply_to_message=None) -> None:
        super().__init__(
            message_id=202,
            date=time.time(),
            chat=Chat(id=-1001234567, type="supergroup", title="تیرامیکس سیتی"),
            from_user=user or User(id=7001, is_bot=False, first_name="Player1", username="p1"),
            text=text,
            reply_to_message=reply_to_message,
        )
        self._replies = []

    async def reply(self, text: str, reply_markup=None, **kw):
        self._replies.append({"text": text, "reply_markup": reply_markup})
        return self

    async def answer(self, text: str, reply_markup=None, **kw):
        self._replies.append({"text": text, "reply_markup": reply_markup})
        return self

    async def edit_text(self, text: str, reply_markup=None, **kw):
        self._replies.append({"text": text, "reply_markup": reply_markup})
        return self


class FakeCall:
    def __init__(self, data: str, message: Message, user: User) -> None:
        self.data = data
        self.message = message
        self.from_user = user
        self.alerts: list[str] = []
        self.answers: list[str] = []

    async def answer(self, text: str = "", show_alert: bool = False, **kw) -> None:
        self.answers.append(text)
        if show_alert:
            self.alerts.append(text)


@pytest.fixture(scope="session")
async def _db():
    await db.connect()
    await seed_catalog()
    yield db
    await db.close()


@pytest.mark.asyncio
async def test_theft_mechanic(_db) -> None:
    u1 = User(id=7001, is_bot=False, first_name="Thief", username="thief")
    u2 = User(id=7002, is_bot=False, first_name="Victim", username="victim")

    async with db.write() as conn:
        await conn.execute("DELETE FROM clan_members WHERE user_id IN (7001, 7002)")
        await conn.execute("DELETE FROM inventory WHERE user_id IN (7001, 7002)")
        await conn.execute("DELETE FROM loadout WHERE user_id IN (7001, 7002)")
        await conn.execute("DELETE FROM wallets WHERE user_id IN (7001, 7002)")
        await conn.execute("DELETE FROM players WHERE user_id IN (7001, 7002)")

    await game.ensure_player(7001, "Thief", "thief")
    await game.ensure_player(7002, "Victim", "victim")
    await economy.grant(7002, credits=5000, kind=ActivityKind.ADMIN)

    victim_msg = MutableMessage(text="سلام به همه", user=u2)
    thief_msg = MutableMessage(text="دزدی", user=u1, reply_to_message=victim_msg)

    await social.cmd_steal(thief_msg)
    assert len(thief_msg.replies) == 1
    reply_text = thief_msg.replies[0]["text"]
    assert "سرقت" in reply_text or "حبس" in reply_text or "جریمه" in reply_text


@pytest.mark.asyncio
async def test_marriage_and_intimacy(_db) -> None:
    u1 = User(id=7003, is_bot=False, first_name="Romeo", username="romeo")
    u2 = User(id=7004, is_bot=False, first_name="Juliet", username="juliet")

    async with db.write() as conn:
        await conn.execute("DELETE FROM clan_members WHERE user_id IN (7003, 7004)")
        await conn.execute("DELETE FROM inventory WHERE user_id IN (7003, 7004)")
        await conn.execute("DELETE FROM loadout WHERE user_id IN (7003, 7004)")
        await conn.execute("DELETE FROM wallets WHERE user_id IN (7003, 7004)")
        await conn.execute("DELETE FROM players WHERE user_id IN (7003, 7004)")

    await game.ensure_player(7003, "Romeo", "romeo")
    await game.ensure_player(7004, "Juliet", "juliet")
    async with db.write() as conn:
        await conn.execute("UPDATE players SET level = 3 WHERE user_id IN (7003, 7004)")

    # 1. Propose marriage
    target_msg = MutableMessage(text="سلام عشقم", user=u2)
    propose_msg = MutableMessage(text="ازدواج", user=u1, reply_to_message=target_msg)
    await social.cmd_marry(propose_msg)
    assert len(propose_msg.replies) == 1
    assert "خواستگاری" in propose_msg.replies[0]["text"]

    # 2. Juliet accepts marriage
    accept_call = FakeCall(data=f"marry:yes:{u1.id}", message=propose_msg, user=u2)
    await social.cb_marry(accept_call)

    p1 = await game.load_player(7003)
    p2 = await game.load_player(7004)
    assert p1.spouse_id == 7004
    assert p2.spouse_id == 7003

    # 3. Intimacy
    intimacy_msg = MutableMessage(text="رابطه", user=u1, reply_to_message=target_msg)
    await social.cmd_intimacy(intimacy_msg)
    assert len(intimacy_msg.replies) == 1
    assert "خانواده" in intimacy_msg.replies[0]["text"] or "بارداری" in intimacy_msg.replies[0]["text"]

    # 4. Divorce request: Juliet requests divorce
    divorce_msg = MutableMessage(text="طلاق", user=u2, reply_to_message=target_msg)
    await social.cmd_divorce(divorce_msg)
    assert len(divorce_msg.replies) == 1
    assert "طلاق" in divorce_msg.replies[0]["text"]

    # 5. Romeo rejects divorce -> Tiramix Family Court triggers with random Judge!
    reject_call = FakeCall(data=f"divorce:court:{u2.id}", message=divorce_msg, user=u1)
    await social.cb_divorce_response(reject_call)
    assert len(divorce_msg.replies) == 2
    court_text = divorce_msg.replies[1]["text"]
    assert "دادگاه" in court_text
    assert "قاضی" in court_text

    # 6. Judge confirms divorce
    judge_user = User(id=7777, is_bot=False, first_name="JudgeAli", username="judgeali")
    judge_call = FakeCall(data=f"judge:divorce:{u1.id}:{u2.id}", message=divorce_msg, user=judge_user)
    await social.cb_judge_verdict(judge_call)
    
    p1_divorced = await game.load_player(7003)
    p2_divorced = await game.load_player(7004)
    assert p1_divorced.spouse_id is None
    assert p2_divorced.spouse_id is None


@pytest.mark.asyncio
async def test_clan_creation(_db) -> None:
    u = User(id=7005, is_bot=False, first_name="ClanLeader", username="leader")
    async with db.write() as conn:
        await conn.execute("DELETE FROM clan_members WHERE user_id = 7005")
        await conn.execute("DELETE FROM clans WHERE leader_id = 7005")
        await conn.execute("DELETE FROM inventory WHERE user_id = 7005")
        await conn.execute("DELETE FROM loadout WHERE user_id = 7005")
        await conn.execute("DELETE FROM wallets WHERE user_id = 7005")
        await conn.execute("DELETE FROM players WHERE user_id = 7005")

    await game.ensure_player(7005, "ClanLeader", "leader")
    # Grant 25,000 credits to afford clan creation
    await economy.grant(7005, credits=25000, kind=ActivityKind.ADMIN)

    msg = MutableMessage(text="ساخت کلن عقاب‌های سرخ", user=u)
    await social.cmd_clan_create(msg)
    assert len(msg.replies) == 1
    assert "عقاب‌های سرخ" in msg.replies[0]["text"]

    p = await game.load_player(7005)
    assert p.clan_id is not None
    assert p.clan_role == "leader"
