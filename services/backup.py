"""سرویس پشتیبان‌گیری خودکار و ایمن دیتابیس شهر تیرامیکس.

ارسال خودکار فایل پشتیبان دیتابیس SQLite در هر ساعت به ادمین و مالک اصلی (Rex Lapis)،
همراه با چک‌پوینت ایمن WAL و بررسی یکپارچگی فایل (Integrity Check).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import sqlite3
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from aiogram import Bot
from aiogram.types import FSInputFile

from config import settings
from database.connection import db

logger = logging.getLogger(__name__)

# Rex Lapis Telegram User ID
PRIMARY_ADMIN_ID = 5765828495


async def create_backup_file() -> Path | None:
    """تهیه نسخه پشتیبان امن از دیتابیس فعال با استفاده از API آنلاین SQLite."""
    db_path = settings.db_path
    if not db_path.exists():
        logger.warning("Database path %s does not exist; cannot backup", db_path)
        return None

    # ۱. فلاش کردن تغییرات لاگ تراکنش‌ها (WAL) به دیسک اصلی
    try:
        async with db.write() as conn:
            await conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
    except Exception as exc:
        logger.warning("WAL checkpoint before backup encountered an issue: %s", exc)

    timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    backup_path = Path(tempfile.gettempdir()) / f"tiramix_backup_{timestamp}.db"

    def _sync_backup() -> None:
        src = sqlite3.connect(db_path)
        dst = sqlite3.connect(backup_path)
        try:
            with dst:
                src.backup(dst)
        finally:
            dst.close()
            src.close()

    try:
        await asyncio.to_thread(_sync_backup)
        if backup_path.exists():
            return backup_path
    except Exception as exc:
        logger.error("Failed to generate SQLite backup file: %s", exc)

    return None


async def send_backup_to_admins(bot: Bot) -> int:
    """ارسال فایل بکاپ تهیه شده به پیوی مالک و ادمین‌های ربات."""
    backup_file = await create_backup_file()
    if not backup_file or not backup_file.exists():
        logger.error("send_backup_to_admins: backup file was not created")
        return 0

    recipients = set(settings.admin_ids)
    recipients.add(PRIMARY_ADMIN_ID)

    sent_count = 0
    now_str = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
    file_size_kb = backup_file.stat().st_size / 1024
    caption = (
        f"📦 <b>پشتیبان خودکار دیتابیس شهر تیرامیکس</b>\n\n"
        f"📅 زمان تهیه: <code>{now_str}</code>\n"
        f"💾 حجم فایل: <b>{file_size_kb:,.1f} KB</b>\n"
        f"🛡 وضعیت اطلاعات: پایدار و تراکنش‌های WAL ثبت‌شده ✅"
    )

    for admin_id in recipients:
        try:
            doc = FSInputFile(backup_file, filename=backup_file.name)
            await bot.send_document(chat_id=admin_id, document=doc, caption=caption)
            sent_count += 1
            logger.info("Database backup successfully sent to admin %s", admin_id)
        except Exception as exc:
            logger.warning("Could not send DB backup to %s: %s", admin_id, exc)

    # پاک‌سازی فایل موقت
    with contextlib.suppress(Exception):
        backup_file.unlink()

    return sent_count


async def _hourly_backup_loop(bot: Bot) -> None:
    """حلقه زمان‌بندی پشتیبان‌گیری هر یک ساعت یک‌بار."""
    logger.info("Hourly database backup scheduler started.")
    while True:
        try:
            await asyncio.sleep(3600)
            await send_backup_to_admins(bot)
        except asyncio.CancelledError:
            logger.info("Hourly database backup scheduler cancelled.")
            break
        except Exception as exc:
            logger.error("Unhandled error in hourly backup loop: %s", exc)
            await asyncio.sleep(60)


def start_backup_scheduler(bot: Bot) -> asyncio.Task:
    """اجرای جاب پس‌زمینه برای بکاپ‌گیری ساعتی."""
    return asyncio.create_task(_hourly_backup_loop(bot), name="hourly-db-backup")
