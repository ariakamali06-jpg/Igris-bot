"""قرعه / هدیه — lottery draw + gift codes (temp DB)."""

from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path

import pytest
import pytest_asyncio

# Point the DB at a scratch file before config/settings are first used.
os.environ.setdefault(
    "DB_PATH", str(Path(tempfile.mkdtemp(prefix="tgbot-pytest-")) / "test.db")
)

from config import settings  # noqa: E402
from database.connection import db  # noqa: E402
from database.seed import seed_catalog  # noqa: E402
from services import economy, rewards  # noqa: E402
from services.game import GameError, ensure_player  # noqa: E402

BASE_ID = 650_000


@pytest_asyncio.fixture(autouse=True)
async def _database() -> None:
    if db._closed:  # noqa: SLF001
        await db.connect()
        await seed_catalog()
    yield
    await db.close()


async def _player(offset: int):
    return await ensure_player(BASE_ID + offset, f"Reward {offset}", None)


async def _reset_lottery() -> None:
    """Wipe the singleton state so each test starts a clean round."""
    async with db.write() as conn:
        await conn.execute("DELETE FROM lottery_state")
        await conn.execute("DELETE FROM lottery_tickets")


async def _force_due_draw() -> None:
    async with db.write() as conn:
        await conn.execute("UPDATE lottery_state SET next_draw_at = 1")


# ---------------------------------------------------------------------------
# Gift codes (هدیه)
# ---------------------------------------------------------------------------


async def test_gift_create_redeem_and_once_per_player() -> None:
    creator = await _player(1)
    alice = await _player(2)
    bob = await _player(3)
    carol = await _player(4)

    created = await rewards.create_code("WELCOMEXX", 500, 2, creator.user_id)
    assert created["success"] is True
    assert created["code"] == "welcomexx"  # stored normalised

    start_a, _ = await economy.balances(alice.user_id)
    first = await rewards.redeem(alice, "WELCOMEXX")  # case-insensitive
    assert first["success"] is True
    assert first["credits"] == 500
    end_a, _ = await economy.balances(alice.user_id)
    assert end_a == start_a + 500

    # Same player again → refused, no second credit.
    again = await rewards.redeem(alice, "welcomexx")
    assert again["success"] is False
    end_a2, _ = await economy.balances(alice.user_id)
    assert end_a2 == end_a

    # Capacity 2: bob fits, carol hits the cap.
    second = await rewards.redeem(bob, "welcomeXX")
    assert second["success"] is True
    third = await rewards.redeem(carol, "WELCOMEXX")
    assert third["success"] is False


async def test_gift_duplicate_code_rejected() -> None:
    creator = await _player(5)
    first = await rewards.create_code("DUPCODE1", 100, 1, creator.user_id)
    assert first["success"] is True
    second = await rewards.create_code("dupcode1", 100, 1, creator.user_id)
    assert second["success"] is False


async def test_gift_expired_code_refused() -> None:
    creator = await _player(6)
    claimer = await _player(7)
    await rewards.create_code("EXPIRED1", 300, 5, creator.user_id)

    async with db.write() as conn:
        await conn.execute(
            "UPDATE gift_codes SET expires_at = ? WHERE code = 'expired1'",
            (int(time.time()) - 10,),
        )

    res = await rewards.redeem(claimer, "EXPIRED1")
    assert res["success"] is False
    bal_before, _ = await economy.balances(claimer.user_id)
    await rewards.redeem(claimer, "EXPIRED1")
    bal_after, _ = await economy.balances(claimer.user_id)
    assert bal_after == bal_before  # an expired code never credits


async def test_gift_validation_errors() -> None:
    creator = await _player(8)
    with pytest.raises(GameError):
        await rewards.create_code("ab", 100, 1, creator.user_id)  # too short
    with pytest.raises(GameError):
        await rewards.create_code("okcode", 0, 1, creator.user_id)  # no money
    with pytest.raises(GameError):
        await rewards.create_code("okcode", 100, 0, creator.user_id)  # no capacity
    with pytest.raises(GameError):
        await rewards.redeem(creator, "")  # empty key is a usage error

    res = await rewards.redeem(creator, "missingcode")
    assert res["success"] is False  # unknown code → refused, not raised


# ---------------------------------------------------------------------------
# Lottery (قرعه)
# ---------------------------------------------------------------------------


async def test_lottery_ticket_feeds_pot_then_draw_pays() -> None:
    await _reset_lottery()
    player = await _player(9)
    start, _ = await economy.balances(player.user_id)

    res = await rewards.buy_ticket(player)
    assert res["success"] is True
    assert res["tickets"] == 1
    pot_add = int(settings.lottery_ticket_price * settings.lottery_pot_share)
    assert res["pot"] == settings.lottery_initial_pot + pot_add

    after_ticket, _ = await economy.balances(player.user_id)
    assert after_ticket == start - settings.lottery_ticket_price

    await _force_due_draw()
    view = await rewards.status(player)  # triggers the lazy draw

    # Only ticket holder in the round → guaranteed winner.
    expected_win = settings.lottery_initial_pot + pot_add
    final, _ = await economy.balances(player.user_id)
    assert final == after_ticket + expected_win

    assert view["state"]["round"] == 1  # rolled into the next round
    assert view["tickets"] == 0  # fresh round, no tickets yet
    assert "خودت بودی" in view["message"]


async def test_lottery_rollover_without_tickets() -> None:
    await _reset_lottery()
    player = await _player(10)
    start, _ = await economy.balances(player.user_id)

    # Materialise the state, then make it due with zero tickets sold.
    await rewards._ensure_state()
    await _force_due_draw()

    view = await rewards.status(player)
    assert view["state"]["round"] == 1
    assert view["state"]["pot"] == settings.lottery_initial_pot  # rolled over
    assert "بدون بلیت" in view["message"]

    # Nobody was paid: the balance is untouched.
    end, _ = await economy.balances(player.user_id)
    assert end == start


async def test_lottery_status_before_due_shows_countdown() -> None:
    await _reset_lottery()
    player = await _player(11)
    view = await rewards.status(player)
    assert view["state"]["round"] == 0
    assert view["tickets"] == 0
    assert "قرعه بعدی" in view["message"]
    assert view["state"]["next_draw_at"] > int(time.time())
