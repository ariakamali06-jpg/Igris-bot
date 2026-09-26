"""Wallet atomicity, ledger reconciliation and cooldown gates (temp DB)."""

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
from services import economy  # noqa: E402
from services.game import ensure_player  # noqa: E402


@pytest_asyncio.fixture(autouse=True)
async def _database() -> None:
    """Connect on first use; close after *every* test.

    Closing in a fixture (not ``teardown_module``) matters: pytest does not
    await async module hooks in auto mode, and a leaked aiosqlite connection
    keeps its non-daemon worker thread alive, hanging the whole run at exit.
    """
    if db._closed:  # noqa: SLF001 - test reaching into lifecycle flag
        await db.connect()
        await seed_catalog()
    yield
    await db.close()


@pytest_asyncio.fixture()
async def player():
    return await ensure_player(991, "Ledger Tester", "ledger")


async def test_grant_then_spend_reconciles() -> None:
    # Random id: the test DB file may be reused between runs, so a fixed id
    # would inherit old balances and make the running-total assertion lie.
    player = await ensure_player(int(time.time() * 1000) % 10**9, "Reconcile", None)
    start, _ = await economy.balances(player.user_id)
    await economy.grant(
        player.user_id, credits=500, kind=economy.ActivityKind.WORK, ref="t1"
    )
    await economy.spend(
        player.user_id, credits=200, kind=economy.ActivityKind.SHOP_BUY, ref="t2"
    )
    end, _ = await economy.balances(player.user_id)
    assert end == start + 300

    rows = await db.fetchall(
        "SELECT credits_delta, balance_after_credits FROM ledger "
        "WHERE user_id = ? ORDER BY id",
        (player.user_id,),
    )
    running = start
    for row in rows:
        running += row["credits_delta"]
        assert row["balance_after_credits"] == running, (row, running)
    assert running == end


async def test_overspend_rejected_and_rolled_back(player) -> None:
    before, _ = await economy.balances(player.user_id)
    with pytest.raises(economy.InsufficientFunds):
        await economy.spend(
            player.user_id,
            credits=before + 1,
            kind=economy.ActivityKind.CASINO,
            ref="over",
        )
    after, _ = await economy.balances(player.user_id)
    assert after == before
    assert after >= 0


async def test_shards_never_negative(player) -> None:
    with pytest.raises(economy.InsufficientFunds):
        await economy.spend(
            player.user_id, shards=1, kind=economy.ActivityKind.SYSTEM, ref="sh"
        )


def test_cooldown_gate() -> None:
    economy.clear_cooldowns()
    economy.require_ready(1234, "unit", 30)
    with pytest.raises(economy.OnCooldown):
        economy.require_ready(1234, "unit", 30)
    # Different user is unaffected.
    economy.require_ready(4321, "unit", 30)
    economy.clear_cooldowns()


def test_drip_discount_is_capped() -> None:
    assert economy.drip_discount(0) == 0
    assert economy.drip_discount(10_000) == settings.drip_discount_cap
    assert economy.discounted_price(1000, 10_000) == round(
        1000 * (1 - settings.drip_discount_cap)
    )


def test_casino_payout_has_house_edge() -> None:
    # EV = p * payout - bet must equal -edge * bet for the configured odds.
    bet = 10_000
    p = 15 / 36
    multiplier = (1 - settings.dice_house_edge) / p
    payout = int(bet * multiplier)
    ev = p * payout - bet
    assert ev < 0, "casino must never be +EV for the player"
    assert ev >= -settings.dice_house_edge * bet - 1  # int() rounding tolerance
