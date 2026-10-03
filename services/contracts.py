"""Daily contracts (قرارداد): three countable missions, reset every 24h.

Progress is *derived* from the ledger instead of instrumenting every feature:
a contract for working counts ``kind = 'work'`` rows written today, a study
contract counts ``kind = 'study'`` rows, and so on.  That keeps phase-1
contracts decoupled from the features they track — adding a new activity later
means adding one SQL pattern here, not touching its service.

``contracts`` only caches the snapshot (progress/day/done) so a completed
contract pays out exactly once per day even if the player opens the menu
twenty times.  Rewards (coins + EXP) move through :func:`services.economy.mutate`
and :func:`services.game.grant_exp_conn` *inside the same transaction*, so
every payout lands once, atomically.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from config import settings
from database.connection import db
from models.enums import ActivityKind
from services import economy
from services.game import grant_exp_conn

# One counting query per contract code, scoped to the current window start.
_PROGRESS_SQL: dict[str, str] = {
    "work": """
        SELECT COUNT(*) AS c FROM ledger
        WHERE user_id = ? AND kind = 'work' AND created_at >= ?
    """,
    "study": """
        SELECT COUNT(*) AS c FROM ledger
        WHERE user_id = ? AND kind = 'study' AND created_at >= ?
    """,
    "shop": """
        SELECT COUNT(*) AS c FROM ledger
        WHERE user_id = ? AND kind = 'shop_buy' AND created_at >= ?
    """,
    "theft": """
        SELECT COUNT(*) AS c FROM ledger
        WHERE user_id = ? AND kind = 'theft' AND credits_delta > 0
        AND created_at >= ?
    """,
}


@dataclass(frozen=True, slots=True)
class ContractDef:
    """One daily mission: what counts, how much, and what it pays."""

    code: str
    title: str
    hint: str
    target: int


def _pool() -> tuple[ContractDef, ...]:
    """All contract blueprints; numeric targets come from config."""
    return (
        ContractDef(
            "work",
            "شیفت کار مرکزی",
            "چند بار دستور «کار» بزن",
            settings.contract_target_work,
        ),
        ContractDef(
            "study",
            "کلاس دانشگاه",
            "یک ترم تحصیل را تمام کن",
            settings.contract_target_study,
        ),
        ContractDef(
            "shop",
            "سفارش فروشگاه",
            "یک خرید از فروشگاه انجام بده",
            settings.contract_target_shop,
        ),
        ContractDef(
            "theft",
            "کار خیابانی",
            "یک دزدی موفق از شهروندان داشته باش",
            settings.contract_target_theft,
        ),
    )


def contracts_for_day(day: int) -> list[ContractDef]:
    """Today's ``contract_count`` missions, rotated by day for variety."""
    pool = _pool()
    count = max(1, min(settings.contract_count, len(pool)))
    return [pool[(day + offset) % len(pool)] for offset in range(count)]


def day_index(current: int | None = None) -> int:
    """Contract-window index (resets every ``contract_window_seconds``)."""
    current = int(time.time()) if current is None else current
    return current // settings.contract_window_seconds


async def sync(user_id: int) -> dict:
    """Recount progress, persist it and pay out anything just completed."""
    current = int(time.time())
    day = day_index(current)
    day_start = day * settings.contract_window_seconds
    defs = contracts_for_day(day)

    earned = 0
    exp_gained = 0
    entries: list[dict] = []

    async with db.write() as conn:
        for definition in defs:
            cursor = await conn.execute(
                _PROGRESS_SQL[definition.code], (user_id, day_start)
            )
            row = await cursor.fetchone()
            await cursor.close()
            progress = int(row["c"]) if row else 0

            cursor = await conn.execute(
                "SELECT day, done FROM contracts WHERE user_id = ? AND code = ?",
                (user_id, definition.code),
            )
            stored = await cursor.fetchone()
            await cursor.close()
            # A stale row belongs to yesterday: done resets with the window.
            done = (
                bool(stored["done"]) if stored and stored["day"] == day else False
            )

            completed = progress >= definition.target
            if completed and not done:
                await economy.mutate(
                    conn,
                    user_id,
                    credits=settings.contract_reward_credits,
                    kind=ActivityKind.CONTRACT,
                    ref=f"contract:{definition.code}",
                )
                await grant_exp_conn(conn, user_id, settings.contract_reward_exp)
                done = True
                earned += settings.contract_reward_credits
                exp_gained += settings.contract_reward_exp

            await conn.execute(
                """
                INSERT INTO contracts (user_id, code, progress, day, done)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT (user_id, code) DO UPDATE SET
                    progress = excluded.progress,
                    day = excluded.day,
                    done = excluded.done
                """,
                (user_id, definition.code, progress, day, int(done)),
            )

            entries.append(
                {
                    "code": definition.code,
                    "title": definition.title,
                    "hint": definition.hint,
                    "target": definition.target,
                    "progress": progress,
                    "done": done,
                }
            )

    lines = ["📑 <b>قراردادهای امروز شهر تیرامیکس</b>", ""]
    for entry in entries:
        mark = "✅" if entry["done"] else "▫️"
        bar = f"{min(entry['progress'], entry['target'])}/{entry['target']}"
        lines.append(f"{mark} <b>{entry['title']}</b> — {bar}")
        lines.append(f"   <i>{entry['hint']}</i>")
    lines.append("")
    lines.append(
        f"🎁 پاداش هر قرارداد: <b>{settings.contract_reward_credits:,}</b> سکه "
        f"+ {settings.contract_reward_exp} EXP"
    )
    lines.append("پاداش قرارداد تکمیل‌شده خودکار واریز می‌شود.")
    if earned:
        lines.append("")
        lines.append(f"💵 واریز شد: <b>+{earned:,}</b> سکه · +{exp_gained} EXP")

    return {
        "success": True,
        "day": day,
        "contracts": entries,
        "earned": earned,
        "exp": exp_gained,
        "message": "\n".join(lines),
    }
