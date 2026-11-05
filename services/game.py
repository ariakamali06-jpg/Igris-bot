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


def _regen_window(energy: int | None, updated_at: int | None, current: int) -> tuple[int, int]:
    """Apply passive regen up to ``current``; returns (energy, updated_at).

    Passive regen rules:
    - Natural ceiling is settings.max_energy (100).
    - If energy is None (NULL in DB) or uninitialized (0 with missing/invalid timestamp),
      recover to full natural cap (100) immediately.
    - If energy >= 100 (whether full or boosted via food up to 1000+),
      passive regen adds 0 and boosted energy is preserved. Timestamp is
      kept current so regen begins when energy drops below 100.
    - If energy < 100, passive regen restores 1 energy per interval (432s / 7.2m),
      yielding exactly 2 full 0->100 refills per 24 hours.
    """
    natural_cap = settings.max_energy
    current = int(current or 0)

    # Missing or uninitialized legacy profile energy defaults to 100 (full)
    if energy is None or (int(energy) == 0 and (updated_at is None or int(updated_at) <= 0)):
        return natural_cap, current

    energy = int(energy)
    updated_at = current if (updated_at is None or int(updated_at) <= 0) else int(updated_at)

    if energy >= natural_cap:
        return energy, current
    elapsed = current - updated_at
    if elapsed < settings.energy_regen_interval_seconds:
        return energy, updated_at
    intervals = int(elapsed // settings.energy_regen_interval_seconds)
    gained = int(intervals * settings.energy_regen_amount)
    if gained <= 0:
        return energy, updated_at
    new_energy = min(natural_cap, energy + gained)
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
                display_name = CASE
                    WHEN players.onboarding_completed = 1 THEN players.display_name
                    ELSE excluded.display_name
                END,
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


async def load_player(user_id: int, display_name: str | None = None) -> Player | None:
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

    keys = row.keys()
    level = int(row["level"] or 1)

    # محاسبه و بازیابی حداقل سابقه کاری بر اساس لول کاراکتر
    min_work = 0
    if level >= 6:
        min_work = 300
    elif level == 5:
        min_work = 150
    elif level == 4:
        min_work = 80
    elif level == 3:
        min_work = 35
    elif level == 2:
        min_work = 10

    raw_work = row["work_count"] if "work_count" in keys else 0
    work_count = (
        int(raw_work)
        if raw_work is not None and int(raw_work) > 0
        else min_work
    )

    # محاسبه و بازیابی مدرک تحصیلی بر اساس شغل کاراکتر
    job = str(row["job"] or "بیکار") if "job" in keys else "بیکار"
    req_edu = 0
    if job in ("مدیر پروژه", "تحلیلگر ارشد بورس", "معمار ارشد"):
        req_edu = 3
    elif job in ("حسابدار شرکت", "برنامه‌نویس جونیور", "معلم دبیرستان"):
        req_edu = 2
    elif job in ("صندوقدار هایپرمارکت", "راننده تاکسی", "نگهبان برج"):
        req_edu = 1

    raw_edu = row["education_level"] if "education_level" in keys else 0
    education_level = max(int(raw_edu or 0), req_edu)

    updates: dict[str, int | str] = {}
    if (energy, updated_at) != (row["energy"], row["energy_updated_at"]):
        updates["energy"] = energy
        updates["energy_updated_at"] = updated_at
    if "work_count" in keys and (row["work_count"] is None or row["work_count"] != work_count):
        updates["work_count"] = work_count
    if "education_level" in keys and (
        row["education_level"] is None or row["education_level"] != education_level
    ):
        updates["education_level"] = education_level

    if updates:
        set_parts = [f"{k} = ?" for k in updates]
        vals = list(updates.values()) + [user_id]
        await conn.execute(f"UPDATE players SET {', '.join(set_parts)} WHERE user_id = ?", vals)

    return Player(
        user_id=user_id,
        display_name=row["display_name"] if display_name is None else display_name,
        username=row["username"],
        level=level,
        exp=int(row["exp"] or 0),
        energy=int(energy or 0),
        energy_updated_at=int(updated_at or 0),
        base_atk=int(row["base_atk"] or 10),
        base_def=int(row["base_def"] or 8),
        base_drip=int(row["base_drip"] or 5),
        last_daily=int(row["last_daily"] or 0),
        created_at=int(row["created_at"] or 0),
        last_seen=int(row["last_seen"] or 0),
        gear=gear,
        loadout=loadout,
        gender=str(row["gender"] or "نامشخص") if "gender" in keys else "نامشخص",
        age=int(row["age"] or 20) if "age" in keys else 20,
        skin_tone=str(row["skin_tone"] or "fair") if "skin_tone" in keys else "fair",
        eye_color=str(row["eye_color"] or "amber") if "eye_color" in keys else "amber",
        body_stance=(
            str(row["body_stance"] or "base_street")
            if "body_stance" in keys
            else "base_street"
        ),
        hair_color=str(row["hair_color"] or "black") if "hair_color" in keys else "black",
        onboarding_completed=(
            int(row["onboarding_completed"] or 0)
            if "onboarding_completed" in keys
            else 0
        ),
        education_level=education_level,
        job=job,
        spouse_id=row["spouse_id"] if "spouse_id" in keys else None,
        marriage_date=int(row["marriage_date"] or 0) if "marriage_date" in keys else 0,
        children_count=int(row["children_count"] or 0) if "children_count" in keys else 0,
        is_pregnant_until=int(row["is_pregnant_until"] or 0) if "is_pregnant_until" in keys else 0,
        is_jailed_until=int(row["is_jailed_until"] or 0) if "is_jailed_until" in keys else 0,
        shield_until=int(row["shield_until"] or 0) if "shield_until" in keys else 0,
        pet_level=int(row["pet_level"] or 0) if "pet_level" in keys else 0,
        affair_count=int(row["affair_count"] or 0) if "affair_count" in keys else 0,
        bank_balance=int(row["bank_balance"] or 0) if "bank_balance" in keys else 0,
        loan_amount=int(row["loan_amount"] or 0) if "loan_amount" in keys else 0,
        loan_due=int(row["loan_due"] or 0) if "loan_due" in keys else 0,
        last_work_time=int(row["last_work_time"] or 0) if "last_work_time" in keys else 0,
        last_study_time=int(row["last_study_time"] or 0) if "last_study_time" in keys else 0,
        last_steal_time=int(row["last_steal_time"] or 0) if "last_steal_time" in keys else 0,
        last_duel_time=int(row["last_duel_time"] or 0) if "last_duel_time" in keys else 0,
        last_intimacy_time=(
            int(row["last_intimacy_time"] or 0)
            if "last_intimacy_time" in keys
            else 0
        ),
        last_affair_time=int(row["last_affair_time"] or 0) if "last_affair_time" in keys else 0,
        eye_style=str(row["eye_style"] or "eyes1_1") if "eye_style" in keys else "eyes1_1",
        nose_style=(
            str(row["nose_style"] or "nose_natural")
            if "nose_style" in keys
            else "nose_natural"
        ),
        mouth_style=str(row["mouth_style"] or "mouth1_1") if "mouth_style" in keys else "mouth1_1",
        hair_style=str(row["hair_style"] or "hair1") if "hair_style" in keys else "hair1",
        clan_id=row["clan_id"] if "clan_id" in keys else None,
        clan_role=row["clan_role"] if "clan_role" in keys else None,
        education_step=int(row["education_step"] or 0) if "education_step" in keys else 0,
        work_count=work_count,
        hp=int(row["hp"] or 100) if "hp" in keys else 100,
        max_hp=int(row["max_hp"] or 100) if "max_hp" in keys else 100,
        sickness=str(row["sickness"] or "healthy") if "sickness" in keys else "healthy",
        reputation=int(row["reputation"] or 100) if "reputation" in keys else 100,
        title=str(row["title"] or "شهروند عادی") if "title" in keys else "شهروند عادی",
        bio=str(row["bio"] or "") if "bio" in keys else "",
        total_earned=int(row["total_earned"] or 0) if "total_earned" in keys else 0,
        total_spent=int(row["total_spent"] or 0) if "total_spent" in keys else 0,
        duels_won=int(row["duels_won"] or 0) if "duels_won" in keys else 0,
        duels_lost=int(row["duels_lost"] or 0) if "duels_lost" in keys else 0,
        thefts_success=int(row["thefts_success"] or 0) if "thefts_success" in keys else 0,
        thefts_failed=int(row["thefts_failed"] or 0) if "thefts_failed" in keys else 0,
        total_stolen=int(row["total_stolen"] or 0) if "total_stolen" in keys else 0,
        jail_count=int(row["jail_count"] or 0) if "jail_count" in keys else 0,
        meals_eaten=int(row["meals_eaten"] or 0) if "meals_eaten" in keys else 0,
        hospital_visits=int(row["hospital_visits"] or 0) if "hospital_visits" in keys else 0,
        divorce_count=int(row["divorce_count"] or 0) if "divorce_count" in keys else 0,
        daily_streak=int(row["daily_streak"] or 0) if "daily_streak" in keys else 0,
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
    if player.gender in ("زن", "دختر"):
        if player.body_stance and player.body_stance.startswith("base_vn_"):
            body = player.body_stance
        else:
            body = "base_female"
    elif player.gender in ("مرد", "پسر"):
        if player.body_stance and player.body_stance.startswith("base_boy_"):
            body = player.body_stance
        else:
            # Legacy/older rows store skin as pale|fair|natural|tan|dark — map it
            # onto the Sutemo boy skin number so tone survives the migration.
            skin_num = {
                "pale": "1", "fair": "2", "natural": "3", "tan": "4", "dark": "5",
            }.get(player.skin_tone, "2")
            body = f"base_boy_{skin_num}"
    elif player.gender in ("سایه‌وار", "نامشخص"):
        body = "base_shadow"
    elif player.body_stance and (
        player.body_stance in (
            "base_female", "base_shadow", "base_street", "base_male", "base_aegis"
        )
        or player.body_stance.startswith("base_boy_")
    ):
        body = player.body_stance
    else:
        body = "base_boy_2"
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
        skin_tone=player.skin_tone,
        hair_color=player.hair_color,
        eye_color=player.eye_color,
        eye_style=player.eye_style,
        mouth_style=player.mouth_style,
        hair_style=player.hair_style,
        gender=player.gender,
    )


async def complete_character_creation(
    user_id: int,
    display_name: str,
    gender: str,
    age: int,
    skin_tone: str,
    eye_color: str,
    body_stance: str,
    hair_style: str,
    hair_color: str,
    starter_items: tuple[str, ...] = (),
    eye_style: str = "eyes1_1",
    mouth_style: str = "mouth1_1",
    nose_style: str = "nose_natural",
) -> Player:
    """Commit full onboarding wizard selections, grant & equip kit, mark complete."""
    current = now()
    async with db.write() as conn:
        await conn.execute(
            """
            UPDATE players SET
                display_name = ?,
                gender = ?,
                age = ?,
                skin_tone = ?,
                eye_color = ?,
                body_stance = ?,
                hair_color = ?,
                hair_style = ?,
                eye_style = ?,
                nose_style = ?,
                mouth_style = ?,
                onboarding_completed = 1,
                last_seen = ?
            WHERE user_id = ?
            """,
            (
                display_name,
                gender,
                age,
                skin_tone,
                eye_color,
                body_stance,
                hair_color,
                hair_style,
                eye_style,
                nose_style,
                mouth_style,
                current,
                user_id,
            ),
        )
        # Wipe old legacy clothes completely from loadout and inventory
        await conn.execute("DELETE FROM loadout WHERE user_id = ?", (user_id,))
        await conn.execute(
            "DELETE FROM inventory WHERE user_id = ? AND "
            "item_id IN ('fitted_tee', 'street_slacks')",
            (user_id,),
        )
        # Grant kit items if explicitly provided
        for item_id in starter_items:
            if item_id in ITEMS_BY_ID:
                await _grant_item_conn(conn, user_id, item_id, current)
                item = ITEMS_BY_ID[item_id]
                await conn.execute(
                    """
                    INSERT INTO loadout (user_id, slot, item_id)
                    VALUES (?, ?, ?)
                    ON CONFLICT(user_id, slot) DO UPDATE SET item_id = excluded.item_id
                    """,
                    (user_id, item.slot.value, item_id),
                )
        return await _load_conn(conn, user_id, current, display_name=display_name)
