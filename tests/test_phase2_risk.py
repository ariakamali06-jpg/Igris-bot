"""ریسک — the crash round: escrow, deterministic multiplier, cash out (temp DB).

Time is controlled two ways: the row's ``started_at`` (moved directly) and a
monkeypatched ``risk_game.now`` so payouts are byte-exact. Player ids: 661_000+.
"""

from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path

import pytest
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
from handlers import risk as risk_handler  # noqa: E402
from models.enums import ActivityKind  # noqa: E402
from services import economy, risk_game  # noqa: E402
from services.game import GameError, InsufficientFunds, ensure_player  # noqa: E402

BASE_ID = 661_000
FIXED_NOW = 1_700_000_000


@pytest_asyncio.fixture(autouse=True)
async def _database() -> None:
    if db._closed:  # noqa: SLF001
        await db.connect()
        await seed_catalog()
    economy.clear_cooldowns()
    yield
    await db.close()


async def _player(offset: int, credits: int = 0):
    player = await ensure_player(BASE_ID + offset, f"Risk {offset}", None)
    if credits:
        await economy.grant(
            player.user_id, credits=credits, kind=ActivityKind.SYSTEM
        )
    return player


async def _balance(user_id: int) -> int:
    credits, _ = await economy.balances(user_id)
    return credits


async def _round_row(user_id: int) -> dict | None:
    row = await db.fetchone(
        "SELECT * FROM risk_rounds WHERE user_id = ? ORDER BY id DESC LIMIT 1",
        (user_id,),
    )
    return dict(row) if row else None


async def _set_started_at(user_id: int, when: int) -> None:
    async with db.write() as conn:
        await conn.execute(
            "UPDATE risk_rounds SET started_at = ? WHERE user_id = ? AND status = 'active'",
            (when, user_id),
        )


