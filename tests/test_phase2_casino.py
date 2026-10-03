"""کازینو / قل‌سنگ / شانس / قفل — phase-2 house games (temp DB).

Covers rigged reels, RPS win/push/loss against the house, the hidden-number
payout, the 3-digit safe lifecycle (escrow → feedback → open/jam), plus
handler-level usage errors and the slot cooldown. Player ids: 660_000+.
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

from config import SLOT_SYMBOLS, settings  # noqa: E402
from database.connection import db  # noqa: E402
from database.seed import seed_catalog  # noqa: E402
from handlers import casino  # noqa: E402
from models.enums import ActivityKind  # noqa: E402
from services import casino_games, economy  # noqa: E402
from services.game import GameError, InsufficientFunds, ensure_player  # noqa: E402

BASE_ID = 660_000


@pytest_asyncio.fixture(autouse=True)
async def _database() -> None:
    if db._closed:  # noqa: SLF001
        await db.connect()
        await seed_catalog()
    economy.clear_cooldowns()
    yield
    await db.close()


async def _player(offset: int, credits: int = 0):
    player = await ensure_player(BASE_ID + offset, f"Arcade {offset}", None)
    if credits:
        await economy.grant(
            player.user_id, credits=credits, kind=ActivityKind.SYSTEM
        )
    return player


async def _balance(user_id: int) -> int:
    credits, _ = await economy.balances(user_id)
    return credits


class MutableMessage(Message):
    model_config = ConfigDict(frozen=False)
    _replies: list = PrivateAttr(default_factory=list)

    def __init__(self, text: str = "", user: User | None = None) -> None:
        super().__init__(
            message_id=6601,
            date=time.time(),
            chat=Chat(id=-100660001, type="supergroup", title="تیرامیکس"),
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


# ---------------------------------------------------------------------------
# کازینو — slots
# ---------------------------------------------------------------------------


async def test_slots_three_of_kind_pays_symbol_multiplier() -> None:
    player = await _player(1, credits=5_000)
    start = await _balance(player.user_id)

    res = await casino_games.slots(player.user_id, 100, spin=("👑", "👑", "👑"))
    multiplier = next(r["three"] for r in SLOT_SYMBOLS if r["symbol"] == "👑")
    payout = 100 * multiplier

    assert res["success"] is True
    assert res["payout"] == payout
    assert res["delta"] == payout - 100
    assert await _balance(player.user_id) == start - 100 + payout

    rows = await db.fetchall(
        "SELECT kind, ref FROM ledger WHERE user_id = ? ORDER BY id",
        (player.user_id,),
    )
    kinds = [(row["kind"], row["ref"]) for row in rows]
    assert (ActivityKind.SLOT.value, "slot:bet") in kinds
    assert (ActivityKind.SLOT.value, "slot:win") in kinds


async def test_slots_loss_debits_stake() -> None:
    player = await _player(2, credits=5_000)
    start = await _balance(player.user_id)

    res = await casino_games.slots(player.user_id, 200, spin=("🍒", "🎭", "💰"))
    assert res["success"] is False
    assert res["payout"] == 0
    assert res["delta"] == -200
    assert await _balance(player.user_id) == start - 200


async def test_slots_pair_pays_pair_multiplier() -> None:
    player = await _player(3, credits=5_000)
    start = await _balance(player.user_id)

    res = await casino_games.slots(player.user_id, 100, spin=("🍒", "👑", "🍒"))
    expected = int(100 * settings.slot_pair_multiplier)
    assert res["payout"] == expected
    assert await _balance(player.user_id) == start - 100 + expected


async def test_slots_insufficient_funds_rolls_back() -> None:
    player = await _player(4)  # starting credits only
    start = await _balance(player.user_id)
    assert start < settings.casino_max_bet

    with pytest.raises(InsufficientFunds):
        await casino_games.slots(player.user_id, settings.casino_max_bet)
    assert await _balance(player.user_id) == start  # escrow rolled back


async def test_slots_handler_usage_error_and_cooldown() -> None:
    player = await _player(5, credits=2_000)
    user = User(id=player.user_id, is_bot=False, first_name="Arcade", username="arc5")

    # Missing amount → usage, and no cooldown burned for a parse failure.
    bad = MutableMessage(text="کازینو", user=user)
    await casino.cmd_slots(bad)
    assert "کازینو [مبلغ]" in bad.replies[0]["text"]

    good = MutableMessage(text=f"کازینو {settings.casino_min_bet}", user=user)
    await casino.cmd_slots(good)
    assert "🎰" in good.replies[0]["text"]

    again = MutableMessage(text=f"کازینو {settings.casino_min_bet}", user=user)
    await casino.cmd_slots(again)
    assert "cools down" in again.replies[-1]["text"]


# ---------------------------------------------------------------------------
# قل‌سنگ — rock-paper-scissors vs the house
# ---------------------------------------------------------------------------


async def test_rps_win_pays_house_edge_payout() -> None:
    player = await _player(6, credits=5_000)
    start = await _balance(player.user_id)

    res = await casino_games.rps(player.user_id, 100, "کاغذ", house_move="سنگ")
    expected = max(101, int(100 * (1.0 - settings.rps_house_edge) / settings.rps_win_prob))
    assert res["success"] is True
    assert res["payout"] == expected
    assert await _balance(player.user_id) == start - 100 + expected


async def test_rps_draw_pushes_stake_back() -> None:
    player = await _player(7, credits=5_000)
    start = await _balance(player.user_id)

    res = await casino_games.rps(player.user_id, 300, "سنگ", house_move="سنگ")
    assert res["success"] is True
    assert res["delta"] == 0
    assert await _balance(player.user_id) == start


async def test_rps_loss_debits_stake() -> None:
    player = await _player(8, credits=5_000)
    start = await _balance(player.user_id)

    res = await casino_games.rps(player.user_id, 100, "سنگ", house_move="کاغذ")
    assert res["success"] is False
    assert res["delta"] == -100
    assert await _balance(player.user_id) == start - 100


async def test_rps_invalid_move_and_insufficient_funds() -> None:
    player = await _player(9, credits=5_000)
    start = await _balance(player.user_id)

    with pytest.raises(GameError):
        await casino_games.rps(player.user_id, 100, "چاقو")
    assert await _balance(player.user_id) == start

    with pytest.raises(InsufficientFunds):
        await casino_games.rps(
            player.user_id, settings.casino_max_bet, "سنگ", house_move="کاغذ"
        )
    assert await _balance(player.user_id) == start


async def test_rps_handler_usage_error() -> None:
    player = await _player(10)
    user = User(id=player.user_id, is_bot=False, first_name="Arcade", username="arc10")

    msg = MutableMessage(text="قل‌سنگ 100", user=user)
    await casino.cmd_rps(msg)
    assert "قل‌سنگ [مبلغ] سنگ|کاغذ|قیچی" in msg.replies[0]["text"]


# ---------------------------------------------------------------------------
# شانس — guess the hidden number
# ---------------------------------------------------------------------------


async def test_guess_hit_pays_multiplier() -> None:
    player = await _player(11, credits=5_000)
    start = await _balance(player.user_id)

    res = await casino_games.guess(player.user_id, 100, 7, secret=7)
    payout = int(100 * settings.guess_win_multiplier)
    assert res["success"] is True
    assert res["payout"] == payout
    assert await _balance(player.user_id) == start - 100 + payout


async def test_guess_miss_debits_stake() -> None:
    player = await _player(12, credits=5_000)
    start = await _balance(player.user_id)

    res = await casino_games.guess(player.user_id, 100, 7, secret=9)
    assert res["success"] is False
    assert res["payout"] == 0
    assert await _balance(player.user_id) == start - 100


async def test_guess_rejects_out_of_range_and_broke_bet() -> None:
    player = await _player(13, credits=5_000)
    start = await _balance(player.user_id)

    with pytest.raises(GameError):
        await casino_games.guess(player.user_id, 100, settings.guess_number_max + 1)
    with pytest.raises(GameError):
        await casino_games.guess(player.user_id, 100, 0)
    assert await _balance(player.user_id) == start

    with pytest.raises(InsufficientFunds):
        await casino_games.guess(
            player.user_id, settings.casino_max_bet, 3, secret=3
        )
    assert await _balance(player.user_id) == start


async def test_guess_handler_requires_two_args() -> None:
    player = await _player(14)
    user = User(id=player.user_id, is_bot=False, first_name="Arcade", username="arc14")

    msg = MutableMessage(text="شانس 300", user=user)
    await casino.cmd_guess(msg)
    assert "شانس [مبلغ] [عدد]" in msg.replies[0]["text"]


# ---------------------------------------------------------------------------
# قفل — the 3-digit safe
# ---------------------------------------------------------------------------


async def test_safe_start_escrows_and_hides_the_code() -> None:
    player = await _player(15, credits=5_000)
    start = await _balance(player.user_id)

    res = await casino_games.safe_start(player.user_id, 500, code="482")
    assert res["success"] is True
    assert "482" not in res["message"]  # the code never leaks to chat
    assert await _balance(player.user_id) == start - 500

    row = await db.fetchone(
        "SELECT * FROM safe_locks WHERE user_id = ?", (player.user_id,)
    )
    assert row is not None
    assert row["code"] == "482"
    assert row["status"] == "active"
    assert row["attempts"] == 0

    # A second lock while one is live is refused, wallet untouched.
    with pytest.raises(GameError):
        await casino_games.safe_start(player.user_id, 500)
    assert await _balance(player.user_id) == start - 500


async def test_safe_attempts_report_digits_then_open() -> None:
    player = await _player(16, credits=5_000)
    start = await _balance(player.user_id)
    await casino_games.safe_start(player.user_id, 500, code="482")

    miss = await casino_games.safe_attempt(player.user_id, "111")
    assert miss["success"] is False
    assert miss["hits"] == 0
    assert "0 رقم درست" in miss["message"]

    partial = await casino_games.safe_attempt(player.user_id, "489")
    assert partial["hits"] == 2
    assert "2 رقم درست" in partial["message"]
    assert str(settings.safe_max_attempts - 2) in partial["message"]

    hit = await casino_games.safe_attempt(player.user_id, "482")
    payout = int(500 * settings.safe_payout_multiplier)
    assert hit["success"] is True
    assert hit["payout"] == payout
    assert await _balance(player.user_id) == start - 500 + payout

    row = await db.fetchone(
        "SELECT status, attempts FROM safe_locks WHERE user_id = ?", (player.user_id,)
    )
    assert row is not None
    assert row["status"] == "opened"
    assert row["attempts"] == 3


async def test_safe_jams_after_max_attempts() -> None:
    player = await _player(17, credits=5_000)
    start = await _balance(player.user_id)
    await casino_games.safe_start(player.user_id, 500, code="482")

    wrong = ["111", "222", "333", "444", "555", "666"][: settings.safe_max_attempts]
    result = None
    for token in wrong:
        result = await casino_games.safe_attempt(player.user_id, token)
    assert result is not None
    assert result["success"] is False
    assert "تموم شد" in result["message"]
    assert await _balance(player.user_id) == start - 500  # stake forfeited

    row = await db.fetchone(
        "SELECT status FROM safe_locks WHERE user_id = ?", (player.user_id,)
    )
    assert row is not None and row["status"] == "failed"

    # Jammed lock refuses further attempts and a fresh one can be opened.
    with pytest.raises(GameError):
        await casino_games.safe_attempt(player.user_id, "482")


async def test_safe_status_and_three_digit_stake_start() -> None:
    player = await _player(18, credits=5_000)
    start = await _balance(player.user_id)

    idle = await casino_games.safe_status(player.user_id)
    assert idle["success"] is False
    assert "قفلی در کار نیست" in idle["message"]

    # With no live lock a 3-digit argument is the *stake*, not a code.
    res = await casino_games.safe_start(player.user_id, 500, code="731")
    assert res["success"] is True
    row = await db.fetchone(
        "SELECT stake FROM safe_locks WHERE user_id = ?", (player.user_id,)
    )
    assert row is not None and row["stake"] == 500
    assert await _balance(player.user_id) == start - 500

    live = await casino_games.safe_status(player.user_id)
    assert live["success"] is True
    assert "731" not in live["message"]

    with pytest.raises(GameError):
        await casino_games.safe_attempt(player.user_id, "73")  # not 3 digits
