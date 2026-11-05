"""سرویس پشتیبان‌گیری خودکار و ایمن دیتابیس شهر تیرامیکس.

ارسال خودکار فایل پشتیبان دیتابیس SQLite در هر ساعت به ادمین و مالک اصلی (Rex Lapis)،
همراه با چک‌پوینت ایمن WAL و بررسی یکپارچگی فایل (Integrity Check).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import shutil
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

    # ۱. فلاش کردن تغییرات لاگ تراکنش‌ها (WAL) به دیسک اصلی (خارج از ترنزکشن)
    try:
        async with db._write_lock:
            conn = db._require_write()
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
            await save_and_pin_backup(bot)
        except asyncio.CancelledError:
            logger.info("Hourly database backup scheduler cancelled.")
            break
        except Exception as exc:
            logger.error("Unhandled error in hourly backup loop: %s", exc)
            await asyncio.sleep(60)


def start_backup_scheduler(bot: Bot) -> asyncio.Task:
    """اجرای جاب پس‌زمینه برای بکاپ‌گیری ساعتی."""
    return asyncio.create_task(_hourly_backup_loop(bot), name="hourly-db-backup")


async def save_and_pin_backup(bot: Bot) -> bool:
    """ارسال و پین کردن آخرین نسخه دیتابیس در چت یا کانال پشتیبان برای بازیابی خودکار."""
    backup_file = await create_backup_file()
    if not backup_file or not backup_file.exists():
        return False

    chat_id = settings.backup_chat_id or PRIMARY_ADMIN_ID
    now_str = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
    file_size_kb = backup_file.stat().st_size / 1024
    caption = (
        f"📦 <b>پشتیبان کلود دیتابیس شهر تیرامیکس</b>\n\n"
        f"📅 زمان ذخیره: <code>{now_str}</code>\n"
        f"💾 حجم فایل: <b>{file_size_kb:,.1f} KB</b>\n"
        f"📌 <i>این پیام پین شده تا در صورت دیپلوی مجدد و پاک شدن سرور، دیتابیس خودکار لود شود.</i>"
    )

    try:
        doc = FSInputFile(backup_file, filename=backup_file.name)
        msg = await bot.send_document(chat_id=chat_id, document=doc, caption=caption)
        with contextlib.suppress(Exception):
            await bot.pin_chat_message(
                chat_id=chat_id, message_id=msg.message_id, disable_notification=True
            )
        logger.info("Latest database backup saved and pinned in chat %s", chat_id)
        return True
    except Exception as exc:
        logger.warning("Could not send and pin backup to chat %s: %s", chat_id, exc)
        return False
    finally:
        with contextlib.suppress(Exception):
            backup_file.unlink()


async def auto_restore_on_startup(bot: Bot) -> bool:
    """در صورتی که دیتابیس لوکال کانتینر خالی باشد، آخرین بکاپ پین‌شده را لود می‌کند."""
    try:
        async with db.read() as conn:
            cur = await conn.execute("SELECT COUNT(*) FROM players")
            count = int((await cur.fetchone())[0])
            if count > 0:
                logger.info(
                    "Local database already contains %d players; skipping auto-restore.", count
                )
                return False
    except RuntimeError:
        # DB not connected yet — skip auto-restore, let normal startup handle it
        logger.warning("DB not connected during auto_restore_on_startup; skipping.")
        return False
    except Exception as exc:
        logger.warning("Error checking local player count: %s", exc)

    chat_id = settings.backup_chat_id or PRIMARY_ADMIN_ID
    logger.info(
        "Local database is empty. Attempting auto-restore from pinned backup in chat %s...",
        chat_id,
    )

    try:
        chat = await bot.get_chat(chat_id)
        pinned = chat.pinned_message
        if not pinned or not pinned.document:
            logger.info("No pinned database document found in chat %s.", chat_id)
            return False

        doc = pinned.document
        if not (doc.file_name or "").endswith(".db"):
            logger.info("Pinned document %s is not a .db file; skipping.", doc.file_name)
            return False

        temp_dir = tempfile.gettempdir()
        temp_file = Path(temp_dir) / f"auto_restore_{int(datetime.now(UTC).timestamp())}.db"
        await bot.download(doc, destination=temp_file)

        ok, reason = await restore_database_from_file(temp_file)
        with contextlib.suppress(Exception):
            if temp_file.exists():
                temp_file.unlink()

        if ok:
            logger.info("Auto-restore success: %s", reason)
            with contextlib.suppress(Exception):
                await bot.send_message(
                    chat_id=PRIMARY_ADMIN_ID,
                    text=(
                        "🔄 <b>بازیابی خودکار دیتابیس پس از دیپلوی جدید!</b>\n\n"
                        f"{reason}\n"
                        "🛡 اطلاعات بازیکنان با موفقیت از تلگرام بارگذاری شد."
                    ),
                )
            return True
        else:
            logger.warning("Auto-restore rejected file: %s", reason)
            return False
    except Exception as exc:
        logger.warning("Auto-restore from Telegram encountered an issue: %s", exc)
        return False


async def restore_database_from_file(backup_path: Path) -> tuple[bool, str]:
    """بازیابی ایمن و اتمیک دیتابیس از فایل پشتیبان با بررسی صحت فایل SQLite."""
    # ۱. بررسی وجود و یکپارچگی فایل SQLite در ترد مجزا
    def _verify_integrity() -> tuple[bool, str, int]:
        if not backup_path.exists() or backup_path.stat().st_size == 0:
            return False, "فایل ارسالی خالی است یا یافت نشد.", 0
        try:
            conn = sqlite3.connect(backup_path)
            cur = conn.cursor()
            cur.execute("PRAGMA integrity_check")
            row = cur.fetchone()
            if not row or row[0].lower() != "ok":
                conn.close()
                return False, f"فایل پشتیبان خراب است ({row[0] if row else 'ناشناخته'})", 0

            # بررسی وجود جدول‌های حیاتی
            cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = {r[0] for r in cur.fetchall()}
            if "players" not in tables:
                conn.close()
                return False, "فایل مربوط به بازی تیرامیکس نیست (جدول بازیکنان وجود ندارد).", 0

            cur.execute("SELECT COUNT(*) FROM players")
            p_count = int(cur.fetchone()[0])
            conn.close()
            return True, "ok", p_count
        except Exception as exc:
            return False, f"خطا در خواندن فایل دیتابیس: {exc}", 0

    ok, reason, player_count = await asyncio.to_thread(_verify_integrity)
    if not ok:
        return False, reason

    target_path = getattr(db, "_path", settings.db_path)

    # ۲. فلاش نهایی لاگ‌ها و بستن اتصالات فعال دیتابیس
    try:
        async with db._write_lock:
            conn = db._require_write()
            await conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except RuntimeError:
        # db was already closed or never connected — that's fine before a restore
        pass
    try:
        await db.close()
    except RuntimeError:
        pass

    await asyncio.sleep(0.05)

    # ۳. پشتیبان‌گیری از فایل فعلی (برای جلوگیری از دست رفتن داده)
    corrupt_backup = target_path.with_suffix(".db.corrupt_backup")
    if target_path.exists():
        with contextlib.suppress(OSError):
            shutil.copy2(target_path, corrupt_backup)

    # ۴. جایگزینی فایل اصلی و پاک‌سازی WAL/SHM قبلی
    def _swap_files() -> None:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(backup_path, target_path)
        wal_path = Path(f"{target_path}-wal")
        shm_path = Path(f"{target_path}-shm")
        if wal_path.exists():
            wal_path.unlink()
        if shm_path.exists():
            shm_path.unlink()

    await asyncio.to_thread(_swap_files)

    # ۵. اتصال مجدد دیتابیس و اجرای چک‌پوینت و مایگریشن
    try:
        await db.connect()
    except Exception as exc:
        # اگه فایل swap‌شده خرابه، برشگردونیم به قبل
        if corrupt_backup.exists():
            shutil.copy2(corrupt_backup, target_path)
            corrupt_backup.unlink()
        return False, f"فایل بکاپ خراب است و قابل بازیابی نیست: {exc}", None

    # ۵. پاک‌سازی کش آواتار
    from services import avatar
    avatar.invalidate_avatar_cache()

    return True, f"دیتابیس با موفقیت بازیابی شد! اطلاعات {player_count} بازیکن لود شد."
