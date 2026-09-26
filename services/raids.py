"""Chat-driven raids: message counter, boss spawns, shared-HP strikes.

The group's message counter lives in ``group_state`` (persisted), so the
spawn cadence survives restarts.  A raid is a single row with one HP pool;
every strike is recorded in ``raid_participants`` inside the *same*
transaction that decrements HP, which is what makes damage totals and loot
splits impossible to desync.

Loot is split pro-rata by damage with a guaranteed minimum share for anyone
who landed at least one hit, persisted to ``raid_loot`` before the raid is
marked cleared.
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass
from typing import Any

from config import settings
from database.connection import db
from models import ActivityKind
from services import economy
from services.game import GameError, grant_exp_conn, now, spend_energy_conn

logger = logging.getLogger(__name__)


BOSSES: tuple[tuple[str, str, str], ...] = (
    ("Rogue Shadow", "shadow", "It peels off the wall and swings first."),
    ("Neon Wraith", "wraith", "A glitch in the alley with a grudge."),
    ("Asphalt Revenant", "revenant", "Rises from the crosswalk, still steaming."),
    ("The Debt Collector", "collector", "Wears a trench coat. Carries your invoice."),
    ("Void Hound", "hound", "Three eyes, no leash, no fear."),
)


@dataclass(frozen=True, slots=True)
class SpawnResult:
    """Returned when the counter crosses the threshold."""

    raid_id: int
    boss_name: str
    boss_key: str
    taunt: str
    hp: int
    max_hp: int


@dataclass(frozen=True, slots=True)
class StrikeResult:
    damage: int
    critical: bool
    hp_left: int
    max_hp: int
    cleared: bool
    participants: int
    shares: dict[int, LootShare]


@dataclass(frozen=True, slots=True)
class LootShare:
    credits: int
    shards: int
    exp: int


# ---------------------------------------------------------------------------
# Message counter
# ---------------------------------------------------------------------------


async def bump_message(chat_id: int) -> SpawnResult | None:
    """Count one group message; spawn a boss when the window closes.

    Returns a :class:`SpawnResult` exactly once per raid — the transaction
    that flips the counter also creates the raid row, so two concurrent
    messages can never double-spawn.
    """
    async with db.write() as conn:
        await conn.execute(
            "INSERT OR IGNORE INTO group_state (chat_id) VALUES (?)", (chat_id,)
        )
        cursor = await conn.execute(
            "SELECT message_count, messages_to_next_raid, active_raid_id "
            "FROM group_state WHERE chat_id = ?",
            (chat_id,),
        )
        state_row = await cursor.fetchone()
        await cursor.close()
        if state_row is None:  # pragma: no cover - INSERT OR IGNORE guarantees it
            raise GameError("group state missing after insert")
        state = dict(state_row)

        # An active raid holds the counter hostage (no stacking bosses).
        if state["active_raid_id"] is not None:
            check = await conn.execute(
                "SELECT status FROM raids WHERE id = ?",
                (state["active_raid_id"],),
            )
            row = await check.fetchone()
            await check.close()
            if row is not None and row["status"] == "active":
                await conn.execute(
                    "UPDATE group_state SET message_count = message_count + 1 "
                    "WHERE chat_id = ?",
                    (chat_id,),
                )
                return None
            # Raid expired/finished without an explicit clear: release the latch.
            await conn.execute(
                "UPDATE group_state SET active_raid_id = NULL WHERE chat_id = ?",
                (chat_id,),
            )
            state["active_raid_id"] = None

        count = state["message_count"] + 1
        target = state["messages_to_next_raid"]

        if target <= 0:
            # (Re)arm the window: the first message after a clear picks the target.
            target = random.randint(
                settings.raid_spawn_min_messages, settings.raid_spawn_max_messages
            )

        if count < target:
            await conn.execute(
                "UPDATE group_state SET message_count = ?, messages_to_next_raid = ? "
                "WHERE chat_id = ?",
                (count, target, chat_id),
            )
            return None

        # --- threshold crossed: spawn the boss -----------------------------
        boss_name, boss_key, taunt = random.choice(BOSSES)
        base_level = 1 + count // max(1, settings.raid_spawn_max_messages)
        hp = settings.raid_hp_base + settings.raid_hp_per_level * base_level
        cursor = await conn.execute(
            """
            INSERT INTO raids (chat_id, boss_name, boss_key, hp, max_hp,
                               status, spawned_at)
            VALUES (?, ?, ?, ?, ?, 'active', ?)
            """,
            (chat_id, boss_name, boss_key, hp, hp, now()),
        )
        raid_id = cursor.lastrowid
        await cursor.close()
        if raid_id is None:  # pragma: no cover - INSERT into AUTOINCREMENT pk
            raise GameError("raid insert produced no id")
        await conn.execute(
            "UPDATE group_state SET message_count = 0, messages_to_next_raid = 0,"
            " active_raid_id = ?, last_raid_at = ? WHERE chat_id = ?",
            (raid_id, now(), chat_id),
        )
        logger.info("raid %s spawned in chat %s: %s", raid_id, chat_id, boss_name)
        return SpawnResult(raid_id, boss_name, boss_key, taunt, hp, hp)


async def active_raid(chat_id: int) -> dict[str, Any] | None:
    async with db.read() as conn:
        cursor = await conn.execute(
            "SELECT r.* FROM raids r JOIN group_state g ON g.active_raid_id = r.id "
            "WHERE g.chat_id = ? AND r.status = 'active'",
            (chat_id,),
        )
        row = await cursor.fetchone()
        await cursor.close()
    return dict(row) if row else None


# ---------------------------------------------------------------------------
# Strikes
# ---------------------------------------------------------------------------


async def strike(raid_id: int, user_id: int, atk: int, drip: int) -> StrikeResult:
    """One energy-gated hit against the shared pool.

    Damage scales with ATK with a small variance; drip adds a chance to crit
    (doubling the hit).  HP, participant damage, loot split and the group
    latch all commit in this one transaction.
    """
    async with db.write() as conn:
        cursor = await conn.execute(
            "SELECT hp, max_hp, status FROM raids WHERE id = ?", (raid_id,)
        )
        raid = await cursor.fetchone()
        await cursor.close()
        if raid is None:
            raise GameError("that boss has already dissolved")
        if raid["status"] != "active":
            raise GameError("that fight is over")

        await spend_energy_conn(conn, user_id, settings.raid_dps_energy_cost)

        variance = random.uniform(0.8, 1.25)
        damage = max(1, int(atk * variance))
        critical = random.random() < min(0.5, 0.05 + drip * 0.003)
        if critical:
            damage *= 2

        hp_left = max(0, raid["hp"] - damage)
        cleared = hp_left == 0

        await conn.execute(
            "UPDATE raids SET hp = ?, status = ?, cleared_at = ? WHERE id = ?",
            (
                hp_left,
                "cleared" if cleared else "active",
                now() if cleared else None,
                raid_id,
            ),
        )
        await conn.execute(
            """
            INSERT INTO raid_participants (raid_id, user_id, damage, hits, last_hit_at)
            VALUES (?, ?, ?, 1, ?)
            ON CONFLICT (raid_id, user_id) DO UPDATE SET
                damage = damage + excluded.damage,
                hits = hits + 1,
                last_hit_at = excluded.last_hit_at
            """,
            (raid_id, user_id, damage, now()),
        )

        shares: dict[int, LootShare] = {}
        if cleared:
            # Unlatch the group immediately so the next window can arm.
            await conn.execute(
                "UPDATE group_state SET active_raid_id = NULL WHERE active_raid_id = ?",
                (raid_id,),
            )
            shares = await _split_loot_conn(conn, raid_id, raid["max_hp"])

        count = await conn.execute(
            "SELECT COUNT(*) AS n FROM raid_participants WHERE raid_id = ?",
            (raid_id,),
        )
        crow = await count.fetchone()
        await count.close()

    return StrikeResult(
        damage=damage,
        critical=critical,
        hp_left=hp_left,
        max_hp=raid["max_hp"],
        cleared=cleared,
        participants=crow["n"] if crow else 0,
        shares=shares,
    )


async def _split_loot_conn(conn, raid_id: int, max_hp: int) -> dict[int, LootShare]:
    """Pro-rata credit split by damage + flat shards/EXP for every hitter."""
    cursor = await conn.execute(
        "SELECT user_id, damage FROM raid_participants "
        "WHERE raid_id = ? AND damage > 0 ORDER BY damage DESC",
        (raid_id,),
    )
    rows = list(await cursor.fetchall())
    await cursor.close()
    if not rows:
        return {}

    credit_pool = max(50, int(max_hp * settings.raid_loot_credits_per_percent / 100))
    total_damage = sum(row["damage"] for row in rows) or 1
    shard_share = max(1, settings.raid_loot_shards // len(rows))
    shares: dict[int, LootShare] = {}
    allocated = 0

    for index, row in enumerate(rows):
        if index == len(rows) - 1:
            credits = max(1, credit_pool - allocated)  # last picker eats rounding dust
        else:
            credits = max(1, credit_pool * row["damage"] // total_damage)
            allocated += credits
        shards = settings.raid_loot_shards if index == 0 else shard_share
        shares[row["user_id"]] = LootShare(
            credits=credits, shards=shards, exp=settings.raid_loot_exp
        )

    for user_id, share in shares.items():
        await conn.execute(
            """
            INSERT INTO raid_loot (raid_id, user_id, credits, soul_shards)
            VALUES (?, ?, ?, ?)
            ON CONFLICT (raid_id, user_id) DO UPDATE SET
                credits = excluded.credits,
                soul_shards = excluded.soul_shards
            """,
            (raid_id, user_id, share.credits, share.shards),
        )
        await economy.mutate(
            conn,
            user_id,
            credits=share.credits,
            shards=share.shards,
            kind=ActivityKind.RAID,
            ref=f"raid:{raid_id}",
        )
        await grant_exp_conn(conn, user_id, share.exp)

    return shares


async def leaderboard_damage(raid_id: int, limit: int = 5) -> list[dict[str, Any]]:
    async with db.read() as conn:
        cursor = await conn.execute(
            "SELECT user_id, damage, hits FROM raid_participants "
            "WHERE raid_id = ? ORDER BY damage DESC LIMIT ?",
            (raid_id, limit),
        )
        rows = [dict(row) for row in await cursor.fetchall()]
        await cursor.close()
    return rows
