"""بورس — deterministic prices, buy/sell, bet settlement (temp DB)."""

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

from config import MARKET_ASSETS, settings  # noqa: E402
from database.connection import db  # noqa: E402
from database.seed import seed_catalog  # noqa: E402
from services import economy, market  # noqa: E402
from services.game import GameError, ensure_player  # noqa: E402

BASE_ID = 630_000


@pytest_asyncio.fixture(autouse=True)
async def _database() -> None:
    if db._closed:  # noqa: SLF001
        await db.connect()
        await seed_catalog()
    yield
    await db.close()


async def _player(offset: int, credits: int = 0):
    player = await ensure_player(BASE_ID + offset, f"Market {offset}", None)
    if credits:
        await economy.grant(
            player.user_id, credits=credits, kind=economy.ActivityKind.SYSTEM
        )
    return player


async def test_prices_are_deterministic_and_bounded() -> None:
    hour = market.hour_now()
    for asset in MARKET_ASSETS:
        first = market.price_at(asset, hour)
        second = market.price_at(asset, hour)
        assert first == second  # pure function of (asset, hour)
        low = asset["base"] * settings.market_price_min_factor
        high = asset["base"] * settings.market_price_max_factor
        assert low <= first <= high


async def test_find_asset_by_name_symbol_and_partial() -> None:
    assert market.find_asset("طلا")["symbol"] == "GOLD"
    assert market.find_asset("GOLD")["name"] == "طلا"
    assert market.find_asset("بیت")["symbol"] == "BTC"  # unique substring
    with pytest.raises(GameError):
        market.find_asset("نقره")
    with pytest.raises(GameError):
        market.find_asset("")


async def test_buy_then_sell_roundtrip() -> None:
    player = await _player(1, credits=5_000)
    start, _ = await economy.balances(player.user_id)

    res = await market.buy(player, "طلا", 500)
    assert res["success"] is True
    after_buy, _ = await economy.balances(player.user_id)
    assert after_buy == start - 500
    assert res["units"] > 0

    pos = await market.holdings(player.user_id)
    assert pos["طلا"] == res["units"]

    sell = await market.sell(player, "طلا")
    assert sell["success"] is True
    assert sell["value"] in (499, 500)  # rounding dust at most 1 coin

    final, _ = await economy.balances(player.user_id)
    assert final == start - 500 + sell["value"]
    assert await market.holdings(player.user_id) == {}


async def test_sell_without_position_fails() -> None:
    player = await _player(2, credits=5_000)
    start, _ = await economy.balances(player.user_id)
    res = await market.sell(player, "نفت")
    assert res["success"] is False
    end, _ = await economy.balances(player.user_id)
    assert end == start


async def test_trade_limits_enforced() -> None:
    player = await _player(3, credits=1_000_000)
    with pytest.raises(GameError):
        await market.buy(player, "طلا", settings.market_trade_min - 1)
    with pytest.raises(GameError):
        await market.buy(player, "طلا", settings.market_trade_max + 1)


async def test_bet_escrows_stake_then_settles_as_win(monkeypatch) -> None:
    player = await _player(4, credits=10_000)
    start, _ = await economy.balances(player.user_id)

    res = await market.place_bet(player, 1_000)
    assert res["success"] is True
    escrowed, _ = await economy.balances(player.user_id)
    assert escrowed == start - 1_000

    placed_hour = res["hour"]
    # Force the index up at the next close → deterministic win.
    monkeypatch.setattr(
        market, "index_at", lambda h: 1.0 if h == placed_hour else 2.0
    )
    settled = await market.settle_bets(player.user_id, placed_hour + 1)
    assert len(settled) == 1
    assert settled[0]["status"] == "won"

    expected_payout = int(
        1_000 * (1.0 - settings.market_bet_house_edge) / settings.market_bet_win_prob
    )
    assert settled[0]["payout"] == expected_payout

    final, _ = await economy.balances(player.user_id)
    assert final == escrowed + expected_payout

    # Nothing left pending: a second settle is a no-op.
    assert await market.settle_bets(player.user_id, placed_hour + 1) == []


async def test_bet_settles_as_loss(monkeypatch) -> None:
    player = await _player(5, credits=10_000)
    start, _ = await economy.balances(player.user_id)

    res = await market.place_bet(player, 1_000)
    placed_hour = res["hour"]
    escrowed, _ = await economy.balances(player.user_id)
    assert escrowed == start - 1_000

    monkeypatch.setattr(
        market, "index_at", lambda h: 2.0 if h == placed_hour else 1.0
    )
    settled = await market.settle_bets(player.user_id, placed_hour + 1)
    assert settled[0]["status"] == "lost"
    assert settled[0]["payout"] == 0

    final, _ = await economy.balances(player.user_id)
    assert final == escrowed  # the stake stays with the house


async def test_bet_push_refunds_stake(monkeypatch) -> None:
    player = await _player(6, credits=10_000)
    res = await market.place_bet(player, 1_000)
    placed_hour = res["hour"]
    escrowed, _ = await economy.balances(player.user_id)

    monkeypatch.setattr(market, "index_at", lambda h: 1.5)  # flat tape
    settled = await market.settle_bets(player.user_id, placed_hour + 1)
    assert settled[0]["status"] == "push"
    assert settled[0]["payout"] == 1_000

    final, _ = await economy.balances(player.user_id)
    assert final == escrowed + 1_000


async def test_bet_limits_enforced() -> None:
    player = await _player(7, credits=1_000_000)
    with pytest.raises(GameError):
        await market.place_bet(player, settings.market_bet_min - 1)
    with pytest.raises(GameError):
        await market.place_bet(player, settings.market_bet_max + 1)


async def test_board_reports_every_asset() -> None:
    rows = await market.board()
    assert len(rows) == len(MARKET_ASSETS)
    for row in rows:
        assert row["price"] > 0
        assert "change_pct" in row
        assert row["symbol"]
