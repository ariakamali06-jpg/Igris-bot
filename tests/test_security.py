"""سپر / دستبرد — shield blocks theft, bank heist outcomes (temp DB)."""

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
from services import economy, security, tiramix  # noqa: E402
from services.game import GameError, ensure_player  # noqa: E402

BASE_ID = 640_000


@pytest_asyncio.fixture(autouse=True)
async def _database() -> None:
    if db._closed:  # noqa: SLF001
        await db.connect()
        await seed_catalog()
    yield
    await db.close()


async def _player(offset: int, credits: int = 0, level: int | None = None):
    player = await ensure_player(BASE_ID + offset, f"Security {offset}", None)
    if credits:
        await economy.grant(
            player.user_id, credits=credits, kind=economy.ActivityKind.SYSTEM
        )
    if level is not None:
        async with db.write() as conn:
            await conn.execute(
                "UPDATE players SET level = ? WHERE user_id = ?",
                (level, player.user_id),
            )
        player = await ensure_player(player.user_id, player.display_name, None)
    return player


async def test_shield_cost_is_clamped_percentage() -> None:
    assert security.shield_cost(0) == settings.shield_cost_min
    assert security.shield_cost(250) == settings.shield_cost_min
    huge = 10_000_000
    assert security.shield_cost(huge) == settings.shield_cost_max
    mid = 40_000
    assert security.shield_cost(mid) == int(mid * settings.shield_cost_pct)


async def test_shield_blocks_theft_cleanly() -> None:
    thief = await _player(1)
    victim = await _player(2, credits=1_000)

    # Victim buys the cover.
    res = await security.buy_shield(victim)
    assert res["success"] is True
    cost = security.shield_cost(1_000 + settings.starting_credits)
    assert res["cost"] == cost

    # Re-hydrate so shield_until is loaded from the row.
    victim = await ensure_player(victim.user_id, victim.display_name, None)
    assert security.has_active_shield(victim) is True

    thief_before, _ = await economy.balances(thief.user_id)
    victim_before, _ = await economy.balances(victim.user_id)

    outcome = await tiramix.attempt_theft(thief, victim)
    assert outcome["success"] is False
    assert outcome.get("blocked") == "shield"

    # No coins moved and no punishment for the thief.
    thief_after, _ = await economy.balances(thief.user_id)
    victim_after, _ = await economy.balances(victim.user_id)
    assert thief_after == thief_before
    assert victim_after == victim_before
    thief_fresh = await ensure_player(thief.user_id, thief.display_name, None)
    assert thief_fresh.is_jailed_until == 0


async def test_shield_status_reflects_state() -> None:
    player = await _player(3, credits=2_000)

    before = await security.shield_status(player)
    assert before["active"] is False

    await security.buy_shield(player)
    player = await ensure_player(player.user_id, player.display_name, None)
    after = await security.shield_status(player)
    assert after["active"] is True


async def test_bank_heist_level_gate_blocks() -> None:
    player = await _player(4, level=1)  # below bank_heist_min_level
    start, _ = await economy.balances(player.user_id)

    res = await security.bank_heist(player, 1_000)
    assert res["success"] is False
    assert res["error"] == "level"

    end, _ = await economy.balances(player.user_id)
    assert end == start  # nothing was escrowed


async def test_bank_heist_rejects_out_of_range_stake() -> None:
    player = await _player(5, level=5)
    with pytest.raises(GameError):
        await security.bank_heist(player, settings.bank_heist_stake_min - 1)
    with pytest.raises(GameError):
        await security.bank_heist(player, settings.bank_heist_stake_max + 1)


async def test_bank_heist_win_pays_multiplier() -> None:
    player = await _player(6, credits=5_000, level=5)
    start, _ = await economy.balances(player.user_id)
    start_exp = player.exp

    res = await security.bank_heist(player, 1_000, roll=0.0)
    assert res["success"] is True
    payout = int(1_000 * settings.bank_heist_payout_multiplier)
    assert res["payout"] == payout

    end, _ = await economy.balances(player.user_id)
    assert end == start - 1_000 + payout

    fresh = await ensure_player(player.user_id, player.display_name, None)
    assert fresh.exp == start_exp + settings.bank_heist_exp
    assert fresh.is_jailed_until == 0


async def test_bank_heist_loss_pays_fine_when_able() -> None:
    player = await _player(7, credits=7_000, level=5)
    start, _ = await economy.balances(player.user_id)

    res = await security.bank_heist(player, 1_000, roll=1.0)
    assert res["success"] is False
    assert res["error"] == "fine"
    fine = int(1_000 * settings.bank_heist_fine_multiplier)
    assert res["fine"] == fine

    end, _ = await economy.balances(player.user_id)
    assert end == start - 1_000 - fine

    fresh = await ensure_player(player.user_id, player.display_name, None)
    assert fresh.is_jailed_until == 0  # paid, so no jail


async def test_bank_heist_loss_goes_to_jail_when_broke() -> None:
    # 2_000 on hand: the 1_000 escrow leaves 1_000 < 5_000 fine → jail.
    player = await _player(8, credits=1_750, level=5)

    res = await security.bank_heist(player, 1_000, roll=1.0)
    assert res["success"] is False
    assert res["error"] == "jail"

    end, _ = await economy.balances(player.user_id)
    assert end == settings.starting_credits + 1_750 - 1_000

    fresh = await ensure_player(player.user_id, player.display_name, None)
    assert fresh.is_jailed_until > int(time.time())


async def test_bank_heist_refused_while_in_jail() -> None:
    player = await _player(9, credits=5_000, level=5)
    async with db.write() as conn:
        await conn.execute(
            "UPDATE players SET is_jailed_until = ? WHERE user_id = ?",
            (int(time.time()) + 600, player.user_id),
        )
    player = await ensure_player(player.user_id, player.display_name, None)

    res = await security.bank_heist(player, 1_000, roll=0.0)
    assert res["success"] is False
    assert res["error"] == "jail"
