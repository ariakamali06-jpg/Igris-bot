"""Unit tests for database backup generation and restore mechanism."""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

import pytest

from database.connection import db
from database.seed import seed_catalog
from services import game
from services.backup import create_backup_file, restore_database_from_file


@pytest.fixture(autouse=True)
async def _setup_db():
    await db.connect()
    await seed_catalog()
    yield
    await db.close()


@pytest.mark.asyncio
async def test_backup_and_restore_cycle():
    """Verify that backup creates a valid SQLite DB and restore successfully loads it back."""
    # 1. Create a dummy player with known properties
    user_id = 880055
    await game.ensure_player(user_id=user_id, username="persist_user", display_name="قهرمان پایدار")

    async with db.write() as conn:
        await conn.execute(
            "UPDATE players SET level = 42, job = 'جادوگر ارشد' WHERE user_id = ?",
            (user_id,),
        )
        await conn.execute(
            "INSERT OR REPLACE INTO wallets (user_id, credits) VALUES (?, ?)",
            (user_id, 999999),
        )

    # 2. Generate backup file
    backup_file = await create_backup_file()
    assert backup_file is not None
    assert backup_file.exists()
    assert backup_file.stat().st_size > 0

    try:
        # 3. Simulate database wipe or corruption (delete player)
        async with db.write() as conn:
            await conn.execute("DELETE FROM players WHERE user_id = ?", (user_id,))
            await conn.execute("DELETE FROM wallets WHERE user_id = ?", (user_id,))

        p_deleted = await game.load_player(user_id)
        assert p_deleted is None, "Player should be deleted before restore"

        # 4. Restore database from backup file
        success, note = await restore_database_from_file(backup_file)
        assert success is True, f"Restore failed: {note}"
        assert "با موفقیت بازیابی شد" in note

        # 5. Verify restored state
        p_restored = await game.load_player(user_id)
        assert p_restored is not None
        assert p_restored.level == 42
        assert p_restored.job == "جادوگر ارشد"

        from services import economy
        credits_bal, _ = await economy.balances(user_id)
        assert credits_bal == 999999

    finally:
        if backup_file.exists():
            backup_file.unlink()


@pytest.mark.asyncio
async def test_restore_rejects_corrupted_or_invalid_file():
    """Verify restore refuses invalid or corrupted non-SQLite files."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        f.write(b"NOT A REAL SQLITE DATABASE FILE GARBAGE DATA")
        fake_path = Path(f.name)

    try:
        success, reason = await restore_database_from_file(fake_path)
        assert success is False
        assert "خطا" in reason or "خراب" in reason
    finally:
        await asyncio.to_thread(fake_path.unlink)
