"""دوز — two-player tic-tac-toe: challenge, accept, board moves, rake (temp DB).

Drives the real handlers with MutableMessage / FakeCall doubles (same pattern
as ``tests/test_duels.py``) so the full reply → accept → button → settle flow
is exercised, including escrow, rake and refund ledger rows.
Player ids: 662_000+.
"""

from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path

import pytest_asyncio
from aiogram.types import Chat, Message, User
from pydantic import ConfigDict, PrivateAttr

# Point the DB at a scratch file before config/settings are first used.
os.environ.setdefault(
    "DB_PATH", str(Path(tempfile.mkdtemp(prefix="tgbot-pytest-")) / "test.db")
)

from config import settings  # noqa: E402
from database.connection import db  # noqa: E402
from database.seed import seed_catalog  # noqa: E402
from handlers import xo  # noqa: E402
from models.enums import ActivityKind  # noqa: E402
from services import economy  # noqa: E402
from services.game import ensure_player  # noqa: E402

BASE_ID = 662_000
STAKE = 200
START_CREDITS = 5_000


@pytest_asyncio.fixture(autouse=True)
async def _database() -> None:
    if db._closed:  # noqa: SLF001
        await db.connect()
        await seed_catalog()
    economy.clear_cooldowns()
    yield
    await db.close()


async def _funded(offset: int, amount: int = START_CREDITS):
    """Player topped up to exactly ``amount`` credits (assertions stay exact)."""
    player = await ensure_player(BASE_ID + offset, f"XO {offset}", None)
    credits, _ = await economy.balances(player.user_id)
    if credits < amount:
        await economy.grant(
            player.user_id, credits=amount - credits, kind=ActivityKind.SYSTEM
        )
    elif credits > amount:
        await economy.spend(
            player.user_id, credits=credits - amount, kind=ActivityKind.SYSTEM
        )
    return player


async def _balance(user_id: int) -> int:
    credits, _ = await economy.balances(user_id)
    return credits


async def _game(game_id: int) -> dict:
    row = await db.fetchone("SELECT * FROM xo_games WHERE id = ?", (game_id,))
    assert row is not None
    return dict(row)


async def _game_id_for(challenger_id: int) -> int:
    row = await db.fetchone(
        "SELECT id FROM xo_games WHERE challenger_id = ? ORDER BY id DESC LIMIT 1",
        (challenger_id,),
    )
    assert row is not None
    return int(row["id"])


class MutableMessage(Message):
    model_config = ConfigDict(frozen=False)
    _replies: list = PrivateAttr(default_factory=list)

    def __init__(self, text: str = "", user: User | None = None, reply_to_message=None) -> None:
        super().__init__(
            message_id=6621,
            date=time.time(),
            chat=Chat(id=-100660003, type="supergroup", title="تیرامیکس"),
            from_user=user,
            text=text,
            reply_to_message=reply_to_message,
        )
        self._replies = []

    @property
    def replies(self) -> list:
        return self._replies

    async def reply(self, text: str, reply_markup=None, **kw):  # noqa: ANN001, ARG002
        self._replies.append({"text": text, "reply_markup": reply_markup})
        return self

    async def answer(self, text: str, reply_markup=None, **kw):  # noqa: ANN001, ARG002
        self._replies.append({"text": text, "reply_markup": reply_markup})
        return self

    async def edit_text(self, text: str, reply_markup=None, **kw):  # noqa: ANN001, ARG002
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


def _user(player) -> User:  # noqa: ANN001
    return User(
        id=player.user_id, is_bot=False, first_name=player.display_name, username=None
    )


async def _challenge(challenger, opponent_user: User, stake: int = STAKE):  # noqa: ANN001
    reply_to = MutableMessage(text="بیا", user=opponent_user)
    msg = MutableMessage(
        text=f"دوز {stake}", user=_user(challenger), reply_to_message=reply_to
    )
    await xo.cmd_xo(msg)
    return msg


# ---------------------------------------------------------------------------
# Challenge
# ---------------------------------------------------------------------------


async def test_challenge_escrows_stake_and_offers_buttons() -> None:
    p1 = await _funded(1)
    p2 = await _funded(2)
    start = await _balance(p1.user_id)

    msg = await _challenge(p1, _user(p2))
    assert await _balance(p1.user_id) == start - STAKE

    game = await _game(await _game_id_for(p1.user_id))
    assert game["status"] == "pending"
    assert game["stake"] == STAKE
    assert game["challenger_id"] == p1.user_id
    assert game["opponent_id"] == p2.user_id

    reply = msg.replies[0]
    keyboard = reply["reply_markup"].inline_keyboard
    assert "قبول دوز" in keyboard[0][0].text
    assert "رد چالش" in keyboard[0][1].text
    assert keyboard[0][0].callback_data == f"xo:accept:{game['id']}"


