"""Exchange (بورس): hourly prices, positions and next-hour bets.

Prices are a *pure function* of ``(asset, hour)``: a seeded PRNG blends two
consecutive hourly noises, so every client (and every test) sees the same
tape, the series is continuous hour-to-hour, and history never needs to be
simulated forward from the epoch.  Rows are additionally cached in
``market_prices`` for display and audit.

Bets escrow their stake at placement, then settle lazily against the index
once the hour rolls over.  The payout is ``stake * (1 - edge) / win_prob``,
which makes the expected value exactly ``-market_bet_house_edge`` — the house
edge survives even though the underlying walk is fair.
"""

from __future__ import annotations

import random
import time
from typing import Any

from config import MARKET_ASSETS, settings
from database.connection import db
from models.enums import ActivityKind
from models.player import Player
from services import economy
from services.game import GameError

# Cooldown action names (``economy.require_ready`` keys).
ACTION_TRADE = "market_trade"
ACTION_BET = "market_bet"


def hour_now(current: int | None = None) -> int:
    """Index of the current market hour."""
    current = int(time.time()) if current is None else current
    return current // settings.market_hour_seconds


def _noise(key: str, hour: int) -> float:
    """Deterministic hourly noise in ``[-1, 1]`` for one asset."""
    return random.Random(f"{key}|{hour}").uniform(-1.0, 1.0)


def price_at(asset: dict[str, Any], hour: int) -> float:
    """Price of ``asset`` at ``hour`` — deterministic and bounded."""
    base = float(asset["base"])
    blended = (_noise(asset["name"], hour - 1) + _noise(asset["name"], hour)) / 2.0
    raw = base * (1.0 + settings.market_volatility * blended)
    low = base * settings.market_price_min_factor
    high = base * settings.market_price_max_factor
    return round(min(high, max(low, raw)), 2)


def index_at(hour: int) -> float:
    """Market index: mean of prices normalised by their base anchors."""
    total = sum(price_at(asset, hour) / float(asset["base"]) for asset in MARKET_ASSETS)
    return total / len(MARKET_ASSETS)


def find_asset(name: str) -> dict[str, Any]:
    """Resolve a user-typed asset name or symbol to its catalog entry."""
    query = name.strip().casefold()
    if not query:
        raise GameError("نام دارایی را بنویسید. مثال: <code>بورس خرید طلا 500</code>")
    for asset in MARKET_ASSETS:
        if asset["name"].casefold() == query or asset["symbol"].casefold() == query:
            return asset
    partial = [
        a
        for a in MARKET_ASSETS
        if query in a["name"].casefold() or query in a["symbol"].casefold()
    ]
    if len(partial) == 1:
        return partial[0]
    available = "، ".join(a["name"] for a in MARKET_ASSETS)
    raise GameError(f"«{name}» در تابلوی بورس پیدا نشد.\nدارایی‌ها: {available}")


async def _cached_price(asset: dict[str, Any], hour: int) -> float:
    """Price for ``hour``, persisted once so history stays inspectable."""
    row = await db.fetchone(
        "SELECT price FROM market_prices WHERE asset = ? AND hour = ?",
        (asset["name"], hour),
    )
    if row is not None:
        return float(row["price"])
    price = price_at(asset, hour)
    async with db.write() as conn:
        await conn.execute(
            "INSERT OR IGNORE INTO market_prices (asset, hour, price) VALUES (?, ?, ?)",
            (asset["name"], hour, price),
        )
    return price


async def board(current_hour: int | None = None) -> list[dict[str, Any]]:
    """All assets with price and 1-hour change percentage."""
    hour = hour_now() if current_hour is None else current_hour
    rows: list[dict[str, Any]] = []
    for asset in MARKET_ASSETS:
        price = await _cached_price(asset, hour)
        previous = await _cached_price(asset, hour - 1)
        change = ((price - previous) / previous) * 100.0 if previous else 0.0
        rows.append(
            {
                "name": asset["name"],
                "symbol": asset["symbol"],
                "price": price,
                "previous": previous,
                "change_pct": change,
            }
        )
    return rows


async def holdings(user_id: int) -> dict[str, float]:
    """Open positions: asset name -> units held."""
    rows = await db.fetchall(
        "SELECT asset, units FROM holdings WHERE user_id = ? ORDER BY asset",
        (user_id,),
    )
    return {row["asset"]: float(row["units"]) for row in rows}


