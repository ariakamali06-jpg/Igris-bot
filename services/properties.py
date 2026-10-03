"""Passive economy: real estate (املاک) and hired crew (نیرو).

Both live in the ``properties`` table and share one accrual rule: every
``property_income_interval`` seconds a unit produces one income tick.  The
pending amount is *derived* from ``last_collect`` (never accumulated by a
background job), so income survives restarts and a bot that was offline for a
week still credits the owner — capped by ``property_max_pending_ticks`` so an
abandoned account cannot bank an eternity of rent.

Every coin that moves goes through :mod:`services.economy` inside one
``db.write()`` transaction: buy = debit + row insert, collect = credit +
``last_collect`` bump, both committed or rolled back together.
"""

from __future__ import annotations

import time
from typing import Any

import aiosqlite

from config import PROPERTY_CATALOG, WORKER_CATALOG, settings
from database.connection import db
from models.enums import ActivityKind
from models.player import Player
from services import economy
from services.game import GameError

KIND_PROPERTY = "property"
KIND_WORKER = "worker"

# Cooldown action names (``economy.require_ready`` keys).
ACTION_BUY = "property_buy"
ACTION_UPGRADE = "property_upgrade"
ACTION_COLLECT = "property_collect"

_CATALOGS: dict[str, tuple[dict[str, Any], ...]] = {
    KIND_PROPERTY: PROPERTY_CATALOG,
    KIND_WORKER: WORKER_CATALOG,
}

_KIND_LABELS = {KIND_PROPERTY: "ملک", KIND_WORKER: "نیرو"}


def catalog(kind: str) -> tuple[dict[str, Any], ...]:
    """The buyable catalog for ``kind`` (``property`` or ``worker``)."""
    return _CATALOGS[kind]


def find_entry(kind: str, name: str) -> dict[str, Any]:
    """Resolve a user-typed name to a catalog entry.

    Accepts an exact name or a unique substring (people type half a name in
    chat), and raises :class:`GameError` with the available options otherwise.
    """
    query = name.strip().casefold()
    label = _KIND_LABELS[kind]
    if not query:
        raise GameError(f"نام {label} را بنویسید. مثال: <code>املاک خرید بوفه کوچه</code>")
    entries = catalog(kind)
    for entry in entries:
        if entry["name"].casefold() == query:
            return entry
    partial = [e for e in entries if query in e["name"].casefold()]
    if len(partial) == 1:
        return partial[0]
    available = "، ".join(e["name"] for e in entries)
    raise GameError(
        f"«{name}» در فهرست {label}‌های شهر پیدا نشد.\n"
        f"گزینه‌ها: {available}"
    )


def level_income_multiplier(level: int) -> float:
    """Each upgrade level adds ``property_level_income_bonus`` (compounding)."""
    return (1.0 + settings.property_level_income_bonus) ** max(0, level - 1)


def income_per_tick(income: int, level: int) -> int:
    """Coins produced by one unit per interval at ``level``."""
    return max(1, int(income * level_income_multiplier(level)))


def pending_income(income: int, level: int, last_collect: int, current: int) -> int:
    """Accrued-but-uncollected coins for one unit at ``current`` time."""
    if last_collect <= 0:
        return 0
    ticks = (current - last_collect) // settings.property_income_interval
    if ticks <= 0:
        return 0
    ticks = min(ticks, settings.property_max_pending_ticks)
    return ticks * income_per_tick(income, level)


def upgrade_cost(level: int) -> int:
    """Cost of going from ``level`` to ``level + 1`` (rising ladder)."""
    return int(
        settings.property_upgrade_base_cost
        * settings.property_upgrade_cost_growth ** max(0, level - 1)
    )


async def owned_rows(user_id: int, kind: str | None = None) -> list[dict[str, Any]]:
    """Owned units, optionally filtered to one kind."""
    sql = "SELECT kind, name, level, last_collect FROM properties WHERE owner_id = ?"
    params: tuple[Any, ...] = (user_id,)
    if kind is not None:
        sql += " AND kind = ?"
        params = (user_id, kind)
    sql += " ORDER BY acquired_at"
    rows = await db.fetchall(sql, params)
    return [dict(row) for row in rows]


