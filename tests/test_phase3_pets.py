"""حیوان — buy/upgrade curve, escrowed challenges, settled battles (temp DB)."""

from __future__ import annotations

import os
import tempfile
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
from models import ActivityKind  # noqa: E402
from services import economy, pets  # noqa: E402
from services.game import GameError, ensure_player, now  # noqa: E402

BASE_ID = 673_000
STAKE = 1_000


@pytest_asyncio.fixture(autouse=True)
async def _database() -> None:
    if db._closed:  # noqa: SLF001
        await db.connect()
        await seed_catalog()
    yield
    await db.close()


async def _player(offset: int, credits: int = 100_000):
    player = await ensure_player(BASE_ID + offset, f"Pet {offset}", None)
    async with db.write() as conn:
        # Pin the wallet: a brand-new player also gets starter credits.
        await conn.execute(
            "UPDATE wallets SET credits = ? WHERE user_id = ?",
            (credits, player.user_id),
        )
    return player


async def _credits(user_id: int) -> int:
    return (await economy.balances(user_id))[0]


async def _fresh(user_id: int):
    return await ensure_player(user_id, f"Pet {user_id}", None)


async def _set_pet(user_id: int, level: int) -> None:
    async with db.write() as conn:
        await conn.execute(
            "UPDATE players SET pet_level = ? WHERE user_id = ?",
            (level, user_id),
        )


async def _challenge_row(challenger_id: int, target_id: int) -> dict | None:
    async with db.read() as conn:
        cursor = await conn.execute(
            "SELECT * FROM pet_challenges WHERE challenger_id = ? "
            "AND target_id = ? ORDER BY id DESC LIMIT 1",
            (challenger_id, target_id),
        )
        row = await cursor.fetchone()
        await cursor.close()
    return dict(row) if row else None


# ---------------------------------------------------------------------------
# خرید / ارتقا
# ---------------------------------------------------------------------------


async def test_buy_grants_the_first_pet() -> None:
    player = await _player(1)
    before = await _credits(player.user_id)

    res = await pets.buy(player)

    assert res["success"] is True
    assert res["level"] == 1
    assert (await _fresh(player.user_id)).pet_level == 1
    assert before - await _credits(player.user_id) == settings.pet_base_cost


async def test_second_pet_is_refused() -> None:
    player = await _player(2)
    await pets.buy(player)
    with pytest.raises(GameError):
        await pets.buy(player)


async def test_upgrade_from_level_one_costs_the_base_price() -> None:
    player = await _player(3)
    await pets.buy(player)
    before = await _credits(player.user_id)

    res = await pets.upgrade(player)

    assert res["level"] == 2
    assert (await _fresh(player.user_id)).pet_level == 2
    assert before - await _credits(player.user_id) == (
        settings.pet_upgrade_cost_base
    )


async def test_upgrade_cost_follows_the_config_growth() -> None:
    player = await _player(4)
    await _set_pet(player.user_id, 2)
    player = await _fresh(player.user_id)
    before = await _credits(player.user_id)

    await pets.upgrade(player)

    expected = int(
        settings.pet_upgrade_cost_base * settings.pet_upgrade_cost_growth
    )
    assert before - await _credits(player.user_id) == expected
    assert (await _fresh(player.user_id)).pet_level == 3


async def test_upgrade_without_a_pet_fails() -> None:
    player = await _player(5)
    with pytest.raises(GameError):
        await pets.upgrade(player)


async def test_upgrade_stops_at_the_config_cap() -> None:
    player = await _player(6)
    await _set_pet(player.user_id, settings.pet_max_level)
    player = await _fresh(player.user_id)
    with pytest.raises(GameError):
        await pets.upgrade(player)


# ---------------------------------------------------------------------------
# چالش (اسکرو)
# ---------------------------------------------------------------------------


async def test_challenge_escrows_the_challenger_stake() -> None:
    challenger = await _player(7)
    target = await _player(8)
    await pets.buy(challenger)
    before = await _credits(challenger.user_id)

    res = await pets.challenge(challenger, target, STAKE)

    assert res["success"] is True
    assert before - await _credits(challenger.user_id) == STAKE
    row = await _challenge_row(challenger.user_id, target.user_id)
    assert row is not None and row["status"] == "pending"
    assert row["amount"] == STAKE
    assert row["expires_at"] > now()


async def test_challenge_requires_a_pet() -> None:
    challenger = await _player(9)
    target = await _player(10)
    with pytest.raises(GameError):
        await pets.challenge(challenger, target, STAKE)


async def test_challenge_amount_band_is_enforced() -> None:
    challenger = await _player(11)
    target = await _player(12)
    await pets.buy(challenger)
    with pytest.raises(GameError):
        await pets.challenge(challenger, target, settings.pet_battle_min - 1)
    with pytest.raises(GameError):
        await pets.challenge(challenger, target, settings.pet_battle_max + 1)


async def test_cannot_challenge_yourself() -> None:
    me = await _player(13)
    await pets.buy(me)
    with pytest.raises(GameError):
        await pets.challenge(me, me, STAKE)


# ---------------------------------------------------------------------------
# تسویه نبرد
# ---------------------------------------------------------------------------


def _payout(amount: int) -> tuple[int, int]:
    pot = amount * 2
    rake = int(pot * settings.pet_battle_rake)
    return pot - rake, rake


