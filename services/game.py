"""Player lifecycle: creation, hydration, progression and equipment.

Every mutation runs inside ``db.write()`` (``BEGIN IMMEDIATE`` + process-wide
lock), so a player row, their wallet, inventory and loadout always move
together - no half-applied purchases or double-spent energy.

Helper functions suffixed ``_conn`` take an open transaction so composites
(e.g. shop buy = wallet debit + inventory insert + ledger row) can share one
atomic section without deadlocking on the non-reentrant write lock.
"""

from __future__ import annotations

import logging
import time

import aiosqlite

from config import settings
from database.connection import db
from database.items import BACKGROUND_KEYS, ITEMS_BY_ID, starter_kit_ids
from models import Player, Slot, StatBlock
from services.compositor import RenderRequest

logger = logging.getLogger(__name__)


class GameError(Exception):
    """Domain error safe to show to a player."""


class InsufficientEnergy(GameError):
    def __init__(self, needed: int, have: int) -> None:
        super().__init__(f"needs {needed} energy, you have {have}")
        self.needed = needed
        self.have = have


class InsufficientFunds(GameError):
    def __init__(self, what: str, needed: int, have: int) -> None:
        super().__init__(f"needs {needed} {what}, you have {have}")
        self.what = what
        self.needed = needed
        self.have = have


class NotOwned(GameError):
    def __init__(self, item_id: str) -> None:
        super().__init__(f"you do not own {item_id}")
        self.item_id = item_id


def now() -> int:
    return int(time.time())


def exp_to_next(level: int) -> int:
    """EXP required to advance *from* ``level`` to ``level + 1``."""
    return int(settings.exp_per_level * (settings.level_exp_growth ** (level - 1)))


# ---------------------------------------------------------------------------
# Energy
# ---------------------------------------------------------------------------


