"""Tests for Tiramix Life Simulator service."""

import pytest
import time
from database.connection import db
from models.player import Player
from services import economy, tiramix


@pytest.fixture(autouse=True)
async def _setup_db():
    await db.connect()
    # Ensure fresh test players
    async with db.write() as conn:
        await conn.execute("DELETE FROM inventory WHERE user_id IN (1001, 1002, 1003)")
        await conn.execute("DELETE FROM loadout WHERE user_id IN (1001, 1002, 1003)")
        await conn.execute("DELETE FROM purchases WHERE user_id IN (1001, 1002, 1003)")
        await conn.execute("DELETE FROM ledger WHERE user_id IN (1001, 1002, 1003)")
        await conn.execute("DELETE FROM wallets WHERE user_id IN (1001, 1002, 1003)")
        await conn.execute("DELETE FROM clans WHERE leader_id IN (1001, 1002, 1003)")
        await conn.execute("DELETE FROM players WHERE user_id IN (1001, 1002, 1003)")
        
        # Insert test players
        for uid, name in [(1001, "PlayerOne"), (1002, "PlayerTwo"), (1003, "PlayerThree")]:
            await conn.execute(
                "INSERT INTO players (user_id, display_name, level, education_level, job, gender) "
                "VALUES (?, ?, 3, 1, 'صندوقدار هایپرمارکت', 'female')",
                (uid, name),
            )
            await conn.execute(
                "INSERT INTO wallets (user_id, credits, soul_shards) VALUES (?, 10000, 5)",
                (uid,),
            )
    yield
    await db.close()


@pytest.mark.asyncio
async def test_work_and_cooldown():
    p = Player(user_id=1001, display_name="PlayerOne", education_level=1, job="صندوقدار هایپرمارکت")
    res = await tiramix.execute_work(p)
    assert res["success"] is True
    assert res["total_earned"] > 0

    # Repeat should fail due to cooldown
    p.last_work_time = int(time.time())
    res2 = await tiramix.execute_work(p)
    assert res2["success"] is False
    assert res2["error"] == "cooldown"


@pytest.mark.asyncio
async def test_study_and_tuition():
    p = Player(user_id=1001, display_name="PlayerOne", education_level=0)
    res = await tiramix.execute_study(p)
    assert res["success"] is True
    assert res["new_level"] == 1
    assert "دیپلم" in res["new_title"]


@pytest.mark.asyncio
async def test_banking_deposit_and_withdraw():
    p = Player(user_id=1001, display_name="PlayerOne", bank_balance=500)
    # Deposit 1000
    res_dep = await tiramix.deposit_bank(p, 1000)
    assert res_dep["success"] is True
    assert res_dep["bank_balance"] == 1500

    # Withdraw 500
    p.bank_balance = 1500
    res_wit = await tiramix.withdraw_bank(p, 500)
    assert res_wit["success"] is True
    assert res_wit["bank_balance"] == 1000


@pytest.mark.asyncio
async def test_bank_loan_and_repay():
    p = Player(user_id=1001, display_name="PlayerOne", level=3, bank_balance=1000)
    res_loan = await tiramix.apply_bank_loan(p, 2000)
    assert res_loan["success"] is True
    assert res_loan["total_due"] == 2300  # 2000 + 15%

    p.loan_amount = 2300
    res_repay = await tiramix.repay_bank_loan(p, 1000)
    assert res_repay["success"] is True
    assert res_repay["remaining_due"] == 1300


@pytest.mark.asyncio
async def test_theft_mechanic():
    thief = Player(user_id=1001, display_name="Thief", level=5, education_level=2)
    victim = Player(user_id=1002, display_name="Victim", level=2, education_level=0)

    res = await tiramix.attempt_theft(thief, victim)
    # Result must be either success (looted) or caught (fine or jail)
    assert res["success"] in (True, False)
    if res["success"]:
        assert res["stolen"] > 0
    else:
        assert res["punishment"] in ("fine", "jail")


@pytest.mark.asyncio
async def test_marriage_and_intimacy():
    p1 = Player(user_id=1001, display_name="P1", level=3, gender="female")
    p2 = Player(user_id=1002, display_name="P2", level=3, gender="male")

    m_res = await tiramix.marry_citizens(p1, p2)
    assert m_res["success"] is True

    p1.spouse_id = 1002
    p2.spouse_id = 1001
    int_res = await tiramix.execute_intimacy(p1, p2)
    assert int_res["success"] is True


@pytest.mark.asyncio
async def test_clan_creation():
    leader = Player(user_id=1001, display_name="Leader")
    res = await tiramix.create_new_clan(leader, group_id=-100123456, clan_name="بکس تیرامیکس")
    assert res["success"] is True
    assert res["clan_name"] == "بکس تیرامیکس"
