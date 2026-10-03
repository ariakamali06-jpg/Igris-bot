"""املاک / نیرو — passive income accrual, buy/upgrade/collect (temp DB)."""

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
from services import economy, properties  # noqa: E402
from services.game import GameError, ensure_player  # noqa: E402

BASE_ID = 610_000


@pytest_asyncio.fixture(autouse=True)
async def _database() -> None:
    if db._closed:  # noqa: SLF001
        await db.connect()
        await seed_catalog()
    yield
    await db.close()


async def _player(offset: int, credits: int = 0):
    player = await ensure_player(BASE_ID + offset, f"Estate {offset}", None)
    if credits:
        await economy.grant(
            player.user_id, credits=credits, kind=economy.ActivityKind.SYSTEM
        )
    return player


async def _set_last_collect(user_id: int, when: int) -> None:
    async with db.write() as conn:
        await conn.execute(
            "UPDATE properties SET last_collect = ? WHERE owner_id = ?",
            (when, user_id),
        )


async def test_buy_debits_wallet_and_inserts_row() -> None:
    player = await _player(1, credits=5_000)
    start, _ = await economy.balances(player.user_id)

    res = await properties.buy_asset(
        player, properties.KIND_PROPERTY, "کارگاه زیرزمینی"
    )
    assert res["success"] is True

    end, _ = await economy.balances(player.user_id)
    assert end == start - 1_200

    row = await db.fetchone(
        "SELECT kind, level, last_collect FROM properties "
        "WHERE owner_id = ? AND name = ?",
        (player.user_id, "کارگاه زیرزمینی"),
    )
    assert row is not None
    assert row["kind"] == "property"
    assert row["level"] == 1
    assert row["last_collect"] > 0


async def test_duplicate_purchase_rejected() -> None:
    player = await _player(2, credits=5_000)
    await properties.buy_asset(player, properties.KIND_PROPERTY, "کارگاه زیرزمینی")
    start, _ = await economy.balances(player.user_id)

    res = await properties.buy_asset(
        player, properties.KIND_PROPERTY, "کارگاه زیرزمینی"
    )
    assert res["success"] is False

    end, _ = await economy.balances(player.user_id)
    assert end == start  # no second debit


async def test_insufficient_funds_rolls_back() -> None:
    player = await _player(3)  # fresh wallet: starting credits only
    with pytest.raises(economy.InsufficientFunds):
        await properties.buy_asset(player, properties.KIND_PROPERTY, "برج تجاری")

    row = await db.fetchone(
        "SELECT 1 AS ok FROM properties WHERE owner_id = ?", (player.user_id,)
    )
    assert row is None  # the insert rolled back with the debit


async def test_pending_income_accrues_and_collects() -> None:
    player = await _player(4, credits=5_000)
    await properties.buy_asset(player, properties.KIND_PROPERTY, "کارگاه زیرزمینی")
    start, _ = await economy.balances(player.user_id)

    # Pretend two full intervals elapsed since acquisition → 2 ticks.
    now = int(time.time())
    await _set_last_collect(player.user_id, now - 2 * settings.property_income_interval)

    expected = 2 * properties.income_per_tick(20, 1)  # کارگاه: income 20, level 1

    # Nothing collected yet — pending shows up in the listing.
    view = await properties.list_estate(player, properties.KIND_PROPERTY)
    assert f"انباشته: <b>{expected:,}</b>" in view["message"]

    res = await properties.collect(player)
    assert res["success"] is True
    assert res["earned"] == expected

    end, _ = await economy.balances(player.user_id)
    assert end == start + expected

    # A second sweep right away has nothing left to collect.
    again = await properties.collect(player)
    assert again["success"] is False
    assert again["earned"] == 0


async def test_pending_income_capped_after_long_absence() -> None:
    player = await _player(5, credits=5_000)
    await properties.buy_asset(player, properties.KIND_PROPERTY, "کارگاه زیرزمینی")

    now = int(time.time())
    ages_away = settings.property_max_pending_ticks + 500
    await _set_last_collect(
        player.user_id, now - ages_away * settings.property_income_interval
    )

    res = await properties.collect(player)
    cap = settings.property_max_pending_ticks * properties.income_per_tick(20, 1)
    assert res["earned"] == cap


async def test_upgrade_raises_income_and_respects_max_level() -> None:
    player = await _player(6, credits=300_000)  # full ladder to level 10 costs ~150k
    await properties.buy_asset(player, properties.KIND_PROPERTY, "کارگاه زیرزمینی")
    start, _ = await economy.balances(player.user_id)

    before = properties.income_per_tick(20, 1)
    res = await properties.upgrade_property(player, "کارگاه زیرزمینی")
    assert res["success"] is True
    assert res["level"] == 2
    after = properties.income_per_tick(20, 2)
    assert after > before

    cost = properties.upgrade_cost(1)
    end, _ = await economy.balances(player.user_id)
    assert end == start - cost

    # Max level: keep upgrading until the cap, then expect refusal.
    for _ in range(settings.property_max_level - 2):
        res = await properties.upgrade_property(player, "کارگاه زیرزمینی")
        assert res["success"] is True
    res = await properties.upgrade_property(player, "کارگاه زیرزمینی")
    assert res["success"] is False


async def test_upgrade_requires_ownership() -> None:
    player = await _player(7, credits=50_000)
    res = await properties.upgrade_property(player, "کارگاه زیرزمینی")
    assert res["success"] is False


async def test_worker_purchase_and_shared_collect() -> None:
    player = await _player(8, credits=10_000)
    res = await properties.buy_asset(player, properties.KIND_WORKER, "نگهبان شب")
    assert res["success"] is True

    row = await db.fetchone(
        "SELECT kind FROM properties WHERE owner_id = ? AND name = ?",
        (player.user_id, "نگهبان شب"),
    )
    assert row is not None and row["kind"] == "worker"

    # وصول sweeps workers too.
    now = int(time.time())
    await _set_last_collect(
        player.user_id, now - settings.property_income_interval
    )
    collect = await properties.collect(player)
    assert collect["success"] is True
    assert collect["earned"] == properties.income_per_tick(40, 1)


async def test_find_entry_partial_match_and_unknown() -> None:
    entry = properties.find_entry(properties.KIND_PROPERTY, "بوفه")
    assert entry["name"] == "بوفه کوچه"  # unique substring

    with pytest.raises(GameError):
        properties.find_entry(properties.KIND_PROPERTY, "هتل پنج ستاره")
    with pytest.raises(GameError):
        properties.find_entry(properties.KIND_PROPERTY, "")
