"""بازارچه — escrow listings, atomic settlement, cancel (temp DB)."""

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
from services import bazaar, economy, shop  # noqa: E402
from services.game import GameError, ensure_player  # noqa: E402

BASE_ID = 671_000
SELL_ITEM = "thief_kit"


@pytest_asyncio.fixture(autouse=True)
async def _database() -> None:
    if db._closed:  # noqa: SLF001
        await db.connect()
        await seed_catalog()
    yield
    await db.close()


async def _player(offset: int, credits: int = 0):
    player = await ensure_player(BASE_ID + offset, f"Bazaar {offset}", None)
    if credits:
        await economy.grant(
            player.user_id, credits=credits, kind=economy.ActivityKind.SYSTEM
        )
    return player


async def _seller_with_item(offset: int):
    """A player who owns the fence's thief kit outright."""
    player = await _player(offset, credits=100_000)
    await shop.buy_item(player.user_id, SELL_ITEM, drip=0)
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


async def _status(listing_id: int) -> str:
    async with db.read() as conn:
        cursor = await conn.execute(
            "SELECT status FROM bazaar_listings WHERE id = ?", (listing_id,)
        )
        row = await cursor.fetchone()
        await cursor.close()
    return str(row["status"]) if row else "missing"


async def test_listing_escrows_the_item_out_of_inventory() -> None:
    seller = await _seller_with_item(1)

    res = await bazaar.list_item(seller.user_id, "ابزار سرقت", 500)

    assert res["success"] is True
    assert not await _owns(seller.user_id, SELL_ITEM)
    assert await _status(res["listing_id"]) == "active"


async def test_listing_an_unowned_item_fails() -> None:
    seller = await _player(2, credits=1_000)
    with pytest.raises(GameError):
        await bazaar.list_item(seller.user_id, "ابزار سرقت", 500)


async def test_price_band_is_enforced() -> None:
    seller = await _seller_with_item(3)
    with pytest.raises(GameError):
        await bazaar.list_item(seller.user_id, "ابزار سرقت", settings.bazaar_price_min - 1)
    with pytest.raises(GameError):
        await bazaar.list_item(
            seller.user_id, "ابزار سرقت", settings.bazaar_price_max + 1
        )


async def test_browse_shows_active_listing() -> None:
    seller = await _seller_with_item(4)
    listed = await bazaar.list_item(seller.user_id, "ابزار سرقت", 700)

    res = await bazaar.browse()

    assert any(row["id"] == listed["listing_id"] for row in res["listings"])
    assert str(listed["listing_id"]) in res["message"]


async def test_buy_settles_item_and_both_wallets_atomically() -> None:
    seller = await _seller_with_item(5)
    listed = await bazaar.list_item(seller.user_id, "ابزار سرقت", 900)
    seller_before, _ = await economy.balances(seller.user_id)
    buyer = await _player(6, credits=10_000)
    buyer_before, _ = await economy.balances(buyer.user_id)

    res = await bazaar.buy_listing(buyer.user_id, listed["listing_id"])

    assert res["success"] is True
    assert await _owns(buyer.user_id, SELL_ITEM)
    assert not await _owns(seller.user_id, SELL_ITEM)
    seller_after, _ = await economy.balances(seller.user_id)
    buyer_after, _ = await economy.balances(buyer.user_id)
    assert seller_after == seller_before + 900
    assert buyer_after == buyer_before - 900
    assert await _status(listed["listing_id"]) == "sold"

    async with db.read() as conn:
        cursor = await conn.execute(
            "SELECT ref FROM ledger WHERE user_id = ? AND kind = ? ORDER BY id",
            (buyer.user_id, ActivityKind.BAZAAR.value),
        )
        rows = [dict(row) for row in await cursor.fetchall()]
        await cursor.close()
    assert any(f"bazaar:buy:{listed['listing_id']}" == row["ref"] for row in rows)


async def test_cannot_buy_own_listing() -> None:
    seller = await _seller_with_item(7)
    listed = await bazaar.list_item(seller.user_id, "ابزار سرقت", 500)
    with pytest.raises(GameError):
        await bazaar.buy_listing(seller.user_id, listed["listing_id"])


async def test_double_buy_is_rejected() -> None:
    seller = await _seller_with_item(8)
    listed = await bazaar.list_item(seller.user_id, "ابزار سرقت", 500)
    first = await _player(9, credits=10_000)
    second = await _player(10, credits=10_000)

    await bazaar.buy_listing(first.user_id, listed["listing_id"])
    with pytest.raises(GameError):
        await bazaar.buy_listing(second.user_id, listed["listing_id"])


async def test_cancel_returns_the_escrowed_item() -> None:
    seller = await _seller_with_item(11)
    listed = await bazaar.list_item(seller.user_id, "ابزار سرقت", 500)
    stranger = await _player(12)

    with pytest.raises(GameError):
        await bazaar.cancel(stranger.user_id, listed["listing_id"])

    res = await bazaar.cancel(seller.user_id, listed["listing_id"])

    assert res["success"] is True
    assert await _owns(seller.user_id, SELL_ITEM)
    assert await _status(listed["listing_id"]) == "cancelled"


async def test_buyer_needs_the_cash() -> None:
    seller = await _seller_with_item(13)
    listed = await bazaar.list_item(seller.user_id, "ابزار سرقت", 5_000)
    poor = await _player(14, credits=10)

    with pytest.raises(GameError):
        await bazaar.buy_listing(poor.user_id, listed["listing_id"])

    # Everything rolled back: still listed, buyer broke, seller keeps the kit.
    assert await _status(listed["listing_id"]) == "active"
    assert not await _owns(poor.user_id, SELL_ITEM)