def _regen_window(energy: int, updated_at: int, current: int) -> tuple[int, int]:
    """Apply passive regen up to ``current``; returns (energy, updated_at).

    Fractional regen is banked by advancing the timestamp only by whole
    intervals, so no regen is lost across restarts.
    """
    if energy >= settings.max_energy:
        return settings.max_energy, current
    elapsed = current - updated_at
    if elapsed < settings.energy_regen_interval_seconds:
        return energy, updated_at
    intervals = int(elapsed // settings.energy_regen_interval_seconds)
    gained = int(intervals * settings.energy_regen_per_minute)
    if gained <= 0:
        return energy, updated_at
    new_energy = min(settings.max_energy, energy + gained)
    consumed = int(intervals * settings.energy_regen_interval_seconds)
    return new_energy, updated_at + consumed


# ---------------------------------------------------------------------------
# Creation / hydration
# ---------------------------------------------------------------------------


async def ensure_player(
    user_id: int,
    display_name: str,
    username: str | None = None,
) -> Player:
    """Create the player (and wallet + starter kit) if new, then hydrate.

    Idempotent and race-free: everything happens in one immediate
    transaction, so two concurrent /start calls cannot double-grant the
    starter kit or starting credits.
    """
    current = now()
    async with db.write() as conn:
        await conn.execute(
            """
            INSERT INTO players (user_id, username, display_name, level, exp,
                                 energy, energy_updated_at, base_atk, base_def,
                                 base_drip, created_at, last_seen)
            VALUES (?, ?, ?, 1, 0, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (user_id) DO UPDATE SET
                username = excluded.username,
                display_name = excluded.display_name,
                last_seen = excluded.last_seen
            """,
            (
                user_id,
                username,
                display_name,
                settings.base_energy,
                current,
                settings.base_atk,
                settings.base_def,
                settings.base_drip,
                current,
                current,
            ),
        )
        cursor = await conn.execute(
            "SELECT 1 FROM wallets WHERE user_id = ?", (user_id,)
        )
        fresh = await cursor.fetchone() is None
        await cursor.close()
        if fresh:
            await conn.execute(
                "INSERT INTO wallets (user_id, credits, soul_shards) VALUES (?, ?, ?)",
                (user_id, settings.starting_credits, settings.starting_soul_shards),
            )
            for item_id in starter_kit_ids():
                await _grant_item_conn(conn, user_id, item_id, current)
            logger.info("created player %s with starter kit", user_id)
            await _equip_defaults_conn(conn, user_id)
        return await _load_conn(conn, user_id, current)


async def load_player(user_id: int, display_name: str = "Unknown") -> Player | None:
    """Hydrate a player with gear stats, or ``None`` if they never started."""
    current = now()
    async with db.write() as conn:
        cursor = await conn.execute(
            "SELECT 1 FROM players WHERE user_id = ?", (user_id,)
        )
        exists = await cursor.fetchone() is not None
        await cursor.close()
        if not exists:
            return None
        await conn.execute(
            "UPDATE players SET last_seen = ? WHERE user_id = ?", (current, user_id)
        )
        return await _load_conn(conn, user_id, current, display_name)


async def _load_conn(
    conn: aiosqlite.Connection,
    user_id: int,
    current: int,
    display_name: str | None = None,
) -> Player:
    cursor = await conn.execute("SELECT * FROM players WHERE user_id = ?", (user_id,))
    row = await cursor.fetchone()
    await cursor.close()
    if row is None:  # pragma: no cover - callers check existence first
        raise GameError(f"player {user_id} vanished mid-transaction")

    energy, updated_at = _regen_window(row["energy"], row["energy_updated_at"], current)
    if (energy, updated_at) != (row["energy"], row["energy_updated_at"]):
        await conn.execute(
            "UPDATE players SET energy = ?, energy_updated_at = ? WHERE user_id = ?",
            (energy, updated_at, user_id),
        )

    gear = StatBlock()
    loadout: dict[str, str | None] = {slot.value: None for slot in Slot}
    cursor = await conn.execute(
        """
        SELECT l.slot, l.item_id, i.atk, i.defense, i.drip
        FROM loadout l JOIN items i ON i.id = l.item_id
        WHERE l.user_id = ?
        """,
        (user_id,),
    )
    for eq in await cursor.fetchall():
        loadout[eq["slot"]] = eq["item_id"]
        gear = gear + StatBlock(eq["atk"], eq["defense"], eq["drip"])
    await cursor.close()

    return Player(
        user_id=user_id,
        display_name=row["display_name"] if display_name is None else display_name,
        username=row["username"],
        level=row["level"],
        exp=row["exp"],
        energy=energy,
        energy_updated_at=updated_at,
        base_atk=row["base_atk"],
        base_def=row["base_def"],
        base_drip=row["base_drip"],
        last_daily=row["last_daily"],
        created_at=row["created_at"],
        last_seen=row["last_seen"],
        gear=gear,
        loadout=loadout,
    )


async def _equip_defaults_conn(conn: aiosqlite.Connection, user_id: int) -> None:
    """Auto-equip starter items into empty slots so /me shows a full look."""
    for item_id in starter_kit_ids():
        item = ITEMS_BY_ID.get(item_id)
        if item is None:
            continue
        cursor = await conn.execute(
            "SELECT 1 FROM inventory WHERE user_id = ? AND item_id = ?",
            (user_id, item_id),
        )
        owned = await cursor.fetchone() is not None
        await cursor.close()
        if not owned:
            continue
        cursor = await conn.execute(
            "SELECT 1 FROM loadout WHERE user_id = ? AND slot = ?",
            (user_id, item.slot.value),
        )
        empty = await cursor.fetchone() is None
        await cursor.close()
        if empty:
            await conn.execute(
                "INSERT OR IGNORE INTO loadout (user_id, slot, item_id) VALUES (?, ?, ?)",
                (user_id, item.slot.value, item_id),
            )


async def _grant_item_conn(
    conn: aiosqlite.Connection, user_id: int, item_id: str, current: int
) -> None:
    await conn.execute(
        "INSERT OR IGNORE INTO inventory (user_id, item_id, acquired_at) VALUES (?, ?, ?)",
        (user_id, item_id, current),
    )


# ---------------------------------------------------------------------------
# Inventory / equipment
# ---------------------------------------------------------------------------


async def list_inventory(user_id: int) -> list[aiosqlite.Row]:
    async with db.read() as conn:
        cursor = await conn.execute(
            """
            SELECT i.*, (SELECT 1 FROM loadout l
                         WHERE l.user_id = inv.user_id AND l.item_id = i.id) AS equipped
            FROM inventory inv JOIN items i ON i.id = inv.item_id
            WHERE inv.user_id = ?
            ORDER BY CASE i.rarity
                        WHEN 'legendary' THEN 0 WHEN 'epic' THEN 1
                        WHEN 'rare' THEN 2 ELSE 3 END,
                     i.slot, i.price_credits
            """,
            (user_id,),
        )
        rows = list(await cursor.fetchall())
        await cursor.close()
        return rows


async def equip_item(user_id: int, item_id: str) -> Slot:
    """Equip an owned item; replaces whatever is in its slot (one txn)."""
    item = ITEMS_BY_ID.get(item_id)
    if item is None:
        raise NotOwned(item_id)
    async with db.write() as conn:
        cursor = await conn.execute(
            "SELECT 1 FROM inventory WHERE user_id = ? AND item_id = ?",
            (user_id, item_id),
        )
        owned = await cursor.fetchone() is not None
        await cursor.close()
        if not owned:
            raise NotOwned(item_id)
        await conn.execute(
            """
            INSERT INTO loadout (user_id, slot, item_id) VALUES (?, ?, ?)
            ON CONFLICT (user_id, slot) DO UPDATE SET item_id = excluded.item_id
            """,
            (user_id, item.slot.value, item_id),
        )
        return item.slot


async def unequip_item(user_id: int, slot: Slot) -> None:
    async with db.write() as conn:
        await conn.execute(
            "DELETE FROM loadout WHERE user_id = ? AND slot = ?", (user_id, slot.value)
        )


# ---------------------------------------------------------------------------
# Progression
# ---------------------------------------------------------------------------


async def grant_exp_conn(
    conn: aiosqlite.Connection, user_id: int, amount: int
) -> tuple[int, int]:
    """Add EXP inside an open transaction; returns (levels_gained, new_level).

    Also applies per-level base stat bonuses. Kept as ``_conn`` so duel/raid
    payouts can award EXP without opening a second (deadlocking) transaction.
    """
    cursor = await conn.execute(
        "SELECT level, exp FROM players WHERE user_id = ?", (user_id,)
    )
    row = await cursor.fetchone()
    await cursor.close()
    if row is None:
        return 0, 1
    if amount <= 0:
        return 0, row["level"]

    level, exp = row["level"], row["exp"] + amount
    gained = 0
    while exp >= exp_to_next(level):
        exp -= exp_to_next(level)
        level += 1
        gained += 1
    await conn.execute(
        """
        UPDATE players
        SET exp = ?, level = ?,
            base_atk = base_atk + ?,
            base_def = base_def + ?,
            base_drip = base_drip + ?
        WHERE user_id = ?
        """,
        (
            exp,
            level,
            gained * settings.level_atk_bonus,
            gained * settings.level_def_bonus,
            gained * settings.level_drip_bonus,
            user_id,
        ),
    )
    return gained, level


async def grant_exp(user_id: int, amount: int) -> tuple[int, int]:
    async with db.write() as conn:
        return await grant_exp_conn(conn, user_id, amount)


async def spend_energy_conn(
    conn: aiosqlite.Connection, user_id: int, amount: int
) -> int:
    """Deduct energy (after applying regen); returns the new value.

    Raises :class:`InsufficientEnergy` - the caller's transaction rolls back,
    so a failed check never leaves partial state.
    """
    if amount <= 0:
        return 0
    cursor = await conn.execute(
        "SELECT energy, energy_updated_at FROM players WHERE user_id = ?",
        (user_id,),
    )
    row = await cursor.fetchone()
    await cursor.close()
    if row is None:
        raise GameError(f"player {user_id} not found")
    energy, updated_at = _regen_window(row["energy"], row["energy_updated_at"], now())
    if energy < amount:
        raise InsufficientEnergy(amount, energy)
    energy -= amount
    await conn.execute(
        "UPDATE players SET energy = ?, energy_updated_at = ? WHERE user_id = ?",
        (energy, updated_at, user_id),
    )
    return energy


async def spend_energy(user_id: int, amount: int) -> int:
    async with db.write() as conn:
        return await spend_energy_conn(conn, user_id, amount)


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def render_request(player: Player) -> RenderRequest:
    """Build the compositor request for a player's current look."""
    # Deterministic scene variety: same player always gets the same backdrop.
    background = BACKGROUND_KEYS[player.user_id % len(BACKGROUND_KEYS)][0]
    body = "base_aegis" if player.loadout.get(Slot.WEAPON.value) else "base_street"
    return RenderRequest(
        display_name=player.display_name,
        username=player.username,
        level=player.level,
        atk=player.atk,
        defense=player.defense,
        drip=player.drip,
        loadout=dict(player.loadout),
        background=background,
        body=body,
    )
