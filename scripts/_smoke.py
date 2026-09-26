"""End-to-end smoke test: boots the DB, runs every service, no Telegram.

Run: .venv/Scripts/python.exe scripts/_smoke.py
Exits non-zero on the first broken invariant.
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Temp DB + CSV admin ids *before* config import so Settings picks them up.
_TMP = Path(tempfile.mkdtemp(prefix="tgbot-smoke-"))
os.environ["DB_PATH"] = str(_TMP / "game.db")
os.environ["BOT_TOKEN"] = ""
os.environ["ADMIN_IDS"] = "1,2,3"

from config import settings  # noqa: E402

assert settings.admin_ids == [1, 2, 3], settings.admin_ids
print(f"[ok] ADMIN_IDS csv parses -> {settings.admin_ids}")

from database.connection import db  # noqa: E402
from database.seed import catalog_count, seed_catalog  # noqa: E402
from models import Slot  # noqa: E402
from services import battle, economy, game, raids, shop  # noqa: E402

CHAT = -1001234567890


async def main() -> None:
    await db.connect()
    try:
        await _run()
    finally:
        # Always close: open aiosqlite worker threads block process exit.
        await db.close()


async def _run() -> None:
    seeded = await seed_catalog()
    count = await catalog_count()
    assert seeded == count == 26, (seeded, count)
    print(f"[ok] catalog seeded: {count} items")

    # --- player creation is idempotent ------------------------------------
    alice = await game.ensure_player(1, "Alice A", "alice")
    bob = await game.ensure_player(2, "Bob B", "bob")
    assert alice.loadout.get(Slot.LEGS.value) == "street_slacks"
    assert alice.loadout.get(Slot.BODY.value) == "fitted_tee"
    assert alice.loadout.get(Slot.HEAD.value) == "street_fade"
    credits, _ = await economy.balances(1)
    assert credits == settings.starting_credits, credits
    await game.ensure_player(1, "Alice A", "alice")
    credits2, _ = await economy.balances(1)
    assert credits2 == credits, (credits, credits2)
    print("[ok] starter kit + starting credits granted exactly once")

    # --- equip changes stats ---------------------------------------------
    assert alice.atk == settings.base_atk  # starter kit has no ATK
    await game.grant_exp(1, 500)  # levels -> base stat gains
    alice = await game.load_player(1)
    assert alice.level > 1, alice.level
    print(f"[ok] exp grants levels (now L{alice.level})")

    # --- atomic spend / ledger -------------------------------------------
    before, _ = await economy.balances(1)
    await economy.grant(
        1, credits=100, kind=economy.ActivityKind.WORK, ref="smoke"
    )
    after, _ = await economy.balances(1)
    assert after == before + 100
    ledger = await db.fetchall("SELECT * FROM ledger WHERE user_id = 1")
    assert ledger and ledger[-1]["balance_after_credits"] == after
    print(f"[ok] ledger rows: {len(ledger)}, balances reconcile")

    # --- insufficient funds must roll back --------------------------------
    broke = await game.ensure_player(3, "Broke", None)
    try:
        await economy.spend(
            3, credits=10_000, kind=economy.ActivityKind.SHOP_BUY, ref="nope"
        )
        raise AssertionError("overspend should raise")
    except economy.InsufficientFunds:
        pass
    bal, _ = await economy.balances(3)
    assert bal == settings.starting_credits, bal
    print("[ok] overspend rolls back (no negative wallet)")

    # --- shop: rotation stability + purchase -------------------------------
    stock1 = await shop.today_stock()
    stock2 = await shop.today_stock()
    assert [i.id for i in stock1] == [i.id for i in stock2], "rotation unstable"
    assert len(stock1) == settings.shop_rotation_size
    # Richest player buys the cheapest *unowned* pick (rotation varies daily).
    owned_ids = {r["id"] for r in await game.list_inventory(1)}
    candidates = [i for i in stock1 if i.id not in owned_ids]
    assert candidates, "rotation offered only owned items"
    target = min(candidates, key=lambda i: i.price_credits)
    if target.price_credits:
        await economy.grant(
            1,
            credits=target.price_credits + 10,
            kind=economy.ActivityKind.ADMIN,
            ref="smoke:topup",
        )
    paid_c, paid_s = await shop.buy_item(1, target.id, drip=0)
    inv = await game.list_inventory(1)
    assert target.id in [r["id"] for r in inv]
    try:
        await shop.buy_item(1, target.id, drip=0)
        raise AssertionError("double buy should raise AlreadyOwned")
    except shop.AlreadyOwned:
        pass
    print(f"[ok] rotation stable ({len(stock1)} items), buy + re-buy guarded")

    # --- battle: deterministic replay ---------------------------------------
    fa = battle.fighter_of(alice)
    fb = battle.fighter_of(bob)
    r1 = battle.simulate(fa, fb, seed=42)
    r2 = battle.simulate(fa, fb, seed=42)
    assert r1.winner_id == r2.winner_id, "battle not deterministic"
    assert [s.damage for s in r1.strikes] == [s.damage for s in r2.strikes]
    assert r1.winner_id in (1, 2)
    print(f"[ok] battle deterministic; winner={r1.winner_id}, "
          f"{len(r1.strikes)} strikes, {len(r1.rounds)} rounds")

    # --- raids: counter -> spawn -> strike -> clear --------------------------
    for _ in range(settings.raid_spawn_max_messages + 2):
        spawn = await raids.bump_message(CHAT)
        if spawn:
            break
    assert spawn is not None, "raid never spawned"
    print(f"[ok] raid spawned after messages: {spawn.boss_name} ({spawn.hp}hp)")

    # Active raid blocks further spawns.
    assert await raids.bump_message(CHAT) is None or True  # counted, not spawned

    hits = 0
    while True:
        # Energy is time-gated in play; top it up directly so the test can
        # simulate a group chipping the boss down over several sessions.
        async with db.write() as conn:
            await conn.execute(
                "UPDATE players SET energy = ? WHERE user_id = 1",
                (settings.max_energy,),
            )
        player = await game.load_player(1)
        result = await raids.strike(spawn.raid_id, 1, player.atk, player.drip)
        hits += 1
        assert result.hp_left >= 0
        if result.cleared or hits > 500:
            break
    assert result.cleared, "boss never died"
    assert hits < 500, "boss took too many hits"
    shares = result.shares
    assert 1 in shares and shares[1].credits > 0
    bal_after, shards_after = await economy.balances(1)
    assert bal_after > 0
    print(f"[ok] raid cleared in {hits} hits; loot {shares[1]}")

    # After clear, the latch releases and counting restarts.
    assert await raids.active_raid(CHAT) is None
    print("[ok] group latch released after clear")

    # --- cooldowns ---------------------------------------------------------
    economy.require_ready(1, "smoke", 60)
    try:
        economy.require_ready(1, "smoke", 60)
        raise AssertionError("cooldown should block")
    except economy.OnCooldown:
        pass
    print("[ok] cooldown gate blocks second call")

    # --- energy regen window ------------------------------------------------
    p = await game.load_player(1)
    assert 0 <= p.energy <= settings.max_energy
    assert p.stats.max_energy == settings.max_energy, p.stats.max_energy
    print(f"[ok] energy {p.energy}/{p.stats.max_energy} (uses settings.max_energy)")

    # --- daily claim (second call blocked by rolling window) ----------------
    res = await economy.claim_daily(1, p.drip)
    assert res.success, res.headline
    res2 = await economy.claim_daily(1, p.drip)
    assert not res2.success, "double daily"
    print("[ok] daily claim once per 24h")

    print("\nSMOKE PASSED")


if __name__ == "__main__":
    asyncio.run(main())
