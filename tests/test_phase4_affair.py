"""رکورد خیانتکارها — secret affairs count, busted ones don't (temp DB)."""

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

from database.connection import db  # noqa: E402
from database.seed import seed_catalog  # noqa: E402
from services import tiramix  # noqa: E402
from services.game import ensure_player  # noqa: E402

BASE_ID = 676_000


@pytest_asyncio.fixture(autouse=True)
async def _database() -> None:
    if db._closed:  # noqa: SLF001
        await db.connect()
        await seed_catalog()
    yield
    await db.close()


async def _player(offset: int):
    return await ensure_player(BASE_ID + offset, f"Cheater {offset}", None)


async def _married_trio(offset: int):
    """cheater A married to spouse C, with innocent bystander B as partner."""
    cheater = await _player(offset)
    partner = await _player(offset + 1)
    spouse = await _player(offset + 2)
    async with db.write() as conn:
        await conn.execute(
            "UPDATE players SET spouse_id = ? WHERE user_id = ?",
            (spouse.user_id, cheater.user_id),
        )
    cheater = await ensure_player(cheater.user_id, cheater.display_name, None)
    return cheater, partner, spouse


async def _affair_count(user_id: int) -> int:
    async with db.read() as conn:
        cursor = await conn.execute(
            "SELECT affair_count AS n FROM players WHERE user_id = ?",
            (user_id,),
        )
        row = await cursor.fetchone()
        await cursor.close()
    return int(row["n"]) if row else 0


async def test_secret_affair_counts_toward_the_record(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cheater, partner, spouse = await _married_trio(1)
    monkeypatch.setattr(tiramix.random, "random", lambda: 1.0)  # never caught

    res = await tiramix.attempt_affair(cheater, partner, spouse)

    assert res["success"] is True
    assert await _affair_count(cheater.user_id) == 1
    record = await tiramix.affair_record()
    assert "رکورد خیانتکارها" in record
    assert cheater.display_name in record


async def test_busted_affair_does_not_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cheater, partner, spouse = await _married_trio(4)
    monkeypatch.setattr(tiramix.random, "random", lambda: 0.0)  # always caught

    res = await tiramix.attempt_affair(cheater, partner, spouse)

    assert res["success"] is False and res.get("busted") is True
    assert await _affair_count(cheater.user_id) == 0


async def test_record_lists_the_biggest_cheater_first() -> None:
    king = await _player(7)
    runner = await _player(8)
    async with db.write() as conn:
        await conn.execute(
            "UPDATE players SET affair_count = 9 WHERE user_id = ?",
            (king.user_id,),
        )
        await conn.execute(
            "UPDATE players SET affair_count = 4 WHERE user_id = ?",
            (runner.user_id,),
        )

    record = await tiramix.affair_record()

    assert king.display_name in record
    assert runner.display_name in record
    assert record.index(king.display_name) < record.index(runner.display_name)
