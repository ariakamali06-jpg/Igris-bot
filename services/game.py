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
from typing import Any

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


def _field_int(row: Any, keys: list[str], col: str, default: int) -> int:
    """مقدار عددی ستون با الگوی ``int(row[col] or default)`` (مقدار صفر هم به default می‌رسد)."""
    return int(row[col] or default) if col in keys else default


def _field_str(row: Any, keys: list[str], col: str, default: str) -> str:
    """مقدار متنی ستون با الگوی ``str(row[col] or default)``."""
    return str(row[col] or default) if col in keys else default


def _field_raw(row: Any, keys: list[str], col: str) -> Any:
    """مقدار خام ستون (برای فیلدهای nullable مثل شناسه‌ها)."""
    return row[col] if col in keys else None


async def _load_gear(
    conn: aiosqlite.Connection, user_id: int
) -> tuple[StatBlock, dict[str, str | None]]:
    """بارگذاری تجهیزات پوشیده و مجموع امتیازهای نبرد."""
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
    return gear, loadout


def _derive_work_and_education(
    row: Any, keys: list[str], level: int
) -> tuple[int, int, str]:
    """محاسبه و بازیابی حداقل سابقه کاری و مدرک تحصیلی بر اساس لول و شغل."""
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
    work_count = int(raw_work) if raw_work is not None and int(raw_work) > 0 else min_work

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
    return work_count, education_level, job


async def _persist_derived(
    conn: aiosqlite.Connection,
    user_id: int,
    row: Any,
    keys: list[str],
    energy: int,
    updated_at: int,
    work_count: int,
    education_level: int,
) -> None:
    """ثبت مقادیر بازیابی‌شده (انرژی، سابقه کاری، مدرک) در صورت تغییر."""
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


def _build_player(
    row: Any,
    keys: list[str],
    *,
    user_id: int,
    display_name: str | None,
    energy: int,
    updated_at: int,
    gear: StatBlock,
    loadout: dict[str, str | None],
    work_count: int,
    education_level: int,
    job: str,
) -> Player:
    """ساخت Player از روی ردیف دیتابیس (نگاشت ستون‌ها با پیش‌فرض‌های امن)."""
    level = int(row["level"] or 1)
    return Player(
        user_id=user_id,
        display_name=row["display_name"] if display_name is None else display_name,
        username=_field_raw(row, keys, "username"),
        level=level,
        exp=_field_int(row, keys, "exp", 0),
        energy=int(energy or 0),
        energy_updated_at=int(updated_at or 0),
        base_atk=_field_int(row, keys, "base_atk", 10),
        base_def=_field_int(row, keys, "base_def", 8),
        base_drip=_field_int(row, keys, "base_drip", 5),
        last_daily=_field_int(row, keys, "last_daily", 0),
        created_at=_field_int(row, keys, "created_at", 0),
        last_seen=_field_int(row, keys, "last_seen", 0),
        gear=gear,
        loadout=loadout,
        gender=_field_str(row, keys, "gender", "نامشخص"),
        age=_field_int(row, keys, "age", 20),
        skin_tone=_field_str(row, keys, "skin_tone", "fair"),
        eye_color=_field_str(row, keys, "eye_color", "amber"),
        body_stance=_field_str(row, keys, "body_stance", "base_street"),
        hair_color=_field_str(row, keys, "hair_color", "black"),
        onboarding_completed=_field_int(row, keys, "onboarding_completed", 0),
        education_level=education_level,
        job=job,
        spouse_id=_field_raw(row, keys, "spouse_id"),
        marriage_date=_field_int(row, keys, "marriage_date", 0),
        children_count=_field_int(row, keys, "children_count", 0),
        is_pregnant_until=_field_int(row, keys, "is_pregnant_until", 0),
        is_jailed_until=_field_int(row, keys, "is_jailed_until", 0),
        shield_until=_field_int(row, keys, "shield_until", 0),
        pet_level=_field_int(row, keys, "pet_level", 0),
        affair_count=_field_int(row, keys, "affair_count", 0),
        bank_balance=_field_int(row, keys, "bank_balance", 0),
        loan_amount=_field_int(row, keys, "loan_amount", 0),
        loan_due=_field_int(row, keys, "loan_due", 0),
        last_work_time=_field_int(row, keys, "last_work_time", 0),
        last_study_time=_field_int(row, keys, "last_study_time", 0),
        last_steal_time=_field_int(row, keys, "last_steal_time", 0),
        last_duel_time=_field_int(row, keys, "last_duel_time", 0),
        last_intimacy_time=_field_int(row, keys, "last_intimacy_time", 0),
        last_affair_time=_field_int(row, keys, "last_affair_time", 0),
        eye_style=_field_str(row, keys, "eye_style", "eyes1_1"),
        nose_style=_field_str(row, keys, "nose_style", "nose_natural"),
        mouth_style=_field_str(row, keys, "mouth_style", "mouth1_1"),
        hair_style=_field_str(row, keys, "hair_style", "hair1"),
        clan_id=_field_raw(row, keys, "clan_id"),
        clan_role=_field_raw(row, keys, "clan_role"),
        education_step=_field_int(row, keys, "education_step", 0),
        work_count=work_count,
        hp=_field_int(row, keys, "hp", 100),
        max_hp=_field_int(row, keys, "max_hp", 100),
        sickness=_field_str(row, keys, "sickness", "healthy"),
        reputation=_field_int(row, keys, "reputation", 100),
        title=_field_str(row, keys, "title", "شهروند عادی"),
        bio=_field_str(row, keys, "bio", ""),
        total_earned=_field_int(row, keys, "total_earned", 0),
        total_spent=_field_int(row, keys, "total_spent", 0),
        duels_won=_field_int(row, keys, "duels_won", 0),
        duels_lost=_field_int(row, keys, "duels_lost", 0),
        thefts_success=_field_int(row, keys, "thefts_success", 0),
        thefts_failed=_field_int(row, keys, "thefts_failed", 0),
        total_stolen=_field_int(row, keys, "total_stolen", 0),
        jail_count=_field_int(row, keys, "jail_count", 0),
        meals_eaten=_field_int(row, keys, "meals_eaten", 0),
        hospital_visits=_field_int(row, keys, "hospital_visits", 0),
        divorce_count=_field_int(row, keys, "divorce_count", 0),
        daily_streak=_field_int(row, keys, "daily_streak", 0),
    )


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
    gear, loadout = await _load_gear(conn, user_id)

    keys = row.keys()
    level = int(row["level"] or 1)
    work_count, education_level, job = _derive_work_and_education(row, keys, level)
    await _persist_derived(
        conn, user_id, row, keys, energy, updated_at, work_count, education_level
    )

    return _build_player(
        row,
        keys,
        user_id=user_id,
        display_name=display_name,
        energy=energy,
        updated_at=updated_at,
        gear=gear,
        loadout=loadout,
        work_count=work_count,
        education_level=education_level,
        job=job,
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
