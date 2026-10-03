"""قرارداد — ledger-derived progress, once-per-window payout (temp DB)."""

from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path

import pytest_asyncio

# Point the DB at a scratch file before config/settings are first used.
os.environ.setdefault(
    "DB_PATH", str(Path(tempfile.mkdtemp(prefix="tgbot-pytest-")) / "test.db")
)

from config import settings  # noqa: E402
from database.connection import db  # noqa: E402
from database.seed import seed_catalog  # noqa: E402
from models.enums import ActivityKind  # noqa: E402
from services import contracts, economy  # noqa: E402
from services.game import ensure_player  # noqa: E402

BASE_ID = 620_000

# One seeding recipe per contract code: enough ledger rows to complete it.
_SEED: dict[str, tuple[ActivityKind, int, int]] = {
    # code: (ledger kind, credits per row, rows to insert)
    "work": (ActivityKind.WORK, 10, settings.contract_target_work),
    "study": (ActivityKind.STUDY, 0, settings.contract_target_study),
    "shop": (ActivityKind.SHOP_BUY, 0, settings.contract_target_shop),
    "theft": (ActivityKind.THEFT, 1, settings.contract_target_theft),
}


@pytest_asyncio.fixture(autouse=True)
async def _database() -> None:
    if db._closed:  # noqa: SLF001
        await db.connect()
        await seed_catalog()
    yield
    await db.close()


async def _player(offset: int):
    return await ensure_player(BASE_ID + offset, f"Contract {offset}", None)


async def _seed_code(user_id: int, code: str, rows: int) -> None:
    kind, credits, _ = _SEED[code]
    for i in range(rows):
        await economy.grant(
            user_id, credits=credits, kind=kind, ref=f"contract-seed:{code}:{i}"
        )


def _today_codes() -> list[contracts.ContractDef]:
    day = contracts.day_index(int(time.time()))
    return contracts.contracts_for_day(day)


async def test_rotation_returns_unique_daily_contracts() -> None:
    day = contracts.day_index(int(time.time()))
    defs = contracts.contracts_for_day(day)
    assert len(defs) == settings.contract_count
    assert len({d.code for d in defs}) == len(defs)
    codes = {d.code for d in defs}
    assert codes <= set(_SEED)


async def test_completed_contracts_pay_once() -> None:
    player = await _player(1)
    # Seed every one of today's contracts to its full target.
    for definition in _today_codes():
        await _seed_code(player.user_id, definition.code, _SEED[definition.code][2])

    # Read the baseline AFTER seeding: the seed rows themselves move coins
    # (the theft contract needs a positive ledger delta to be counted).
    start, _ = await economy.balances(player.user_id)
    start_exp = player.exp
    count = len(_today_codes())
    res = await contracts.sync(player.user_id)
    assert res["success"] is True
    assert res["earned"] == settings.contract_reward_credits * count
    assert res["exp"] == settings.contract_reward_exp * count
    assert all(entry["done"] for entry in res["contracts"])

    end, _ = await economy.balances(player.user_id)
    assert end == start + settings.contract_reward_credits * count

    # EXP was granted inside the transaction exactly once.
    fresh = await ensure_player(player.user_id, player.display_name, None)
    assert fresh.exp == start_exp + settings.contract_reward_exp * count

    # Re-opening the board must never pay again (regression: the old handler
    # granted EXP a second time outside the transaction).
    second = await contracts.sync(player.user_id)
    assert second["earned"] == 0
    assert second["exp"] == 0
    final, _ = await economy.balances(player.user_id)
    assert final == end


async def test_partial_progress_pays_nothing() -> None:
    player = await _player(2)
    start, _ = await economy.balances(player.user_id)

    definition = _today_codes()[0]
    target = _SEED[definition.code][2]
    # One row short of the target (or nothing at all for target==1).
    await _seed_code(player.user_id, definition.code, max(0, target - 1))

    res = await contracts.sync(player.user_id)
    assert res["earned"] == 0
    assert res["exp"] == 0
    assert not any(entry["done"] for entry in res["contracts"])

    end, _ = await economy.balances(player.user_id)
    assert end == start


async def test_stale_done_from_yesterday_pays_again() -> None:
    player = await _player(3)
    day = contracts.day_index(int(time.time()))

    # Snapshot left over from yesterday's window, already marked done.
    async with db.write() as conn:
        for definition in _today_codes():
            await conn.execute(
                """
                INSERT INTO contracts (user_id, code, progress, day, done)
                VALUES (?, ?, 99, ?, 1)
                ON CONFLICT (user_id, code) DO UPDATE SET
                    progress = excluded.progress,
                    day = excluded.day,
                    done = excluded.done
                """,
                (player.user_id, definition.code, day - 1),
            )

    # Today's activity completed all of them: a stale done flag from a
    # previous window must not block the new payout.
    for definition in _today_codes():
        await _seed_code(player.user_id, definition.code, _SEED[definition.code][2])

    # Baseline after seeding — the seed rows themselves move coins.
    start, _ = await economy.balances(player.user_id)
    res = await contracts.sync(player.user_id)
    count = len(_today_codes())
    assert res["earned"] == settings.contract_reward_credits * count
    assert all(entry["done"] for entry in res["contracts"])

    end, _ = await economy.balances(player.user_id)
    assert end == start + settings.contract_reward_credits * count