async def test_challenge_usage_errors() -> None:
    p1 = await _funded(3)
    user = _user(p1)

    # No argument at all → usage text.
    bad = MutableMessage(text="دوز", user=user)
    await xo.cmd_xo(bad)
    assert "دوز [مبلغ شرط]" in bad.replies[0]["text"]

    # Stake outside the config band → refused.
    huge = MutableMessage(text=f"دوز {settings.xo_max_bet + 1}", user=user)
    await xo.cmd_xo(huge)
    assert "شرط باید بین" in huge.replies[0]["text"]

    # Valid stake but no reply target → pointing message, nothing escrowed.
    start = await _balance(p1.user_id)
    orphan = MutableMessage(text=f"دوز {STAKE}", user=user)
    await xo.cmd_xo(orphan)
    assert "روی پیام حریف ریپلای بزن" in orphan.replies[0]["text"]
    assert await _balance(p1.user_id) == start

    # Self-challenge is refused.
    self_msg = MutableMessage(
        text=f"دوز {STAKE}", user=user, reply_to_message=MutableMessage(text="x", user=user)
    )
    await xo.cmd_xo(self_msg)
    assert "آینه‌باز" in self_msg.replies[0]["text"]


async def test_decline_refunds_and_strangers_are_blocked() -> None:
    p1 = await _funded(5)
    p2 = await _funded(6)
    start = await _balance(p1.user_id)
    await _challenge(p1, _user(p2))
    game_id = await _game_id_for(p1.user_id)

    card = MutableMessage(text="چالش", user=_user(p2))
    stranger = User(id=BASE_ID + 99, is_bot=False, first_name="Nobody")
    blocked = FakeCall(f"xo:decline:{game_id}", card, stranger)
    await xo.cb_decline(blocked)
    assert blocked.alerts[0] == "این دوز مال تو نیست."
    assert await _balance(p1.user_id) == start - STAKE  # untouched

    decline = FakeCall(f"xo:decline:{game_id}", card, _user(p2))
    await xo.cb_decline(decline)
    assert await _balance(p1.user_id) == start  # fully refunded
    game = await _game(game_id)
    assert game["status"] == "declined"


async def test_challenge_cooldown_blocks_second_table() -> None:
    p1 = await _funded(7)
    p2 = await _funded(8)
    p3 = await _funded(9)
    await _challenge(p1, _user(p2))

    again = await _challenge(p1, _user(p3))
    assert "cools down" in again.replies[-1]["text"]
    rows = await db.fetchall(
        "SELECT id FROM xo_games WHERE challenger_id = ?", (p1.user_id,)
    )
    assert len(rows) == 1  # the blocked attempt never escrowed anything


# ---------------------------------------------------------------------------
# Accept / board
# ---------------------------------------------------------------------------


async def test_only_opponent_can_accept_and_board_opens() -> None:
    p1 = await _funded(10)
    p2 = await _funded(11)
    await _challenge(p1, _user(p2))
    game_id = await _game_id_for(p1.user_id)
    card = MutableMessage(text="میز", user=_user(p2))

    stranger = User(id=BASE_ID + 98, is_bot=False, first_name="Nobody")
    blocked = FakeCall(f"xo:accept:{game_id}", card, stranger)
    await xo.cb_accept(blocked)
    assert blocked.alerts, "stranger must be rejected"
    assert (await _game(game_id))["status"] == "pending"

    accept = FakeCall(f"xo:accept:{game_id}", card, _user(p2))
    await xo.cb_accept(accept)
    game = await _game(game_id)
    assert game["status"] == "active"
    assert game["turn_id"] == p1.user_id  # challenger opens as ❌

    board_reply = card.replies[-1]
    assert "نوبت" in board_reply["text"]
    keyboard = board_reply["reply_markup"].inline_keyboard
    assert len(keyboard) == 3 and all(len(row) == 3 for row in keyboard)
    assert keyboard[0][0].callback_data == f"xo:move:{game_id}:0"


