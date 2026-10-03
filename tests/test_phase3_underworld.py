"""سایه / اخاذی — guards, fees, payouts, jail (temp DB)."""

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
from services import economy, underworld  # noqa: E402
from services.game import GameError, ensure_player, now  # noqa: E402

BASE_ID = 672_000


@pytest_asyncio.fixture(autouse=True)
async def _database() -> None:
    if db._closed:  # noqa: SLF001
        await db.connect()
        await seed_catalog()
    yield
    await db.close()


async def _player(offset: int, credits: int = 0, level: int = 1):
    player = await ensure_player(BASE_ID + offset, f"Shade {offset}", None)
    async with db.write() as conn:
        # Pin the wallet: a brand-new player also gets starter credits.
        await conn.execute(
            "UPDATE wallets SET credits = ? WHERE user_id = ?",
            (credits, player.user_id),
        )
    if level != 1:
        async with db.write() as conn:
            await conn.execute(
                "UPDATE players SET level = ? WHERE user_id = ?",
                (level, player.user_id),
            )
        player = await ensure_player(player.user_id, player.display_name, None)
    return player


async def _credits(user_id: int) -> int:
    return (await economy.balances(user_id))[0]


async def _give_guard(user_id: int) -> None:
    """Hand over the guard item directly — keeps test balances clean."""
    async with db.write() as conn:
        await conn.execute(
            "INSERT OR IGNORE INTO inventory (user_id, item_id, acquired_at) "
            "VALUES (?, ?, ?)",
            (user_id, settings.guard_item_id, now()),
        )


async def _set_col(user_id: int, column: str, value: int) -> None:
    async with db.write() as conn:
        await conn.execute(
            f"UPDATE players SET {column} = ? WHERE user_id = ?",  # noqa: S608
            (value, user_id),
        )


async def _get_col(user_id: int, column: str) -> int:
    async with db.read() as conn:
        cursor = await conn.execute(
            f"SELECT {column} AS v FROM players WHERE user_id = ?",  # noqa: S608
            (user_id,),
        )
        row = await cursor.fetchone()
        await cursor.close()
    return int(row["v"]) if row else 0


# ---------------------------------------------------------------------------
# سایه هکر
# ---------------------------------------------------------------------------


async def test_hacker_success_drains_a_capped_slice() -> None:
    attacker = await _player(1, credits=2_000)
    victim = await _player(2, credits=10_000)

    res = await underworld.shadow_hack(attacker, victim, roll=0.0)

    assert res["success"] is True
    expected = int(10_000 * settings.shadow_hacker_steal_pct)
    assert res["stolen"] == expected
    assert await _credits(victim.user_id) == 10_000 - expected
    assert await _credits(attacker.user_id) == (
        2_000 - settings.shadow_hacker_fee + expected
    )


async def test_hacker_steal_is_capped_at_the_config_max() -> None:
    attacker = await _player(3, credits=2_000)
    victim = await _player(4, credits=5_000_000)

    res = await underworld.shadow_hack(attacker, victim, roll=0.0)

    assert res["stolen"] == settings.shadow_hacker_steal_max


async def test_hacker_failure_burns_fee_plus_fine() -> None:
    attacker = await _player(5, credits=5_000)
    victim = await _player(6, credits=10_000)

    res = await underworld.shadow_hack(attacker, victim, roll=0.99)

    assert res["success"] is False
    assert await _credits(victim.user_id) == 10_000
    assert await _credits(attacker.user_id) == (
        5_000 - settings.shadow_hacker_fee - settings.shadow_hacker_fail_fine
    )


async def test_guard_item_blocks_the_hack_but_the_fee_is_burned() -> None:
    attacker = await _player(7, credits=5_000)
    victim = await _player(8, credits=10_000)
    await _give_guard(victim.user_id)

    res = await underworld.shadow_hack(attacker, victim, roll=0.0)

    assert res["success"] is False
    assert res["blocked"] == "guard"
    assert "نگهبان" in res["message"]
    assert await _credits(victim.user_id) == 10_000
    assert await _credits(attacker.user_id) == 5_000 - settings.shadow_hacker_fee


async def test_active_shield_blocks_the_hack() -> None:
    attacker = await _player(9, credits=5_000)
    victim = await _player(10, credits=10_000)
    await _set_col(victim.user_id, "shield_until", now() + 600)
    victim = await ensure_player(victim.user_id, victim.display_name, None)

    res = await underworld.shadow_hack(attacker, victim, roll=0.0)

    assert res["success"] is False
    assert res["blocked"] == "shield"
    assert await _credits(attacker.user_id) == 5_000 - settings.shadow_hacker_fee


async def test_cannot_shadow_yourself() -> None:
    me = await _player(11, credits=5_000)
    with pytest.raises(GameError):
        await underworld.shadow_hack(me, me, roll=0.0)


# ---------------------------------------------------------------------------
# سایه قاتل
# ---------------------------------------------------------------------------


