"""SQLite connectivity with transactional guarantees.

Why this file exists
--------------------
The brief requires "atomic transactions and row locking on all wallet/inventory
mutations to prevent double-spending race conditions".  SQLite does not offer
``SELECT ... FOR UPDATE``; its equivalent guarantee is the *write lock*, which
must be taken at the **start** of a transaction (``BEGIN IMMEDIATE``) rather
than implicitly on the first write.  Otherwise two tasks can both pass a
``SELECT balance`` check before either writes.

This module therefore provides:

* ``db.write()``  — an async context manager that takes a **process-wide**
  ``asyncio.Lock`` and then issues ``BEGIN IMMEDIATE``.  Every balance check,
  debit, credit and inventory mutation in the bot runs inside it, so the
  read-check-write sequence is atomic against every other task in the process.
* ``db.read()``   — a lock-free connection for the (far more common)
  non-mutating queries.  WAL mode lets readers proceed during a write.
* ``CHECK (col >= 0)`` constraints in the schema as a defence-in-depth backstop.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import aiosqlite

from config import settings

logger = logging.getLogger(__name__)

SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"

# Applied to both connections at open time.
_PRAGMAS = (
    "PRAGMA journal_mode = WAL",      # readers never block the writer
    "PRAGMA synchronous = NORMAL",    # safe with WAL, much faster than FULL
    "PRAGMA foreign_keys = ON",       # SQLite defaults this OFF
    "PRAGMA busy_timeout = 5000",     # wait instead of failing on lock contention
    "PRAGMA temp_store = MEMORY",
)


class Database:
    """Owns the two long-lived SQLite connections used by the whole process."""

    def __init__(self, path: Path | str = settings.db_path) -> None:
        self._path = Path(path)
        # One writer guarded by a lock; one reader that never mutates.
        self._write_conn: aiosqlite.Connection | None = None
        self._read_conn: aiosqlite.Connection | None = None
        # Serialises BEGIN IMMEDIATE across all coroutines in this process.
        self._write_lock = asyncio.Lock()
        self._closed = True

    # -- lifecycle ----------------------------------------------------------

    async def connect(self) -> None:
        """Open connections, apply pragmas and install the schema."""
        if not self._closed:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)

        self._write_conn = await aiosqlite.connect(
            self._path, isolation_level=None  # we drive BEGIN/COMMIT ourselves
        )
        self._read_conn = await aiosqlite.connect(
            self._path, isolation_level=None, check_same_thread=True
        )

        for conn in (self._write_conn, self._read_conn):
            conn.row_factory = sqlite3.Row
            await self._apply_pragmas(conn)

        await self._migrate()
        self._closed = False
        logger.info("database ready at %s", self._path)

    async def _apply_pragmas(self, conn: aiosqlite.Connection) -> None:
        for pragma in _PRAGMAS:
            await conn.execute(pragma)

    async def _migrate(self) -> None:
        """Idempotent schema install. ``CREATE TABLE IF NOT EXISTS`` throughout."""
        script = SCHEMA_PATH.read_text(encoding="utf-8")
        assert self._write_conn is not None
        await self._write_conn.executescript(script)

        # Migrate columns for players table
        cursor = await self._write_conn.execute("PRAGMA table_info(players)")
        cols = {row["name"] for row in await cursor.fetchall()}
        await cursor.close()
        for col, col_type, default_val in [
            ("gender", "TEXT", "'نامشخص'"),
            ("age", "INTEGER", "20"),
            ("skin_tone", "TEXT", "'fair'"),
            ("eye_color", "TEXT", "'amber'"),
            ("body_stance", "TEXT", "'base_street'"),
            ("hair_color", "TEXT", "'black'"),
            ("onboarding_completed", "INTEGER", "0"),
        ]:
            if col not in cols:
                await self._write_conn.execute(
                    f"ALTER TABLE players ADD COLUMN {col} {col_type} DEFAULT {default_val}"
                )

    async def close(self) -> None:
        """Close both connections (safe to call twice)."""
        self._closed = True
        for conn in (self._write_conn, self._read_conn):
            if conn is not None:
                try:
                    await conn.close()
                except Exception:  # noqa: BLE001 - shutting down regardless
                    logger.exception("error closing a connection")
        self._write_conn = None
        self._read_conn = None

    def _require_write(self) -> aiosqlite.Connection:
        if self._write_conn is None or self._closed:
            raise RuntimeError("database is not connected; call connect() first")
        return self._write_conn

    def _require_read(self) -> aiosqlite.Connection:
        if self._read_conn is None or self._closed:
            raise RuntimeError("database is not connected; call connect() first")
        return self._read_conn

    # -- transactions -------------------------------------------------------

    @asynccontextmanager
    async def write(self) -> AsyncIterator[aiosqlite.Connection]:
        """Run a serialised, all-or-nothing read-modify-write transaction.

        The outer ``asyncio.Lock`` serialises tasks; ``BEGIN IMMEDIATE`` takes
        SQLite's own write lock up front so the whole read-check-write window
        is atomic.  Rollback happens on *any* exception, which is exactly what
        you want for a failed balance check in the middle of a transfer.
        """
        conn = self._require_write()
        async with self._write_lock:
            await conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
            except BaseException:
                await conn.execute("ROLLBACK")
                raise
            else:
                await conn.execute("COMMIT")

    @asynccontextmanager
    async def read(self) -> AsyncIterator[aiosqlite.Connection]:
        """Read-only context. No lock, no explicit transaction."""
        yield self._require_read()

    # -- convenience query helpers (all read-only, never mutate) ------------

    async def fetchone(self, sql: str, params: Any = ()) -> sqlite3.Row | None:
        async with self.read() as conn:
            cursor = await conn.execute(sql, params)
            try:
                return await cursor.fetchone()
            finally:
                await cursor.close()

    async def fetchall(self, sql: str, params: Any = ()) -> list[sqlite3.Row]:
        async with self.read() as conn:
            cursor = await conn.execute(sql, params)
            try:
                return list(await cursor.fetchall())
            finally:
                await cursor.close()

    async def executescript(self, script: str) -> None:
        """Apply an out-of-band SQL script (seeding) under the write lock.

        Runs *outside* ``db.write()``: ``sqlite3.executescript`` issues an
        implicit COMMIT first, which would make the enclosing transaction's
        own COMMIT raise ``cannot commit - no transaction is active``.
        """
        conn = self._require_write()
        async with self._write_lock:
            await conn.executescript(script)

    async def fetch_setting(self, key: str, default: str | None = None) -> str | None:
        row = await self.fetchone("SELECT value FROM constants WHERE key = ?", (key,))
        return row["value"] if row else default

    # -- introspection -------------------------------------------------------

    async def table_names(self) -> set[str]:
        rows = await self.fetchall(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
        return {row["name"] for row in rows}

    async def integrity_check(self) -> str:
        row = await self.fetchone("PRAGMA integrity_check")
        return row[0] if row else "unknown"


# ---------------------------------------------------------------------------
# Process-wide singleton used by every service and handler.
# ---------------------------------------------------------------------------
db = Database()