async def settle_bets(user_id: int, hour: int | None = None) -> list[dict[str, Any]]:
    """Resolve every pending bet whose hour has elapsed (lazy settlement)."""
    current = hour_now() if hour is None else hour
    pending = await db.fetchall(
        "SELECT id, amount, hour FROM market_bets "
        "WHERE user_id = ? AND status = 'pending' AND hour < ? "
        "ORDER BY id",
        (user_id, current),
    )
    settled: list[dict[str, Any]] = []
    for row in pending:
        then = index_at(int(row["hour"]))
        now_index = index_at(current)
        if now_index > then:
            status, payout = "won", 0
        elif now_index < then:
            status, payout = "lost", 0
        else:
            status, payout = "push", int(row["amount"])
        if status == "won":
            payout = int(
                int(row["amount"])
                * (1.0 - settings.market_bet_house_edge)
                / settings.market_bet_win_prob
            )

        async with db.write() as conn:
            await conn.execute(
                "UPDATE market_bets SET status = ?, payout = ?, settled_at = ? "
                "WHERE id = ?",
                (status, payout, int(time.time()), row["id"]),
            )
            if payout > 0:
                await economy.mutate(
                    conn,
                    user_id,
                    credits=payout,
                    kind=ActivityKind.MARKET_BET,
                    ref=f"market:settle:{status}",
                )
        settled.append(
            {
                "id": int(row["id"]),
                "amount": int(row["amount"]),
                "hour": int(row["hour"]),
                "status": status,
                "payout": payout,
            }
        )
    return settled


async def buy(player: Player, name: str, amount: int) -> dict[str, Any]:
    """Buy ``amount`` coins worth of an asset (units stored as real numbers)."""
    asset = find_asset(name)
    if not settings.market_trade_min <= amount <= settings.market_trade_max:
        raise GameError(
            f"مبلغ خرید باید بین {settings.market_trade_min:,} تا "
            f"{settings.market_trade_max:,} سکه باشد."
        )
    hour = hour_now()
    price = await _cached_price(asset, hour)
    units = round(amount / price, 8)

    async with db.write() as conn:
        await economy.mutate(
            conn,
            player.user_id,
            credits=-amount,
            kind=ActivityKind.MARKET_BUY,
            ref=f"market:buy:{asset['symbol']}",
        )
        await conn.execute(
            "INSERT INTO holdings (user_id, asset, units, updated_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT (user_id, asset) DO UPDATE SET "
            "units = units + excluded.units, updated_at = excluded.updated_at",
            (player.user_id, asset["name"], units, int(time.time())),
        )

    return {
        "success": True,
        "asset": asset["name"],
        "amount": amount,
        "units": units,
        "price": price,
        "message": (
            f"📈 <b>خرید {asset['name']} ثبت شد!</b>\n\n"
            f"💵 پرداخت: <b>{amount:,}</b> سکه\n"
            f"🔖 قیمت هر واحد: <b>{price:,.2f}</b>\n"
            f"📦 دریافتی: <b>{units:,.4f}</b> واحد {asset['symbol']}\n\n"
            f"برای فروش: <code>بورس فروش {asset['name']}</code>"
        ),
    }


async def sell(player: Player, name: str) -> dict[str, Any]:
    """Sell the whole position in one asset at the current hour price."""
    asset = find_asset(name)
    hour = hour_now()
    price = await _cached_price(asset, hour)

    async with db.write() as conn:
        cursor = await conn.execute(
            "SELECT units FROM holdings WHERE user_id = ? AND asset = ?",
            (player.user_id, asset["name"]),
        )
        row = await cursor.fetchone()
        await cursor.close()
        units = float(row["units"]) if row else 0.0
        if units <= 0:
            return {
                "success": False,
                "message": (
                    f"❌ هیچ واحد {asset['name']} در سبدت نیست.\n"
                    f"برای خرید: <code>بورس خرید {asset['name']} 500</code>"
                ),
            }

        value = int(units * price)
        if value <= 0:
            return {
                "success": False,
                "message": f"📦 سبد {asset['name']} آنقدر خرد است که ارزش فروش ندارد.",
            }
        # Delete first, then credit: one transaction, both or neither.
        await conn.execute(
            "DELETE FROM holdings WHERE user_id = ? AND asset = ?",
            (player.user_id, asset["name"]),
        )
        await economy.mutate(
            conn,
            player.user_id,
            credits=value,
            kind=ActivityKind.MARKET_SELL,
            ref=f"market:sell:{asset['symbol']}",
        )

    return {
        "success": True,
        "asset": asset["name"],
        "units": units,
        "price": price,
        "value": value,
        "message": (
            f"📉 <b>فروش {asset['name']} انجام شد!</b>\n\n"
            f"📦 فروخته شد: <b>{units:,.4f}</b> واحد\n"
            f"🔖 قیمت فروش: <b>{price:,.2f}</b>\n"
            f"💵 واریزی: <b>+{value:,}</b> سکه"
        ),
    }


