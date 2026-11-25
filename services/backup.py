"""سرویس پشتیبان‌گیری خودکار و ایمن دیتابیس شهر تیرامیکس.

ارسال خودکار فایل پشتیبان دیتابیس SQLite در هر ساعت به ادمین و مالک اصلی (Rex Lapis)،
همراه با چک‌پوینت ایمن WAL و بررسی یکپارچگی فایل (Integrity Check).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
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


async def create_backup_file() -> Path | None:
    """تهیه نسخه پشتیبان امن از دیتابیس فعال با استفاده از API آنلاین SQLite.

    بعد از ساخت، صحت فایل (integrity_check + جدول players) صحه‌گذاری می‌شود؛
    اگر فایل خراب باشد تا دو بار دیگر تلاش می‌کنیم — هیچ‌وقت بکاپ خراب
    تولید نمی‌شود (تهیهٔ نسخهٔ پشتیبان بدون تأیید، ممنوع).
    """
    db_path = settings.db_path
    if not db_path.exists():
        logger.warning("Database path %s does not exist; cannot backup", db_path)
        return None

    # ۱. فلاش کردن تغییرات لاگ تراکنش‌ها (WAL) به دیسک اصلی (خارج از ترنزکشن)
    try:
        async with db._write_lock:
            conn = db._require_write()
            await conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except Exception as exc:
        logger.warning("WAL checkpoint before backup encountered an issue: %s", exc)

    for attempt in range(1, 4):  # up to 3 attempts: 1 fresh + 2 retries
        timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        # نام تصادفی و مجوز محدود (mkstemp) تا فایل دیتابیس در مسیر مشترک سیستم
        # قابل حدس یا خواندن توسط کاربران دیگر نباشد.
        fd, name = tempfile.mkstemp(prefix=f"tiramix_backup_{timestamp}_", suffix=".db")
        os.close(fd)
        backup_path = Path(name)

        def _sync_backup(dst_path: Path = backup_path) -> None:
            src = sqlite3.connect(db_path)
            dst = sqlite3.connect(dst_path)
            try:
                with dst:
                    src.backup(dst)
            finally:
                dst.close()
                src.close()

        try:
            await asyncio.to_thread(_sync_backup)
            if not await asyncio.to_thread(backup_path.exists):
                logger.warning("backup attempt %d: file not created", attempt)
                continue
            ok, reason, _p_count = await verify_backup_file(backup_path)
            if ok:
                logger.info(
                    "backup attempt %d verified OK (%d players)", attempt, _p_count
                )
                return backup_path
            logger.error(
                "backup attempt %d FAILED integrity: %s — regenerating", attempt, reason
            )

            def _unlink(p: Path = backup_path) -> None:
                p.unlink(missing_ok=True)

            await asyncio.to_thread(_unlink)
        except Exception as exc:
            logger.error("Failed to generate SQLite backup file: %s", exc)

            def _unlink_err(p: Path = backup_path) -> None:
                p.unlink(missing_ok=True)

            await asyncio.to_thread(_unlink_err)

    logger.critical(
        "create_backup_file: all 3 attempts failed — refusing to hand out a bad backup"
    )
    return None


async def verify_backup_file(backup_path: Path) -> tuple[bool, str, int]:
    """صحت‌سنجی فایل بکاپ: integrity_check + جدول players + شمارش بازیکنان.

    Returns (ok, reason, player_count). هر جا فایل باید «سالم» شمرده شود
    (قبل از آپلود به تلگرام، قبل از ریستور) این تابع باید پاس شود.
    """
    def _verify() -> tuple[bool, str, int]:
        if not backup_path.exists() or backup_path.stat().st_size == 0:
            return False, "فایل ارسالی خالی است یا یافت نشد.", 0
        conn = None
        try:
            conn = sqlite3.connect(backup_path)
            cur = conn.cursor()
            cur.execute("PRAGMA integrity_check")
            row = cur.fetchone()
            if not row or str(row[0]).lower() != "ok":
                return False, f"فایل پشتیبان خراب است ({row[0] if row else 'ناشناخته'}).", 0

            # بررسی وجود جدول‌های حیاتی
            cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = {r[0] for r in cur.fetchall()}
            if "players" not in tables:
                return False, "فایل مربوط به بازی تیرامیکس نیست (جدول بازیکنان وجود ندارد).", 0

            cur.execute("SELECT COUNT(*) FROM players")
            p_count = int(cur.fetchone()[0])
            return True, "ok", p_count
        except Exception as exc:
            logger.warning("backup verify failed: %s", exc)
            return False, f"خطا در خواندن فایل دیتابیس: {exc}", 0
        finally:
            # بستن حتمی اتصال: روی ویندوز اتصال باز، فایل را قفل نگه می‌دارد
            # و از حذف فایل پشتیبان جلوگیری می‌کند.
            if conn is not None:
                with contextlib.suppress(Exception):
                    conn.close()

    return await asyncio.to_thread(_verify)


def _retry_backup_on_corruption(db_path: Path, backup_path: Path) -> None:
    """اگر فایل پشتیبان ناقص نوشته شده باشد، یک بار دیگر از نو آن را می‌سازد.

    علت خرابی معمول: WAL نیمه‌کاره یا کپی شدن فایل هنگام نوشتن. پس از حذف فایل
    معیوب، چک‌پوینت FULL اجرا می‌شود تا همه‌ی تراکنش‌ها به فایل اصلی منتقل شوند
    و سپس بکاپ دوباره گرفته شود.
    """
    if backup_path.exists():
        backup_path.unlink()
    src = sqlite3.connect(db_path)
    dst = sqlite3.connect(backup_path)
    try:
        cur = src.cursor()
        cur.execute("PRAGMA wal_checkpoint(FULL)")
        cur.close()
        with dst:
            src.backup(dst)
    finally:
        dst.close()
        src.close()


async def create_verified_backup() -> tuple[Path | None, str, int]:
    """تهیه پشتیبان + تأیید سلامت؛ اگر ناقص بود یک بار دوباره ساخته می‌شود.

    خروجی: (مسیر فایل یا None، پیام وضعیت، تعداد بازیکنان)
    """
    db_path = settings.db_path
    backup_file = await create_backup_file()
    if not backup_file or not backup_file.exists():
        return None, "ساخت فایل پشتیبان ناموفق بود.", 0

    ok, reason, player_count = await verify_backup_file(backup_file)
    if ok:
        return backup_file, reason, player_count

    logger.warning(
        "Backup failed verification (%s); rebuilding with FULL WAL checkpoint...", reason
    )
    try:
        await asyncio.to_thread(_retry_backup_on_corruption, db_path, backup_file)
    except Exception as exc:
        logger.error("Rebuild of corrupted backup failed: %s", exc)
        with contextlib.suppress(Exception):
            backup_file.unlink()
        return None, f"بازسازی فایل پشتیبان ناموفق بود: {exc}", 0

    ok, reason, player_count = await verify_backup_file(backup_file)
    if ok:
        return backup_file, reason, player_count

    logger.error("Backup still corrupt after rebuild: %s", reason)
    with contextlib.suppress(Exception):
        backup_file.unlink()
    return None, reason, 0


async def send_backup_to_admins(bot: Bot) -> int:
    """ارسال فایل بکاپ تهیه شده به پیوی مالک و ادمین‌های ربات."""
    backup_file = await create_backup_file()
    if not backup_file or not backup_file.exists():
        logger.error("send_backup_to_admins: backup file was not created")
        return 0

    # صحت مجدد قبل از ارسال (در صورت فایل خراب، هرگز ارسال نمی‌شود)
    ok, reason, _p = await verify_backup_file(backup_file)
    if not ok:
        logger.error("send_backup_to_admins: refusing to send corrupted backup: %s", reason)
        with contextlib.suppress(Exception):
            backup_file.unlink()
        return 0

    recipients = set(settings.admin_ids)
    if not recipients:
        logger.warning("ADMIN_IDS is empty; nobody to receive the backup.")
        return 0

    sent_count = 0
    now_str = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
    file_size_kb = backup_file.stat().st_size / 1024
    caption = (
        f"📦 <b>پشتیبان خودکار دیتابیس شهر تیرامیکس</b>\n\n"
        f"📅 زمان تهیه: <code>{now_str}</code>\n"
        f"💾 حجم فایل: <b>{file_size_kb:,.1f} KB</b>\n"
        f"🛡 وضعیت اطلاعات: سالم (integrity_check پاس شد، {_p} بازیکن) ✅"
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

    # صحت مجدد قبل از آپلود — فایل خراب هرگز روی کانال بکاپ پین نمی‌شود.
    ok, reason, p_count = await verify_backup_file(backup_file)
    if not ok:
        logger.error("save_and_pin_backup: refusing to pin corrupted backup: %s", reason)
        with contextlib.suppress(Exception):
            backup_file.unlink()
        return False

    chat_id = settings.backup_chat_id or settings.primary_admin_id
    if not chat_id:
        logger.warning("No BACKUP_CHAT_ID or ADMIN_IDS configured; cannot store cloud backup.")
        return False
    now_str = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
    file_size_kb = backup_file.stat().st_size / 1024
    caption = (
        f"📦 <b>پشتیبان کلود دیتابیس شهر تیرامیکس</b>\n\n"
        f"📅 زمان ذخیره: <code>{now_str}</code>\n"
        f"💾 حجم فایل: <b>{file_size_kb:,.1f} KB</b>\n"
        f"🛡 صحت‌سنجی: integrity_check ✅ | {p_count} بازیکن ثبت‌شده\n"
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
            row = await cur.fetchone()
            await cur.close()
            count = int(row[0]) if row else 0
            if count > 0:
                logger.info(
                    "Local database already contains %d players; skipping auto-restore.", count
                )
                return False
    except Exception as exc:
        logger.warning("Error checking local player count: %s", exc)

    chat_id = settings.backup_chat_id or settings.primary_admin_id
    if not chat_id:
        logger.warning("No BACKUP_CHAT_ID or ADMIN_IDS configured; cannot auto-restore.")
        return False
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

        fd, name = tempfile.mkstemp(prefix="auto_restore_", suffix=".db")
        os.close(fd)
        temp_file = Path(name)
        await bot.download(doc, destination=temp_file)

        ok, reason = await restore_database_from_file(temp_file)
        with contextlib.suppress(Exception):
            if await asyncio.to_thread(temp_file.exists):
                await asyncio.to_thread(temp_file.unlink)

        if ok:
            logger.info("Auto-restore success: %s", reason)
            with contextlib.suppress(Exception):
                await bot.send_message(
                    chat_id=settings.primary_admin_id,
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
    # ۱. بررسی وجود و یکپارچگی فایل SQLite (از تابع مشترک صحت‌سنجی)
    ok, reason, player_count = await verify_backup_file(backup_path)
    if not ok:
        return False, reason

    target_path = getattr(db, "_path", settings.db_path)

    # ۲. فلاش نهایی لاگ‌ها و بستن اتصالات فعال دیتابیس
    with contextlib.suppress(Exception):
        async with db._write_lock:
            conn = db._require_write()
            await conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    await db.close()
    await asyncio.sleep(0.05)

    # ۳. جایگزینی فایل اصلی و پاک‌سازی WAL/SHM قبلی
    def _swap_files() -> None:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(backup_path, target_path)
        wal_path = Path(f"{target_path}-wal")
        shm_path = Path(f"{target_path}-shm")
        # روی ویندوز ممکن است WAL/SHM هنوز توسط آخرین خواننده قفل شده باشد؛
        # این بی‌خطر است چون فایل اصلی جایگزین شده و SQLite هنگام اتصال بعدی
        # دوباره آن‌ها را می‌سازد. حذف فقط با تلاش (best-effort).
        for p in (wal_path, shm_path):
            if p.exists():
                with contextlib.suppress(OSError):
                    p.unlink()

    # CRITICAL: between db.close() and db.connect() the process has no database.
    # If the swap raises, the bot keeps polling with a closed connection and every
    # update dies with "database is not connected".  Always reconnect — even on
    # failure — so a bad restore degrades to "restore failed", never to a dead bot.
    try:
        await asyncio.to_thread(_swap_files)
    finally:
        if db._closed:
            await db.connect()

    # ۵. پاک‌سازی کش آواتار
    from services import avatar
    avatar.invalidate_avatar_cache()

    return True, f"دیتابیس با موفقیت بازیابی شد! اطلاعات {player_count} بازیکن لود شد."
