import json
import time
import pytest
from aiogram.types import Chat, Message, User
from pydantic import ConfigDict, PrivateAttr

from config import settings
from database.connection import db
from handlers import duels
from models.enums import ActivityKind
from services import economy, game


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


@pytest.fixture(autouse=True)
async def _setup_db():
    await db.connect()
    from database.seed import seed_catalog
    await seed_catalog()
    economy._cooldowns.clear()
    yield
    await db.close()


@pytest.fixture
async def challenger_and_opponent():
    p1 = await game.ensure_player(1001, "Challenger", "p1")
    p2 = await game.ensure_player(1002, "Opponent", "p2")
    async with db.write() as conn:
        await conn.execute("UPDATE wallets SET credits = 5000 WHERE user_id IN (1001, 1002)")
        await conn.execute("UPDATE players SET onboarding_completed = 1 WHERE user_id IN (1001, 1002)")
    economy._cooldowns.clear()
    return p1, p2


@pytest.mark.asyncio
async def test_cmd_duel_creates_challenge_with_buttons(challenger_and_opponent):
    p1, p2 = challenger_and_opponent
    tg1 = User(id=1001, is_bot=False, first_name="Challenger", username="p1")
    tg2 = User(id=1002, is_bot=False, first_name="Opponent", username="p2")

    reply_to = MutableMessage(text="سلام", user=tg2)
    msg = MutableMessage(text="دوئل 200", user=tg1, reply_to_message=reply_to)

    await duels.cmd_duel(msg)

    # Check duel record in DB
    async with db.read() as conn:
        row = await (
            await conn.execute(
                "SELECT * FROM duels WHERE challenger_id = ? AND opponent_id = ? ORDER BY id DESC LIMIT 1",
                (1001, 1002),
            )
        ).fetchone()
        assert row is not None
        assert row["stake"] == 200
        assert row["status"] == "pending"

    # Check challenger balance was escrowed
    bal1, _ = await economy.balances(1001)
    assert bal1 == 4800

    # Check message sent with inline buttons
    assert len(msg.replies) == 1
    reply = msg.replies[0]
    assert "شما تا ۱۰ دقیقه فرصت دارید" in reply["text"]
    kb = reply["reply_markup"].inline_keyboard
    assert len(kb) == 1
    assert "قبول مبارزه" in kb[0][0].text
    assert "رد چالش" in kb[0][1].text


@pytest.mark.asyncio
async def test_duel_decline_refunds_challenger(challenger_and_opponent):
    p1, p2 = challenger_and_opponent
    tg1 = User(id=1001, is_bot=False, first_name="Challenger", username="p1")
    tg2 = User(id=1002, is_bot=False, first_name="Opponent", username="p2")

    reply_to = MutableMessage(text="سلام", user=tg2)
    msg = MutableMessage(text="دوئل 300", user=tg1, reply_to_message=reply_to)
    await duels.cmd_duel(msg)

    async with db.read() as conn:
        row = await (
            await conn.execute("SELECT id FROM duels ORDER BY id DESC LIMIT 1")
        ).fetchone()
        duel_id = row["id"]

    card = MutableMessage(text="چالش", user=tg2)
    stranger = User(id=9999, is_bot=False, first_name="Stranger")
    call_stranger = FakeCall(f"duel:decline:{duel_id}", card, user=stranger)
    await duels.cb_decline(call_stranger)
    assert call_stranger.alerts[0] == "این دوئل متعلق به شما نیست."

    # Opponent declines
    call_opponent = FakeCall(f"duel:decline:{duel_id}", card, user=tg2)
    await duels.cb_decline(call_opponent)

    # Duel is declined and challenger refunded
    async with db.read() as conn:
        row = await (await conn.execute("SELECT status FROM duels WHERE id = ?", (duel_id,))).fetchone()
        assert row["status"] == "declined"

    bal1, _ = await economy.balances(1001)
    assert bal1 == 5000  # fully refunded


@pytest.mark.asyncio
async def test_duel_accept_and_settle(challenger_and_opponent):
    p1, p2 = challenger_and_opponent
    tg1 = User(id=1001, is_bot=False, first_name="Challenger", username="p1")
    tg2 = User(id=1002, is_bot=False, first_name="Opponent", username="p2")

    reply_to = MutableMessage(text="سلام", user=tg2)
    msg = MutableMessage(text="دوئل 400", user=tg1, reply_to_message=reply_to)
    await duels.cmd_duel(msg)

    async with db.read() as conn:
        row = await (
            await conn.execute("SELECT id FROM duels ORDER BY id DESC LIMIT 1")
        ).fetchone()
        duel_id = row["id"]

    card = MutableMessage(text="چالش", user=tg2)
    call_accept = FakeCall(f"duel:accept:{duel_id}", card, user=tg2)
    await duels.cb_accept(call_accept)

    # Duel settled
    async with db.read() as conn:
        row = await (await conn.execute("SELECT status, winner_id, log_json FROM duels WHERE id = ?", (duel_id,))).fetchone()
        assert row["status"] == "resolved"
        assert row["winner_id"] in (1001, 1002)
        log = json.loads(row["log_json"])
        assert log["stake"] == 400

    # Final round reveal
    call_round = FakeCall(f"duel:round:{duel_id}", card, user=tg2)
    await duels.cb_round(call_round)
    assert call_round.answers[0] == "نتیجه نهایی مشخص شد! ⚔️"