class MutableMessage(Message):
    model_config = ConfigDict(frozen=False)
    _replies: list = PrivateAttr(default_factory=list)

    def __init__(self, text: str = "", user: User | None = None) -> None:
        super().__init__(
            message_id=6611,
            date=time.time(),
            chat=Chat(id=-100660002, type="supergroup", title="تیرامیکس"),
            from_user=user,
            text=text,
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


async def test_start_escrows_stake_and_hides_crash_point() -> None:
    player = await _player(1, credits=5_000)
    start = await _balance(player.user_id)

    res = await risk_game.start(
        player.user_id, 500, chat_id=-100660002, crash_point=4.35
    )
    assert res["success"] is True
    assert res["stake"] == 500
    assert await _balance(player.user_id) == start - 500

    row = await _round_row(player.user_id)
    assert row is not None
    assert row["status"] == "active"
    assert row["stake"] == 500
    assert row["started_at"] > 0
    # The hidden crash point stays in the row, never in the chat text.
    assert float(row["crash_point"]) == 4.35
    assert float(row["crash_point"]) >= settings.risk_crash_floor
    assert "4.35" not in res["message"]  # the crash point never leaks to chat

    bets = await db.fetchall(
        "SELECT kind, ref FROM ledger WHERE user_id = ? ORDER BY id",
        (player.user_id,),
    )
    assert (bets[-1]["kind"], bets[-1]["ref"]) == (ActivityKind.RISK.value, "risk:bet")

    # One live round per player.
    with pytest.raises(GameError):
        await risk_game.start(player.user_id, 500)
    assert await _balance(player.user_id) == start - 500


async def test_draw_crash_point_respects_floor_and_edge() -> None:
    # u → 1 means the exponential tail is never taken; the floor still holds.
    assert risk_game.draw_crash_point(1.0) == settings.risk_crash_floor
    for u in (0.9, 0.5, 0.2, 0.05):
        assert risk_game.draw_crash_point(u) >= settings.risk_crash_floor
    # Deterministic sample: same u, same point.
    assert risk_game.draw_crash_point(0.4) == risk_game.draw_crash_point(0.4)


async def test_status_multiplier_grows_with_elapsed_time(monkeypatch) -> None:
    player = await _player(2, credits=5_000)
    monkeypatch.setattr(risk_game, "now", lambda: FIXED_NOW)
    await risk_game.start(player.user_id, 500, crash_point=999.0)
    await _set_started_at(player.user_id, FIXED_NOW - 10)

    res = await risk_game.status(player.user_id)
    expected = round(1.0 + settings.risk_growth_rate * 10, 2)
    assert res["success"] is True
    assert res["multiplier"] == expected
    assert f"{expected:.2f}x" in res["message"]

    # Later on the wire, the same round is worth more.
    await _set_started_at(player.user_id, FIXED_NOW - 30)
    later = await risk_game.status(player.user_id)
    assert later["multiplier"] == round(1.0 + settings.risk_growth_rate * 30, 2)


async def test_cashout_before_crash_pays_stake_times_multiplier(monkeypatch) -> None:
    player = await _player(3, credits=5_000)
    start = await _balance(player.user_id)
    monkeypatch.setattr(risk_game, "now", lambda: FIXED_NOW)
    await risk_game.start(player.user_id, 500, crash_point=999.0)
    await _set_started_at(player.user_id, FIXED_NOW - 20)

    res = await risk_game.cashout(player.user_id)
    expected_mult = round(1.0 + settings.risk_growth_rate * 20, 2)
    payout = int(500 * expected_mult)
    assert res["success"] is True
    assert res["multiplier"] == expected_mult
    assert res["payout"] == payout
    assert await _balance(player.user_id) == start - 500 + payout

    row = await _round_row(player.user_id)
    assert row is not None
    assert row["status"] == "cashed"
    assert row["payout"] == payout

    # Round is gone — cashing out again is refused.
    with pytest.raises(GameError):
        await risk_game.cashout(player.user_id)


async def test_cashout_after_crash_is_a_loss(monkeypatch) -> None:
    player = await _player(4, credits=5_000)
    start = await _balance(player.user_id)
    monkeypatch.setattr(risk_game, "now", lambda: FIXED_NOW)
    await risk_game.start(player.user_id, 500, crash_point=1.5)
    await _set_started_at(player.user_id, FIXED_NOW - 10)  # 1 + 0.07*10 = 1.70 > 1.5

    res = await risk_game.cashout(player.user_id)
    assert res["success"] is False
    assert res["payout"] == 0
    assert "ترکید" in res["message"]
    assert await _balance(player.user_id) == start - 500  # stake stayed with the house

    row = await _round_row(player.user_id)
    assert row is not None and row["status"] == "crashed"


async def test_expiry_burns_the_stake_even_below_crash(monkeypatch) -> None:
    player = await _player(5, credits=5_000)
    start = await _balance(player.user_id)
    monkeypatch.setattr(risk_game, "now", lambda: FIXED_NOW)
    await risk_game.start(player.user_id, 500, crash_point=999.0)
    await _set_started_at(
        player.user_id, FIXED_NOW - settings.risk_round_seconds - 5
    )

    res = await risk_game.cashout(player.user_id)
    assert res["success"] is False
    assert "تموم شد" in res["message"]
    assert await _balance(player.user_id) == start - 500

    row = await _round_row(player.user_id)
    assert row is not None and row["status"] == "expired"


async def test_status_resolves_a_snapped_round_lazily(monkeypatch) -> None:
    player = await _player(6, credits=5_000)
    start = await _balance(player.user_id)
    monkeypatch.setattr(risk_game, "now", lambda: FIXED_NOW)
    await risk_game.start(player.user_id, 500, crash_point=1.2)
    await _set_started_at(player.user_id, FIXED_NOW - 30)

    res = await risk_game.status(player.user_id)
    assert res["success"] is False
    assert "ترکید" in res["message"]
    assert await _balance(player.user_id) == start - 500

    # The round closed itself; a second read reports the empty table + history.
    empty = await risk_game.status(player.user_id)
    assert empty["success"] is False
    assert "در جریان نیست" in empty["message"]
    last = await risk_game.recent(player.user_id)
    assert last["success"] is True
    assert last["status"] == "منفجر شد"


async def test_start_rejects_insufficient_funds_and_bad_stake() -> None:
    player = await _player(7)  # starting credits only
    start = await _balance(player.user_id)

    with pytest.raises(InsufficientFunds):
        await risk_game.start(player.user_id, settings.casino_max_bet)
    assert await _balance(player.user_id) == start
    assert await _round_row(player.user_id) is None

    with pytest.raises(GameError):
        await risk_game.start(player.user_id, settings.casino_min_bet - 1)
    assert await _balance(player.user_id) == start


async def test_handler_usage_status_and_cooldown() -> None:
    player = await _player(8, credits=5_000)
    user = User(id=player.user_id, is_bot=False, first_name="Risk", username="risk8")

    # Garbage argument → usage text, no cooldown burned.
    bad = MutableMessage(text="ریسک برو بیرون", user=user)
    await risk_handler.cmd_risk(bad)
    assert "ریسک [مبلغ]" in bad.replies[0]["text"]

    # Idle status with no round and no history.
    idle = MutableMessage(text="ریسک", user=user)
    await risk_handler.cmd_risk(idle)
    assert "ریسکی در جریان نیست" in idle.replies[0]["text"]

    # Start, then status shows the live round, then cooldown blocks a restart.
    started = MutableMessage(text="ریسک 100", user=user)
    await risk_handler.cmd_risk(started)
    assert "ریسک روی میز" in started.replies[0]["text"]

    live = MutableMessage(text="ریسک", user=user)
    await risk_handler.cmd_risk(live)
    assert "ضریب فعلی" in live.replies[0]["text"]

    again = MutableMessage(text="ریسک 100", user=user)
    await risk_handler.cmd_risk(again)
    assert "cools down" in again.replies[-1]["text"]


async def test_handler_cashout_keyword_works(monkeypatch) -> None:
    player = await _player(9, credits=5_000)
    start = await _balance(player.user_id)
    user = User(id=player.user_id, is_bot=False, first_name="Risk", username="risk9")
    monkeypatch.setattr(risk_game, "now", lambda: FIXED_NOW)

    await risk_game.start(player.user_id, 100, crash_point=999.0)
    await _set_started_at(player.user_id, FIXED_NOW - 5)

    msg = MutableMessage(text="ریسک برداشت", user=user)
    await risk_handler.cmd_risk(msg)
    payout = int(100 * round(1.0 + settings.risk_growth_rate * 5, 2))
    assert "🙌" in msg.replies[0]["text"]
    assert await _balance(player.user_id) == start - 100 + payout
