"""جام — round lifecycle: escrow, settle, rake, refunds (temp DB)."""

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
from services import cup, economy  # noqa: E402
from services.game import GameError, ensure_player, now  # noqa: E402

BASE_ID = 675_000
CHAT_ID = -100_555_001


@pytest_asyncio.fixture(autouse=True)
async def _database() -> None:
    if db._closed:  # noqa: SLF001
        await db.connect()
        await seed_catalog()
    # Rounds are keyed by chat, so every test starts from an empty cup.
    async with db.write() as conn:
        await conn.execute("DELETE FROM cup_entries")
        await conn.execute("DELETE FROM cup_rounds")
    yield
    await db.close()


async def _player(offset: int, credits: int = 10_000):
    player = await ensure_player(BASE_ID + offset, f"Cup {offset}", None)
    async with db.write() as conn:
        await conn.execute(
            "UPDATE wallets SET credits = ? WHERE user_id = ?",
            (credits, player.user_id),
        )
    return player


async def _credits(user_id: int) -> int:
    return (await economy.balances(user_id))[0]


async def _round_row(round_id: int) -> dict:
    async with db.read() as conn:
        cursor = await conn.execute(
            "SELECT * FROM cup_rounds WHERE id = ?", (round_id,)
        )
        row = await cursor.fetchone()
        await cursor.close()
    assert row is not None
    return dict(row)


async def _force_close(round_id: int) -> None:
    async with db.write() as conn:
        await conn.execute(
            "UPDATE cup_rounds SET closes_at = ? WHERE id = ?",
            (now() - 1, round_id),
        )


async def _roll_series(monkeypatch: pytest.MonkeyPatch, values: list[float]) -> None:
    series = iter(values)
    monkeypatch.setattr(cup.random, "random", lambda: next(series))


# ---------------------------------------------------------------------------
# ثبت‌نام
# ---------------------------------------------------------------------------


async def test_join_opens_a_round_and_escrows_the_fee() -> None:
    player = await _player(1)
    before = await _credits(player.user_id)

    res = await cup.join(player, CHAT_ID)

    assert res["success"] is True
    assert before - await _credits(player.user_id) == settings.cup_entry_fee
    row = await _round_row(res["round_id"])
    assert row["status"] == "open"
    assert row["entry_fee"] == settings.cup_entry_fee


async def test_duplicate_join_is_refused() -> None:
    player = await _player(2)
    await cup.join(player, CHAT_ID)
    with pytest.raises(GameError):
        await cup.join(player, CHAT_ID)


async def test_poor_join_leaves_no_round_behind() -> None:
    player = await _player(3, credits=10)
    with pytest.raises(GameError):
        await cup.join(player, CHAT_ID)
    # The fee mutation rolled the whole join (round included) back.
    async with db.read() as conn:
        cursor = await conn.execute(
            "SELECT COUNT(*) AS n FROM cup_rounds WHERE chat_id = ?",
            (CHAT_ID,),
        )
        row = await cursor.fetchone()
        await cursor.close()
    assert int(row["n"]) == 0


async def test_status_shows_open_round_with_entries() -> None:
    player = await _player(4)
    res_join = await cup.join(player, CHAT_ID)

    res = await cup.status(CHAT_ID, player)

    assert res["open"] is True
    assert res["entries"] == 1
    assert res["round_id"] == res_join["round_id"]
    assert "باز" in res["message"]


async def test_status_on_a_quiet_chat() -> None:
    res = await cup.status(CHAT_ID + 1)
    assert res["open"] is False
    assert "برپا نیست" in res["message"]


# ---------------------------------------------------------------------------
# تسویه
# ---------------------------------------------------------------------------


async def test_settlement_pays_the_top_scorer_the_whole_pot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    p1 = await _player(5)
    p2 = await _player(6)
    p3 = await _player(7)
    joined = await cup.join(p1, CHAT_ID)
    await cup.join(p2, CHAT_ID)
    await cup.join(p3, CHAT_ID)
    await _force_close(joined["round_id"])
    # Entries settle in join order: p2 outscores p1 and p3.
    await _roll_series(monkeypatch, [0.1, 0.9, 0.5])

    res = await cup.status(CHAT_ID)

    pot = settings.cup_entry_fee * 3
    rake = int(pot * settings.cup_rake)
    payout = pot - rake
    assert res["settled"] is True and res["cancelled"] is False
    assert res["winner_ids"] == [p2.user_id]
    # Everyone paid the entry; only p2 got the pot back on top.
    assert await _credits(p1.user_id) == 10_000 - settings.cup_entry_fee
    assert await _credits(p2.user_id) == (
        10_000 - settings.cup_entry_fee + payout
    )
    assert await _credits(p3.user_id) == 10_000 - settings.cup_entry_fee
    assert (await _round_row(joined["round_id"]))["status"] == "settled"

    async with db.read() as conn:
        cursor = await conn.execute(
            "SELECT ref FROM ledger WHERE user_id = ? AND kind = ?",
            (p2.user_id, ActivityKind.CUP.value),
        )
        refs = [row["ref"] for row in await cursor.fetchall()]
        await cursor.close()
    assert "cup:win" in refs


async def test_tied_top_scores_split_the_pool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    p1 = await _player(8)
    p2 = await _player(9)
    p3 = await _player(10)
    joined = await cup.join(p1, CHAT_ID)
    await cup.join(p2, CHAT_ID)
    await cup.join(p3, CHAT_ID)
    await _force_close(joined["round_id"])
    await _roll_series(monkeypatch, [0.7, 0.7, 0.7])

    await cup.status(CHAT_ID)

    pot = settings.cup_entry_fee * 3
    rake = int(pot * settings.cup_rake)
    share = (pot - rake) // 3
    for player in (p1, p2, p3):
        assert await _credits(player.user_id) == (
            10_000 - settings.cup_entry_fee + share
        )


async def test_underattended_round_cancels_and_refunds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    p1 = await _player(11)
    p2 = await _player(12)
    joined = await cup.join(p1, CHAT_ID)
    await cup.join(p2, CHAT_ID)
    await _force_close(joined["round_id"])
    await _roll_series(monkeypatch, [0.9, 0.1])  # never consumed: < min players

    res = await cup.status(CHAT_ID)

    assert res["cancelled"] is True
    assert await _credits(p1.user_id) == 10_000
    assert await _credits(p2.user_id) == 10_000
    assert (await _round_row(joined["round_id"]))["status"] == "cancelled"


async def test_a_new_round_can_open_after_settlement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    p1 = await _player(13)
    p2 = await _player(14)
    p3 = await _player(15)
    joined = await cup.join(p1, CHAT_ID)
    await cup.join(p2, CHAT_ID)
    await cup.join(p3, CHAT_ID)
    await _force_close(joined["round_id"])
    await _roll_series(monkeypatch, [0.2, 0.4, 0.6])
    await cup.status(CHAT_ID)

    fresh = await _player(16)
    res = await cup.join(fresh, CHAT_ID)

    assert res["round_id"] != joined["round_id"]
    assert (await _round_row(res["round_id"]))["status"] == "open"