async def test_killer_success_jails_the_target() -> None:
    attacker = await _player(12, credits=5_000)
    victim = await _player(13, credits=1_000)

    res = await underworld.shadow_kill(attacker, victim, roll=0.0)

    assert res["success"] is True
    jailed_until = await _get_col(victim.user_id, "is_jailed_until")
    assert jailed_until > now()
    assert await _credits(attacker.user_id) == 5_000 - settings.shadow_killer_fee


async def test_killer_failure_burns_only_the_fee() -> None:
    attacker = await _player(14, credits=5_000)
    victim = await _player(15, credits=1_000)

    res = await underworld.shadow_kill(attacker, victim, roll=0.99)

    assert res["success"] is False
    assert await _get_col(victim.user_id, "is_jailed_until") == 0
    assert await _credits(attacker.user_id) == 5_000 - settings.shadow_killer_fee


async def test_killer_refuses_an_already_jailed_target() -> None:
    attacker = await _player(16, credits=5_000)
    victim = await _player(17, credits=1_000)
    await _set_col(victim.user_id, "is_jailed_until", now() + 300)
    victim = await ensure_player(victim.user_id, victim.display_name, None)

    with pytest.raises(GameError):
        await underworld.shadow_kill(attacker, victim, roll=0.0)
    # Nothing charged for an invalid contract.
    assert await _credits(attacker.user_id) == 5_000


async def test_guard_item_blocks_the_killer() -> None:
    attacker = await _player(18, credits=5_000)
    victim = await _player(19, credits=1_000)
    await _give_guard(victim.user_id)

    res = await underworld.shadow_kill(attacker, victim, roll=0.0)

    assert res["blocked"] == "guard"
    assert await _get_col(victim.user_id, "is_jailed_until") == 0


# ---------------------------------------------------------------------------
# اخاذی
# ---------------------------------------------------------------------------


async def test_extort_success_takes_a_percentage() -> None:
    attacker = await _player(20, credits=1_000)
    victim = await _player(21, credits=5_000)

    res = await underworld.extort(attacker, victim, roll=0.0)

    assert res["success"] is True
    expected = int(5_000 * settings.extort_steal_pct)
    assert res["stolen"] == expected
    assert await _credits(victim.user_id) == 5_000 - expected
    assert await _credits(attacker.user_id) == 1_000 + expected


async def test_extort_failure_pays_compensation_and_jails_attacker() -> None:
    attacker = await _player(22, credits=5_000)
    victim = await _player(23, credits=5_000)

    res = await underworld.extort(attacker, victim, roll=0.99)

    assert res["success"] is False
    comp = settings.extort_fail_compensation
    assert res["compensation"] == comp
    assert await _credits(attacker.user_id) == 5_000 - comp
    assert await _credits(victim.user_id) == 5_000 + comp
    assert await _get_col(attacker.user_id, "is_jailed_until") > now()
    assert await _get_col(victim.user_id, "is_jailed_until") == 0


async def test_guard_item_blocks_extort_and_nothing_moves() -> None:
    attacker = await _player(24, credits=5_000)
    victim = await _player(25, credits=5_000)
    await _give_guard(victim.user_id)

    res = await underworld.extort(attacker, victim, roll=0.0)

    assert res["success"] is False
    assert res["blocked"] == "guard"
    assert await _credits(attacker.user_id) == 5_000
    assert await _credits(victim.user_id) == 5_000


async def test_extort_chance_scales_with_level_and_is_clamped() -> None:
    rich = await _player(26, level=1)
    veteran = await _player(27, level=40)
    weak = await _player(28, level=1)

    high = underworld.extort_chance(veteran, rich)
    low = underworld.extort_chance(weak, veteran)
    even = underworld.extort_chance(rich, weak)

    assert high == settings.extort_success_max
    assert low == settings.extort_success_min
    assert even == pytest.approx(settings.extort_success_base)


async def test_shadow_fee_failure_leaves_no_partial_ledger_litter() -> None:
    """A too-expensive fee must roll the whole attempt back."""
    attacker = await _player(29, credits=10)  # poorer than the fee
    victim = await _player(30, credits=10_000)

    with pytest.raises(GameError):
        await underworld.shadow_hack(attacker, victim, roll=0.0)

    assert await _credits(attacker.user_id) == 10
    assert await _credits(victim.user_id) == 10_000


async def test_shadow_success_writes_ledger_rows() -> None:
    attacker = await _player(31, credits=2_000)
    victim = await _player(32, credits=10_000)

    await underworld.shadow_hack(attacker, victim, roll=0.0)

    async with db.read() as conn:
        cursor = await conn.execute(
            "SELECT ref FROM ledger WHERE user_id = ? AND kind = ? ORDER BY id",
            (victim.user_id, ActivityKind.SHADOW.value),
        )
        rows = [dict(row) for row in await cursor.fetchall()]
        await cursor.close()
    assert rows and rows[0]["ref"] == "shadow:hacker:loss"
