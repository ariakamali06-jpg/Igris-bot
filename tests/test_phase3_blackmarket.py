"""کاسب — fence shelf, exclusive stock, atomic purchase (temp DB)."""

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
from services import blackmarket, economy, shop  # noqa: E402
from services.game import GameError, ensure_player  # noqa: E402

BASE_ID = 670_000


@pytest_asyncio.fixture(autouse=True)
async def _database() -> None:
    if db._closed:  # noqa: SLF001
        await db.connect()
        await seed_catalog()
    yield
    await db.close()


async def _player(offset: int, credits: int = 0):
    player = await ensure_player(BASE_ID + offset, f"Fence {offset}", None)
    if credits:
        await economy.grant(
            player.user_id, credits=credits, kind=economy.ActivityKind.SYSTEM
        )
    return player


async def _owns(user_id: int, item_id: str) -> bool:
    async with db.read() as conn:
        cursor = await conn.execute(
            "SELECT 1 FROM inventory WHERE user_id = ? AND item_id = ?",
            (user_id, item_id),
        )
        row = await cursor.fetchone()
        await cursor.close()
    return row is not None


def _price(item_id: str) -> int:
    return next(i.price_credits for i in blackmarket.fence_stock() if i.id == item_id)


async def test_shelf_lists_exclusive_dark_stock() -> None:
    res = await blackmarket.shelf()
    assert {item.id for item in blackmarket.fence_stock()} == {
        "thief_kit",
        "guard_item",
    }
    assert "نگهبان شخصی" in res["message"]
    assert "ابزار سرقت" in res["message"]


async def test_fence_stock_never_reaches_the_boutique() -> None:
    stock_ids = {item.id for item in await shop.today_stock()}
    assert stock_ids.isdisjoint({"thief_kit", "guard_item"})


async def test_buy_moves_item_and_charges_full_price() -> None:
    player = await _player(1, credits=10_000)
    before, _ = await economy.balances(player.user_id)

    res = await blackmarket.buy(player.user_id, "نگهبان شخصی", drip=0)

    assert res["success"] is True
    assert await _owns(player.user_id, "guard_item")
    after, _ = await economy.balances(player.user_id)
    assert before - after == _price("guard_item")


async def test_double_buy_is_refused_with_stock_message() -> None:
    player = await _player(2, credits=10_000)
    await blackmarket.buy(player.user_id, "guard", drip=0)

    res = await blackmarket.buy(player.user_id, "guard", drip=0)

    assert res["success"] is False
    assert "داری" in res["message"]


async def test_unknown_item_raises_domain_error() -> None:
    player = await _player(3, credits=10_000)
    with pytest.raises(GameError):
        await blackmarket.buy(player.user_id, "چیز ناموجود", drip=0)


async def test_poor_buy_refused_no_item_no_charge() -> None:
    player = await _player(4, credits=10)
    before, _ = await economy.balances(player.user_id)

    with pytest.raises(GameError):
        await blackmarket.buy(player.user_id, "ابزار سرقت", drip=0)

    assert not await _owns(player.user_id, "thief_kit")
    after, _ = await economy.balances(player.user_id)
    assert after == before