async def place_bet(player: Player, amount: int) -> dict[str, Any]:
    """Bet on the index rising at the next hour close (stake escrowed)."""
    if not settings.market_bet_min <= amount <= settings.market_bet_max:
        raise GameError(
            f"مبلغ شرط باید بین {settings.market_bet_min:,} تا "
            f"{settings.market_bet_max:,} سکه باشد."
        )
    hour = hour_now()
    async with db.write() as conn:
        await economy.mutate(
            conn,
            player.user_id,
            credits=-amount,
            kind=ActivityKind.MARKET_BET,
            ref="market:bet",
        )
        cursor = await conn.execute(
            "INSERT INTO market_bets (user_id, amount, hour, created_at) "
            "VALUES (?, ?, ?, ?)",
            (player.user_id, amount, hour, int(time.time())),
        )
        bet_id = cursor.lastrowid
        await cursor.close()

    payout = int(
        amount * (1.0 - settings.market_bet_house_edge) / settings.market_bet_win_prob
    )
    edge_pct = settings.market_bet_house_edge * 100
    return {
        "success": True,
        "bet_id": bet_id,
        "amount": amount,
        "hour": hour,
        "payout": payout,
        "message": (
            f"🎲 <b>شرط روی بورس ثبت شد!</b>\n\n"
            f"💵 مبلغ: <b>{amount:,}</b> سکه (در صندوق امانی)\n"
            f"📈 شرط: تا بسته شدن این ساعت، شاخص <b>بالا</b> برود\n"
            f"💰 در صورت برد: <b>{payout:,}</b> سکه "
            f"(لبه خانه {edge_pct:g}٪)\n"
            f"نتیجه در فراخوانی بعدی <code>بورس</code> اعلام می‌شود."
        ),
    }


async def overview(player: Player, current_hour: int | None = None) -> dict[str, Any]:
    """Board + open positions + settled bets, ready to render."""
    settled = await settle_bets(player.user_id, current_hour)
    board_rows = await board(current_hour)
    positions = await holdings(player.user_id)
    hour = hour_now() if current_hour is None else current_hour
    index_now = index_at(hour)

    lines = [f"📊 <b>تابلوی بورس تیرامیکس — ساعت {hour}</b>", ""]
    for row in board_rows:
        arrow = "🔺" if row["change_pct"] >= 0 else "🔻"
        lines.append(
            f"• <b>{row['name']}</b> ({row['symbol']}) — "
            f"<b>{row['price']:,.2f}</b> "
            f"{arrow} {row['change_pct']:+.2f}٪ (۱ ساعت اخیر)"
        )
    lines.append("")
    lines.append(f"🧭 شاخص کل: <b>{index_now:.4f}</b>")

    if settled:
        lines.append("")
        lines.append("🧾 <b>نتایج شرط‌های تسویه‌شده:</b>")
        for result in settled:
            if result["status"] == "won":
                lines.append(
                    f"• برد ✅ {result['amount']:,} ← "
                    f"<b>+{result['payout']:,}</b> سکه"
                )
            elif result["status"] == "push":
                lines.append(
                    f"• مساوی ➖ {result['amount']:,} سکه برگشت خورد"
                )
            else:
                lines.append(f"• باخت ❌ {result['amount']:,} سکه سوخت")

    lines.append("")
    if positions:
        lines.append("📦 <b>سبد دارایی تو:</b>")
        for asset_name, units in positions.items():
            asset = find_asset(asset_name)
            value = units * price_at(asset, hour)
            lines.append(
                f"• {asset_name}: {units:,.4f} واحد ≈ <b>{int(value):,}</b> سکه"
            )
    else:
        lines.append("📦 سبد دارایی خالی است.")

    lines.append("")
    lines.append("📌 دستورات:")
    lines.append(
        "• <code>بورس خرید [دارایی] [مبلغ]</code> — مثال: بورس خرید طلا 500"
    )
    lines.append("• <code>بورس فروش [دارایی]</code> — فروش کل سبد یک دارایی")
    lines.append(
        "• <code>بورس شرط [مبلغ]</code> — شرط روی بالا رفتن شاخص ساعت بعد"
    )

    return {
        "success": True,
        "hour": hour,
        "board": board_rows,
        "positions": positions,
        "settled": settled,
        "message": "\n".join(lines),
    }
