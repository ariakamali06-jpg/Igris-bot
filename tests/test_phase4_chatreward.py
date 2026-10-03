"""پاداش فعالیت چت — counters accumulate, pay out with روزانه, reset (temp DB)."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

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

BASE_ID = 674_000


@pytest_asyncio.fixture(autouse=True)
async def _database() -> None:
    if db._closed:  # noqa: SLF001
        await db.connect()
        await seed_catalog()
    yield
    await db.close()


async def _player(offset: int, credits: int = 0):
    player = await ensure_player(BASE_ID + offset, f"Chat {offset}", None)
    async with db.write() as conn:
        await conn.execute(
            "UPDATE wallets SET credits = ? WHERE user_id = ?",
            (credits, player.user_id),
        )
    return player


async def _messages(user_id: int) -> int:
    async with db.read() as conn:
        cursor = await conn.execute(
            "SELECT messages FROM chat_activity WHERE user_id = ?",
            (user_id,),
        )
        row = await cursor.fetchone()
        await cursor.close()
    return int(row["messages"]) if row else 0


async def test_bumps_accumulate_per_user() -> None:
    player = await _player(1)

    for _ in range(3):
        await economy.bump_chat_activity(player.user_id)
    other = await _player(2)
    await economy.bump_chat_activity(other.user_id)

    assert await _messages(player.user_id) == 3
    assert await _messages(other.user_id) == 1


async def test_daily_pays_the_chat_bonus_and_resets() -> None:
    player = await _player(3, credits=5_000)
    for _ in range(10):
        await economy.bump_chat_activity(player.user_id)
    before, _ = await economy.balances(player.user_id)

    result = await economy.claim_daily(player.user_id, drip=0)

    expected_daily = int(settings.daily_claim_credits)
    expected_bonus = 10 * settings.chat_reward_per_message
    assert result.success is True
    assert result.credits_delta == expected_daily + expected_bonus
    assert "پاداش فعالیت چت" in result.detail
    after, _ = await economy.balances(player.user_id)
    assert after == before + expected_daily + expected_bonus
    assert await _messages(player.user_id) == 0


async def test_chat_bonus_respects_the_config_cap() -> None:
    player = await _player(4, credits=5_000)
    for _ in range(settings.chat_reward_cap + 77):
        await economy.bump_chat_activity(player.user_id)
    before, _ = await economy.balances(player.user_id)

    result = await economy.claim_daily(player.user_id, drip=0)

    capped = settings.chat_reward_cap * settings.chat_reward_per_message
    assert result.credits_delta == int(settings.daily_claim_credits) + capped
    after, _ = await economy.balances(player.user_id)
    assert after - before == int(settings.daily_claim_credits) + capped


async def test_second_claim_in_the_same_day_keeps_the_counter() -> None:
    player = await _player(5, credits=5_000)
    await economy.claim_daily(player.user_id, drip=0)
    await economy.bump_chat_activity(player.user_id)
    await economy.bump_chat_activity(player.user_id)

    again = await economy.claim_daily(player.user_id, drip=0)

    assert again.success is False
    # A blocked claim must NOT swallow the chat counter.
    assert await _messages(player.user_id) == 2


async def test_daily_without_chat_activity_stays_the_base_amount() -> None:
    player = await _player(6, credits=5_000)
    before, _ = await economy.balances(player.user_id)

    result = await economy.claim_daily(player.user_id, drip=0)

    assert result.credits_delta == int(settings.daily_claim_credits)
    assert "پاداش فعالیت چت" not in result.detail
    after, _ = await economy.balances(player.user_id)
    assert after - before == int(settings.daily_claim_credits)