async def test_wrong_turn_occupied_and_stranger_moves_alert() -> None:
    p1 = await _funded(12)
    p2 = await _funded(13)
    await _challenge(p1, _user(p2))
    game_id = await _game_id_for(p1.user_id)
    card = MutableMessage(text="میز", user=_user(p2))
    await xo.cb_accept(FakeCall(f"xo:accept:{game_id}", card, _user(p2)))

    stranger = User(id=BASE_ID + 97, is_bot=False, first_name="Nobody")
    stray = FakeCall(f"xo:move:{game_id}:0", card, stranger)
    await xo.cb_move(stray)
    assert "تو بازیکن این دوز نیستی" in stray.alerts[0]

    early = FakeCall(f"xo:move:{game_id}:4", card, _user(p2))
    await xo.cb_move(early)
    assert early.alerts[0] == "نوبت تو نیست! ⏳"

    first = FakeCall(f"xo:move:{game_id}:0", card, _user(p1))
    await xo.cb_move(first)
    assert not first.alerts  # accepted silently, board re-rendered

    taken = FakeCall(f"xo:move:{game_id}:0", card, _user(p2))
    await xo.cb_move(taken)
    assert "پرته" in taken.alerts[0]

    valid = FakeCall(f"xo:move:{game_id}:4", card, _user(p2))
    await xo.cb_move(valid)
    game = await _game(game_id)
    assert game["board"] == "X...O...."  # X@0 from the turn above, O@4
    assert game["turn_id"] == p1.user_id


# ---------------------------------------------------------------------------
# Settlements
# ---------------------------------------------------------------------------


async def test_x_win_takes_pot_minus_rake() -> None:
    p1 = await _funded(14)
    p2 = await _funded(15)
    x_start = await _balance(p1.user_id)
    o_start = await _balance(p2.user_id)
    await _challenge(p1, _user(p2))
    game_id = await _game_id_for(p1.user_id)
    card = MutableMessage(text="میز", user=_user(p2))
    await xo.cb_accept(FakeCall(f"xo:accept:{game_id}", card, _user(p2)))

    # X takes the top row: 0, 1, 2 with O on 3, 4.
    for idx, player in ((0, p1), (3, p2), (1, p1), (4, p2), (2, p1)):
        call = FakeCall(f"xo:move:{game_id}:{idx}", card, _user(player))
        await xo.cb_move(call)
        assert not call.alerts

    game = await _game(game_id)
    assert game["status"] == "resolved"
    assert game["winner_id"] == p1.user_id

    rake = int(STAKE * 2 * settings.xo_house_rake)
    assert await _balance(p1.user_id) == x_start + STAKE - rake
    assert await _balance(p2.user_id) == o_start - STAKE

    final = card.replies[-1]
    assert "🏆" in final["text"]
    assert f"{settings.xo_house_rake:.0%}" in final["text"]


async def test_draw_refunds_both_stakes() -> None:
    p1 = await _funded(16)
    p2 = await _funded(17)
    x_start = await _balance(p1.user_id)
    o_start = await _balance(p2.user_id)
    await _challenge(p1, _user(p2))
    game_id = await _game_id_for(p1.user_id)
    card = MutableMessage(text="میز", user=_user(p2))
    await xo.cb_accept(FakeCall(f"xo:accept:{game_id}", card, _user(p2)))

    sequence = [(0, p1), (1, p2), (2, p1), (4, p2), (3, p1), (5, p2), (7, p1), (6, p2), (8, p1)]
    for idx, player in sequence:
        call = FakeCall(f"xo:move:{game_id}:{idx}", card, _user(player))
        await xo.cb_move(call)
        assert not call.alerts

    game = await _game(game_id)
    assert game["status"] == "resolved"
    assert game["winner_id"] is None
    assert game["board"] == "XOXXOOOXX"  # full board, nobody connected three
    assert await _balance(p1.user_id) == x_start
    assert await _balance(p2.user_id) == o_start
    assert "🤝" in card.replies[-1]["text"]


async def test_insufficient_funds_on_accept_voids_challenge() -> None:
    p1 = await _funded(18)
    p2 = await _funded(19, amount=START_CREDITS)
    x_start = await _balance(p1.user_id)
    await _challenge(p1, _user(p2))
    game_id = await _game_id_for(p1.user_id)

    # The opponent gambles their stack away between challenge and accept.
    credits, _ = await economy.balances(p2.user_id)
    await economy.spend(p2.user_id, credits=credits, kind=ActivityKind.SYSTEM)

    card = MutableMessage(text="میز", user=_user(p2))
    accept = FakeCall(f"xo:accept:{game_id}", card, _user(p2))
    await xo.cb_accept(accept)

    game = await _game(game_id)
    assert game["status"] == "declined"  # challenge voided cleanly
    assert await _balance(p1.user_id) == x_start  # escrow returned
    assert card.replies, "the broke opponent must see an error"