async def _find_owned(
    conn: aiosqlite.Connection, user_id: int, kind: str, name: str
) -> Any:
    cursor = await conn.execute(
        "SELECT kind, name, level, last_collect FROM properties "
        "WHERE owner_id = ? AND kind = ? AND name = ?",
        (user_id, kind, name),
    )
    row = await cursor.fetchone()
    await cursor.close()
    return row


def _entry_or_default(kind: str, name: str) -> dict[str, Any]:
    """Catalog entry for an owned unit; falls back if the catalog changed."""
    for entry in catalog(kind):
        if entry["name"] == name:
            return entry
    return {"name": name, "price": 0, "income": 1, "desc": ""}


async def list_estate(player: Player, kind: str) -> dict[str, Any]:
    """Catalog + owned units + accrued income for the ``املاک``/``نیرو`` view."""
    current = int(time.time())
    label = _KIND_LABELS[kind]
    lines = [
        f"{'🏘' if kind == KIND_PROPERTY else '👥'} <b>دفتر {label}‌های تیرامیکس</b>",
        "",
        "🛒 <b>فروشی‌ها:</b>",
    ]
    for entry in catalog(kind):
        lines.append(
            f"• <b>{entry['name']}</b> — {entry['price']:,} سکه · "
            f"درآمد {entry['income']:,} سکه هر "
            f"{settings.property_income_interval // 60} دقیقه"
        )
        lines.append(f"  <i>{entry['desc']}</i>")

    owned = await owned_rows(player.user_id, kind)
    lines.append("")
    if owned:
        lines.append(f"🧾 <b>{label}‌های خودت:</b>")
        total_pending = 0
        for row in owned:
            entry = _entry_or_default(kind, row["name"])
            pending = pending_income(
                entry["income"], row["level"], row["last_collect"], current
            )
            total_pending += pending
            lines.append(
                f"• <b>{row['name']}</b> — لول <b>{row['level']}</b> · "
                f"درآمد هر تیک {income_per_tick(entry['income'], row['level']):,} · "
                f"انباشته: <b>{pending:,}</b> سکه"
            )
        lines.append(f"💰 جمع انباشته: <b>{total_pending:,}</b> سکه")
        lines.append("برای برداشت: <code>املاک وصول</code>")
    else:
        lines.append(f"هنوز {label}‌ای نداری! برای شروع: <code>املاک خرید [نام]</code>")
    return {"success": True, "kind": kind, "owned": owned, "message": "\n".join(lines)}


async def buy_asset(player: Player, kind: str, name: str) -> dict[str, Any]:
    """Buy one property/worker: debit + insert in a single transaction."""
    entry = find_entry(kind, name)
    current = int(time.time())
    label = _KIND_LABELS[kind]

    async with db.write() as conn:
        existing = await _find_owned(conn, player.user_id, kind, entry["name"])
        if existing is not None:
            return {
                "success": False,
                "message": f"❌ این {label} از قبل مال توست؛ {label} دیگه‌ای بخر.",
            }
        await economy.mutate(
            conn,
            player.user_id,
            credits=-entry["price"],
            kind=ActivityKind.PROPERTY_BUY,
            ref=f"{kind}:{entry['name']}",
        )
        await conn.execute(
            "INSERT INTO properties "
            "(owner_id, kind, name, level, last_collect, acquired_at) "
            "VALUES (?, ?, ?, 1, ?, ?)",
            (player.user_id, kind, entry["name"], current, current),
        )

    return {
        "success": True,
        "kind": kind,
        "name": entry["name"],
        "price": entry["price"],
        "income": entry["income"],
        "message": (
            f"✅ <b>{label} «{entry['name']}» به نام تو ثبت شد!</b>\n\n"
            f"💵 قیمت پرداختی: <b>{entry['price']:,}</b> سکه\n"
            f"📈 درآمد: <b>{entry['income']:,}</b> سکه هر "
            f"{settings.property_income_interval // 60} دقیقه\n\n"
            f"با <code>املاک وصول</code> درآمد انباشته را بردار."
        ),
    }


