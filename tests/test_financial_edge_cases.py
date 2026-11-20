"""لبه‌های مالی: مبالغ نامعتبر، کسری موجودی، همزمانی تراکنش‌ها و رُند کردن (دیتابیس موقت).

تمرکز روی مسیرهایی که در تست‌های عادی پوشش داده نشده‌اند:
- هرگز موجودی منفی نمی‌شود و تراکنش ناموفق کامل rollback می‌شود
- مسابقهٔ همزمانی (double-spend) در انتقال وجه
- مرزهای مبلغ در بورس و بازارچه و قمار
- ریاضیات محض درآمد املاک و تخفیف دریپ
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path

import pytest
import pytest_asyncio

os.environ.setdefault(
    "DB_PATH", str(Path(tempfile.mkdtemp(prefix="tgbot-pytest-")) / "test.db")
)

from config import settings  # noqa: E402
from database.connection import db  # noqa: E402
from database.seed import seed_catalog  # noqa: E402
from models.enums import ActivityKind  # noqa: E402
from models.player import Player  # noqa: E402
from services import economy, market, properties, tiramix  # noqa: E402
from services.game import GameError, InsufficientFunds, ensure_player  # noqa: E402

BASE_ID = 730_000


@pytest_asyncio.fixture(autouse=True)
async def _database() -> None:
    if db._closed:  # noqa: SLF001
        await db.connect()
        await seed_catalog()
    yield
    await db.close()


async def _player(offset: int) -> Player:
    player = await ensure_player(BASE_ID + offset, f"Fin {offset}", None)
    async with db.write() as conn:
        await conn.execute(
            "UPDATE players SET onboarding_completed = 1 WHERE user_id = ?",
            (player.user_id,),
        )
        # صفر کردن کیف: موجودی اولیهٔ بازی نباید محاسبات مالی تست را به‌هم بزند
        await conn.execute(
            "UPDATE wallets SET credits = 0, soul_shards = 0 WHERE user_id = ?",
            (player.user_id,),
        )
    return player


# ---------------------------------------------------------------------------
# ۱. کسری موجودی: هرگز منفی نشو، تراکنش ناموفق کامل برگردد
# ---------------------------------------------------------------------------


async def test_insufficient_spend_rolls_back_and_keeps_balance():
    player = await _player(1)
    await economy.grant(player.user_id, credits=100, kind=ActivityKind.ADMIN)

    with pytest.raises(InsufficientFunds):
        async with db.write() as conn:
            await economy.mutate(
                conn, player.user_id, credits=-150, kind=ActivityKind.SHOP_BUY
            )

    cash, _ = await economy.balances(player.user_id)
    assert cash == 100  # هیچ تغییری ثبت نشده
    rows = await db.fetchall(
        "SELECT * FROM ledger WHERE user_id = ? AND credits_delta = -150",
        (player.user_id,),
    )
    assert rows == []  # ردیف لجر هم برای تلاش ناموفق نوشته نشده


async def test_spend_wrapper_rejects_overdraft():
    player = await _player(2)
    await economy.grant(player.user_id, credits=50, kind=ActivityKind.ADMIN)
    with pytest.raises(InsufficientFunds):
        await economy.spend(player.user_id, credits=51, kind=ActivityKind.SHOP_BUY)
    cash, _ = await economy.balances(player.user_id)
    assert cash == 50


# ---------------------------------------------------------------------------
# ۲. انتقال وجه: اعتبارسنجی مبلغ/هدف + عدم دوبار‌خرجی در همزمانی
# ---------------------------------------------------------------------------


async def test_transfer_rejects_zero_negative_and_self():
    a = await _player(3)
    b = await _player(4)
    await economy.grant(a.user_id, credits=500, kind=ActivityKind.ADMIN)

    for bad in (0, -10):
        res = await tiramix.transfer_credits(a.user_id, b.user_id, bad)
        assert res["success"] is False

    res = await tiramix.transfer_credits(a.user_id, a.user_id, 10)
    assert res["success"] is False

    cash, _ = await economy.balances(a.user_id)
    assert cash == 500  # هیچ‌کدام اجرا نشده‌اند


async def test_transfer_requires_target_identity():
    a = await _player(5)
    await economy.grant(a.user_id, credits=100, kind=ActivityKind.ADMIN)
    # هدف بدون شناسنامه
    await ensure_player(BASE_ID + 6, "NoDoc", None)
    res = await tiramix.transfer_credits(a.user_id, BASE_ID + 6, 10)
    assert res["success"] is False
    assert "شناسنامه" in res["message"]


async def test_transfer_insufficient_keeps_both_balances():
    a = await _player(7)
    b = await _player(8)
    await economy.grant(a.user_id, credits=100, kind=ActivityKind.ADMIN)

    res = await tiramix.transfer_credits(a.user_id, b.user_id, 150)
    assert res["success"] is False
    assert "کافی نیست" in res["message"]

    ca, _ = await economy.balances(a.user_id)
    cb, _ = await economy.balances(b.user_id)
    assert (ca, cb) == (100, 0)
    rows = await db.fetchall(
        "SELECT * FROM ledger WHERE user_id IN (?, ?) AND kind = ?",
        (a.user_id, b.user_id, ActivityKind.BANK_TRANSFER.value),
    )
    assert rows == []  # انتقال ناموفق هیچ ردی لجر نگذاشته


async def test_transfer_success_moves_exact_amount_with_double_ledger():
    a = await _player(9)
    b = await _player(10)
    await economy.grant(a.user_id, credits=500, kind=ActivityKind.ADMIN)

    res = await tiramix.transfer_credits(a.user_id, b.user_id, 200)
    assert res["success"] is True

    ca, _ = await economy.balances(a.user_id)
    cb, _ = await economy.balances(b.user_id)
    assert (ca, cb) == (300, 200)

    rows = await db.fetchall(
        "SELECT * FROM ledger WHERE user_id IN (?, ?) AND kind = ?",
        (a.user_id, b.user_id, ActivityKind.BANK_TRANSFER.value),
    )
    deltas = sorted(r["credits_delta"] for r in rows)
    assert deltas == [-200, 200]


async def test_concurrent_transfers_cannot_double_spend():
    """دو انتقال همزمان بیش از موجودی: دقیقاً یکی موفق، موجودی هرگز منفی نمی‌شود."""
    a = await _player(11)
    b = await _player(12)
    await economy.grant(a.user_id, credits=100, kind=ActivityKind.ADMIN)

    results = await asyncio.gather(
        tiramix.transfer_credits(a.user_id, b.user_id, 60),
        tiramix.transfer_credits(a.user_id, b.user_id, 60),
        return_exceptions=True,
    )

    successes = [r for r in results if isinstance(r, dict) and r.get("success")]
    assert len(successes) == 1

    ca, _ = await economy.balances(a.user_id)
    cb, _ = await economy.balances(b.user_id)
    assert ca == 40
    assert cb == 60


# ---------------------------------------------------------------------------
# ۳. بورس: مرزهای مبلغ، نام ناشناخته، rollback و چرخه خرید/فروش
# ---------------------------------------------------------------------------


async def test_market_buy_rejects_out_of_band_amounts_and_unknown_asset():
    p = await _player(13)
    await economy.grant(p.user_id, credits=1_000_000, kind=ActivityKind.ADMIN)
    player = await ensure_player(p.user_id, "Fin 13", None)

    with pytest.raises(GameError):
        await market.buy(player, "طلا", settings.market_trade_min - 1)
    with pytest.raises(GameError):
        await market.buy(player, "طلا", settings.market_trade_max + 1)
    with pytest.raises(GameError):
        await market.buy(player, "دارایی‌ناشناخته", settings.market_trade_min)

    cash, _ = await economy.balances(p.user_id)
    assert cash == 1_000_000  # هیچ خریدی انجام نشده
    assert await market.holdings(p.user_id) == {}


async def test_market_buy_insufficient_rolls_back_position():
    p = await _player(14)
    await economy.grant(p.user_id, credits=5, kind=ActivityKind.ADMIN)
    player = await ensure_player(p.user_id, "Fin 14", None)

    with pytest.raises(InsufficientFunds):
        await market.buy(player, "نفت", settings.market_trade_min)

    cash, _ = await economy.balances(p.user_id)
    assert cash == 5
    assert await market.holdings(p.user_id) == {}


async def test_market_buy_sell_round_trip_respects_rounding():
    p = await _player(15)
    amount = settings.market_trade_min
    await economy.grant(p.user_id, credits=amount, kind=ActivityKind.ADMIN)
    player = await ensure_player(p.user_id, "Fin 15", None)

    await market.buy(player, "طلا", amount)
    cash, _ = await economy.balances(p.user_id)
    assert cash == 0
    assert await market.holdings(p.user_id)

    res = await market.sell(player, "طلا")
    assert res["success"] is True
    # اختلاف فروش فقط می‌تواند ناشی از رُند کردن ۸ رقمی واحدها باشد
    assert 0 < res["value"] <= amount
    assert amount - res["value"] <= 1

    cash, _ = await economy.balances(p.user_id)
    assert cash == res["value"]
    assert await market.holdings(p.user_id) == {}


async def test_market_sell_without_position_is_soft_failure():
    p = await _player(16)
    player = await ensure_player(p.user_id, "Fin 16", None)
    res = await market.sell(player, "الماس")
    assert res["success"] is False
    cash, _ = await economy.balances(p.user_id)
    assert cash == 0


# ---------------------------------------------------------------------------
# ۴. املاک: نام ناشناخته، کسری موجودی (rollback)، خرید تکراری
# ---------------------------------------------------------------------------


async def test_property_buy_edges():
    p = await _player(17)
    player = await ensure_player(p.user_id, "Fin 17", None)
    entry = properties.catalog(properties.KIND_PROPERTY)[0]

    with pytest.raises(GameError):
        await properties.buy_asset(player, properties.KIND_PROPERTY, "ملک‌ناشناخته")

    await economy.grant(p.user_id, credits=entry["price"] - 1, kind=ActivityKind.ADMIN)
    with pytest.raises(InsufficientFunds):
        await properties.buy_asset(player, properties.KIND_PROPERTY, entry["name"])
    owned = await properties.owned_rows(p.user_id)
    assert owned == []
    cash, _ = await economy.balances(p.user_id)
    assert cash == entry["price"] - 1  # رول‌بک کامل

    await economy.grant(p.user_id, credits=1, kind=ActivityKind.ADMIN)
    res = await properties.buy_asset(player, properties.KIND_PROPERTY, entry["name"])
    assert res["success"] is True

    dup = await properties.buy_asset(player, properties.KIND_PROPERTY, entry["name"])
    assert dup["success"] is False  # خرید تکراری پول کم نمی‌کند
    cash, _ = await economy.balances(p.user_id)
    assert cash == 0


# ---------------------------------------------------------------------------
# ۵. ریاضیات محض: درآمد انباشته، تخفیف دریپ، مرزهای شرط‌بندی
# ---------------------------------------------------------------------------


def test_pending_income_edges():
    interval = settings.property_income_interval
    per_tick = properties.income_per_tick(10, 1)

    assert properties.pending_income(10, 1, 0, 10**9) == 0  # بدون جمع‌آوری قبلی
    assert properties.pending_income(10, 1, 100, 50) == 0  # زمان معکوس
    assert properties.pending_income(10, 1, 100, 100 + interval - 1) == 0  # هنوز یک تیک نشده
    assert properties.pending_income(10, 1, 100, 100 + interval) == per_tick

    # سقف تیک‌های انباشته
    far = 100 + interval * (settings.property_max_pending_ticks + 500)
    assert (
        properties.pending_income(10, 1, 100, far)
        == settings.property_max_pending_ticks * per_tick
    )


def test_discounted_price_never_zero_and_capped():
    assert economy.discounted_price(0, 50) == 0
    assert economy.discounted_price(100, 0) == 100

    # سقف تخفیف رعایت می‌شود، حتی با دریپ نامتناهی
    capped = max(1, round(100 * (1 - settings.drip_discount_cap)))
    assert economy.discounted_price(100, 10**6) == capped

    # قیمت کوچک هرگز به صفر نمی‌رسد
    assert economy.discounted_price(1, 10**6) == 1


def test_validate_bet_bounds():
    with pytest.raises(GameError):
        economy.validate_bet(settings.casino_min_bet - 1)
    with pytest.raises(GameError):
        economy.validate_bet(settings.casino_max_bet + 1)
    assert economy.validate_bet(settings.casino_min_bet) == settings.casino_min_bet


def test_upgrade_cost_is_monotonic():
    costs = [properties.upgrade_cost(lv) for lv in range(1, settings.property_max_level + 1)]
    assert costs == sorted(costs)
    assert all(c > 0 for c in costs)