async def test_accept_settles_when_the_challenger_wins() -> None:
    challenger = await _player(14)
    target = await _player(15)
    await pets.buy(challenger)
    await pets.buy(target)
    await pets.challenge(challenger, target, STAKE)
    c_before = await _credits(challenger.user_id)
    t_before = await _credits(target.user_id)

    res = await pets.accept(
        target,
        challenger,
        roll_challenger=1.0,
        roll_target=0.0,
    )

    payout, rake = _payout(STAKE)
    assert res["success"] is True and res["draw"] is False
    assert res["winner_id"] == challenger.user_id
    assert res["payout"] == payout and res["rake"] == rake
    assert await _credits(challenger.user_id) == c_before + payout
    assert await _credits(target.user_id) == t_before
    row = await _challenge_row(challenger.user_id, target.user_id)
    assert row["status"] == "resolved"
    assert row["winner_id"] == challenger.user_id


async def test_accept_settles_when_the_target_wins() -> None:
    challenger = await _player(16)
    target = await _player(17)
    await pets.buy(challenger)
    await pets.buy(target)
    await pets.challenge(challenger, target, STAKE)
    c_before = await _credits(challenger.user_id)
    t_before = await _credits(target.user_id)

    res = await pets.accept(
        target,
        challenger,
        roll_challenger=0.0,
        roll_target=1.0,
    )

    payout, _ = _payout(STAKE)
    assert res["winner_id"] == target.user_id
    assert await _credits(target.user_id) == t_before + payout
    assert await _credits(challenger.user_id) == c_before


async def test_a_draw_refunds_both_sides() -> None:
    challenger = await _player(18)
    target = await _player(19)
    await pets.buy(challenger)
    await pets.buy(target)
    await pets.challenge(challenger, target, STAKE)
    c_before = await _credits(challenger.user_id)
    t_before = await _credits(target.user_id)

    res = await pets.accept(
        target,
        challenger,
        roll_challenger=0.5,
        roll_target=0.5,
    )

    assert res["draw"] is True
    assert await _credits(challenger.user_id) == c_before + STAKE
    assert await _credits(target.user_id) == t_before


async def test_accept_without_a_pending_challenge_fails() -> None:
    challenger = await _player(20)
    target = await _player(21)
    await pets.buy(challenger)
    await pets.buy(target)
    with pytest.raises(GameError):
        await pets.accept(target, challenger)


async def test_accept_fails_when_the_target_has_no_pet() -> None:
    challenger = await _player(22)
    target = await _player(23)
    await pets.buy(challenger)
    await pets.challenge(challenger, target, STAKE)
    before = await _credits(challenger.user_id)

    with pytest.raises(GameError):
        await pets.accept(target, challenger)

    # The escrow must stay put until cancelled or expired.
    assert await _credits(challenger.user_id) == before
    assert (await _challenge_row(challenger.user_id, target.user_id))[
        "status"
    ] == "pending"


async def test_decline_refunds_the_challenger() -> None:
    challenger = await _player(24)
    target = await _player(25)
    await pets.buy(challenger)
    await pets.challenge(challenger, target, STAKE)
    before = await _credits(challenger.user_id)

    res = await pets.decline(target, challenger)

    assert res["success"] is True
    assert await _credits(challenger.user_id) == before + STAKE
    assert (await _challenge_row(challenger.user_id, target.user_id))[
        "status"
    ] == "declined"


async def test_cancel_refunds_the_challenger() -> None:
    challenger = await _player(26)
    target = await _player(27)
    await pets.buy(challenger)
    await pets.challenge(challenger, target, STAKE)
    before = await _credits(challenger.user_id)

    res = await pets.cancel(challenger, target)

    assert res["success"] is True
    assert await _credits(challenger.user_id) == before + STAKE
    assert (await _challenge_row(challenger.user_id, target.user_id))[
        "status"
    ] == "cancelled"


async def test_expired_challenge_refunds_on_accept_attempt() -> None:
    challenger = await _player(28)
    target = await _player(29)
    await pets.buy(challenger)
    await pets.buy(target)
    row = await pets.challenge(challenger, target, STAKE)
    async with db.write() as conn:
        await conn.execute(
            "UPDATE pet_challenges SET expires_at = ? WHERE id = ?",
            (now() - 1, int(row["challenge_id"])),
        )
    before = await _credits(challenger.user_id)

    res = await pets.accept(target, challenger)

    # The refund commits on the normal return path — a raise would roll it back.
    assert res["success"] is False and res.get("expired") is True
    assert await _credits(challenger.user_id) == before + STAKE
    assert (await _challenge_row(challenger.user_id, target.user_id))[
        "status"
    ] == "expired"


async def test_settlement_writes_ledger_rows() -> None:
    challenger = await _player(30)
    target = await _player(31)
    await pets.buy(challenger)
    await pets.buy(target)
    await pets.challenge(challenger, target, STAKE)

    async with db.read() as conn:
        cursor = await conn.execute(
            "SELECT ref FROM ledger WHERE user_id = ? AND kind = ? ORDER BY id",
            (challenger.user_id, ActivityKind.PET.value),
        )
        refs = [row["ref"] for row in await cursor.fetchall()]
        await cursor.close()
    assert refs == ["pet:buy", "pet:stake"]