async def upgrade_property(player: Player, name: str) -> dict[str, Any]:
    """+30% income per level, capped at ``property_max_level``."""
    entry = find_entry(KIND_PROPERTY, name)

    async with db.write() as conn:
        row = await _find_owned(conn, player.user_id, KIND_PROPERTY, entry["name"])
        if row is None:
            return {
                "success": False,
                "message": (
                    f"❌ اول باید «{entry['name']}» را بخری: "
                    f"<code>املاک خرید {entry['name']}</code>"
                ),
            }
        level = row["level"]
        if level >= settings.property_max_level:
            return {
                "success": False,
                "message": (
                    f"🏆 «{entry['name']}» به بالاترین لول "
                    f"({settings.property_max_level}) رسیده؛ دیگر ارتقا نمی‌گیرد."
                ),
            }
        cost = upgrade_cost(level)
        await economy.mutate(
            conn,
            player.user_id,
            credits=-cost,
            kind=ActivityKind.PROPERTY_UPGRADE,
            ref=f"property:upgrade:{entry['name']}",
        )
        new_level = level + 1
        await conn.execute(
            "UPDATE properties SET level = ? WHERE owner_id = ? AND kind = ? AND name = ?",
            (new_level, player.user_id, KIND_PROPERTY, entry["name"]),
        )

    before = income_per_tick(entry["income"], level)
    after = income_per_tick(entry["income"], new_level)
    return {
        "success": True,
        "name": entry["name"],
        "level": new_level,
        "cost": cost,
        "message": (
            f"🏗 <b>ارتقای «{entry['name']}» به لول {new_level}!</b>\n\n"
            f"💵 هزینه: <b>{cost:,}</b> سکه\n"
            f"📈 درآمد هر تیک: {before:,} ← <b>{after:,}</b> سکه\n"
            f"⏱ هر تیک {settings.property_income_interval // 60} دقیقه یک‌بار."
        ),
    }


async def collect(player: Player) -> dict[str, Any]:
    """``املاک وصول`` — sweep accrued income from every owned unit."""
    current = int(time.time())

    async with db.write() as conn:
        cursor = await conn.execute(
            "SELECT kind, name, level, last_collect FROM properties WHERE owner_id = ?",
            (player.user_id,),
        )
        rows = list(await cursor.fetchall())
        await cursor.close()

        total = 0
        paid: list[tuple[str, int]] = []
        for row in rows:
            entry = _entry_or_default(row["kind"], row["name"])
            pending = pending_income(
                entry["income"], row["level"], row["last_collect"], current
            )
            if pending <= 0:
                continue
            total += pending
            paid.append((row["name"], pending))
            await conn.execute(
                "UPDATE properties SET last_collect = ? "
                "WHERE owner_id = ? AND kind = ? AND name = ?",
                (current, player.user_id, row["kind"], row["name"]),
            )

        if total <= 0:
            return {
                "success": False,
                "earned": 0,
                "message": (
                    f"⏳ هنوز چیزی انباشته نشده. هر "
                    f"{settings.property_income_interval // 60} دقیقه "
                    "یک تیک درآمد اضافه می‌شود."
                ),
            }

        await economy.mutate(
            conn,
            player.user_id,
            credits=total,
            kind=ActivityKind.PROPERTY_COLLECT,
            ref="property:collect",
        )

    lines = ["💰 <b>وصول درآمد انجام شد!</b>", ""]
    for name, amount in paid:
        lines.append(f"• {name}: <b>+{amount:,}</b> سکه")
    lines.append("")
    lines.append(f"💵 مجموع واریزی: <b>+{total:,}</b> سکه")
    return {"success": True, "earned": total, "items": paid, "message": "\n".join(lines)}
