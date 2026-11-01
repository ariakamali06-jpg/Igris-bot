"""تست‌های ویژگی‌های جدید بانک مرکزی، دکمه‌های شیشه‌ای بورس، انتقال رکسار و دروازه‌بان."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.types import Message

from config import settings
from database.connection import db
from database.seed import seed_catalog
from handlers import economy, market
from models.enums import ActivityKind
from services import economy as econ_service
from services import game, tiramix


@pytest.fixture(autouse=True)
async def _setup_db():
    await db.connect()
    await seed_catalog()
    yield
    await db.close()


@pytest.mark.asyncio
async def test_transfer_credits_success():
    p1 = await game.ensure_player(111001, "Alice", "alice")
    p2 = await game.ensure_player(111002, "Bob", "bob")

    async with db.write() as conn:
        await conn.execute(
            "UPDATE players SET onboarding_completed = 1 WHERE user_id IN (111001, 111002)"
        )

    # Grant p1 1000 rexars
    await econ_service.grant(p1.user_id, credits=1000, kind=ActivityKind.ADMIN)

    res = await tiramix.transfer_credits(p1.user_id, p2.user_id, 400)
    assert res["success"] is True
    assert "انتقال موفق وجه" in res["message"]
    assert "400" in res["message"]
    assert "رکسار" in res["message"]

    c1, _ = await econ_service.balances(p1.user_id)
    c2, _ = await econ_service.balances(p2.user_id)
    assert c1 == 850  # 250 initial + 1000 - 400
    assert c2 == 650  # 250 initial + 400


@pytest.mark.asyncio
async def test_transfer_credits_guards():
    p1 = await game.ensure_player(111003, "Charlie", "charlie")
    p2 = await game.ensure_player(111004, "Dave", "dave")

    # Target not onboarded
    async with db.write() as conn:
        await conn.execute("UPDATE players SET onboarding_completed = 1 WHERE user_id = 111003")
        await conn.execute("UPDATE players SET onboarding_completed = 0 WHERE user_id = 111004")

    res_not_onboarded = await tiramix.transfer_credits(p1.user_id, p2.user_id, 100)
    assert res_not_onboarded["success"] is False
    assert "شناسنامه نساخته است" in res_not_onboarded["message"]

    # Self transfer
    res_self = await tiramix.transfer_credits(p1.user_id, p1.user_id, 100)
    assert res_self["success"] is False
    assert "نمی‌توانید به خودتان" in res_self["message"]

    # Insufficient funds
    async with db.write() as conn:
        await conn.execute("UPDATE players SET onboarding_completed = 1 WHERE user_id = 111004")
    res_poor = await tiramix.transfer_credits(p1.user_id, p2.user_id, 999999)
    assert res_poor["success"] is False
    assert "موجودی کیف پول شما کافی نیست" in res_poor["message"]


@pytest.mark.asyncio
async def test_bank_markup_and_actions():
    p = await game.ensure_player(111005, "Eve", "eve")
    async with db.write() as conn:
        await conn.execute("UPDATE players SET onboarding_completed = 1 WHERE user_id = 111005")

    text, markup = await economy._render_bank_view(p)
    assert "بانک مرکزی شهر تیرامیکس" in text
    assert "رکسار" in text
    assert "شارد" not in text

    btn_texts = [btn.text for row in markup.inline_keyboard for btn in row]
    assert any("واریز" in t for t in btn_texts)
    assert any("برداشت" in t for t in btn_texts)
    assert any("وام" in t for t in btn_texts)
    assert any("تسویه" in t for t in btn_texts)
    assert any("کارت به کارت" in t for t in btn_texts)


@pytest.mark.asyncio
async def test_market_markup_and_buttons():
    markup = market._market_markup()
    btn_texts = [btn.text for row in markup.inline_keyboard for btn in row]
    assert any("طلا" in t for t in btn_texts)
    assert any("نفت" in t for t in btn_texts)
    assert any("الماس" in t for t in btn_texts)
    assert any("بیت‌کوین" in t for t in btn_texts)
    assert any("شاخص کل" in t for t in btn_texts)
    assert any("فروش" in t for t in btn_texts)


@pytest.mark.asyncio
async def test_street_boss_disabled_by_default():
    assert settings.street_boss_enabled is False

    # Calling /boss command informs player it is disabled
    msg = MagicMock(spec=Message)
    msg.reply = AsyncMock()
    await raids_cmd_boss(msg)
    msg.reply.assert_called_once()
    assert "غیرفعال است" in msg.reply.call_args[0][0]


async def raids_cmd_boss(msg):
    from handlers.raids import cmd_boss
    await cmd_boss(msg)
