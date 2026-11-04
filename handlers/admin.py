"""هندلرهای مدیریت و کدهای تقلب سازنده ربات تیرامیکس (Developer & Admin Cheats).

این ماژول به مالک پروژه و ادمین‌های مجاز اجازه می‌دهد مشخصات بازیکنان
(لول، تحصیلات، رکسار نقد، موجودی بانک، انرژی، قدرت حمله، دفاع، استایل، شغل و زندان)
را برای خود یا سایر بازیکنان (از طریق ریپلای، یوزرنیم یا آیدی عددی) تنظیم کنند.
"""

from __future__ import annotations

import contextlib
import logging
import tempfile
import time
from pathlib import Path

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from database.connection import db
from handlers.common import CommandOrText, esc, hydrate, is_admin_or_owner
from services import economy, game

logger = logging.getLogger(__name__)
router = Router(name="admin")

_HELP_TEXT = (
    "🛠 <b>پنل کدهای تقلب سازنده (Developer Cheats)</b>\n\n"
    "👑 <i>این دستورات فقط برای سازنده بازی (آریا) و ادمین‌های سیستم مجاز است.</i>\n\n"
    "📌 <b>نحوه استفاده:</b>\n"
    "• روی خودت: کافیه دستور رو بنویسی.\n"
    "• روی بازیکن دیگر: روی پیامش <b>ریپلای</b> کن یا آیدی/یوزرنیم رو اول دستور بنویس.\n\n"
    "⚡ <b>لیست کدهای تقلب:</b>\n"
    "🔹 <code>چیت لول [عدد]</code> — تغییر سطح (Level)\n"
    "🔹 <code>چیت سواد [۰ تا ۵]</code> — تغییر مدرک تحصیلی (۰: بی‌سواد تا ۵: دکتری)\n"
    "🔹 <code>چیت پول [عدد]</code> — تنظیم رکسار کیف پول\n"
    "🔹 <code>چیت بانک [عدد]</code> — تنظیم موجودی حساب بانکی\n"
    "🔹 <code>چیت انرژی [عدد]</code> — تنظیم انرژی کاراکتر\n"
    "🔹 <code>چیت اتک [عدد]</code> — قدرت حمله پایه (ATK)\n"
    "🔹 <code>چیت دفاع [عدد]</code> — قدرت دفاع پایه (DEF)\n"
    "🔹 <code>چیت دریپ [عدد]</code> — استایل و شانس کریتیکال (DRIP)\n"
    "🔹 <code>چیت شغل [عنوان شغل]</code> — تغییر شغل کاراکتر\n"
    "🔹 <code>چیت ازادی</code> — خروج فوری از بازداشتگاه و صفر شدن جریمه\n"
    "🔹 <code>چیت مکس</code> — ماکزیمم کردن تمام آمارها برای تست سریع و قدرتمند!\n"
    "🔹 <code>چیت ریست</code> — بازنشانی ساخت کاراکتر برای تست مجدد از اول\n"
    "🔹 <code>چیت وضعیت</code> — مشاهده تمام آمارهای دیتابیسی بازیکن\n"
    "🔹 <code>چیت کانال</code> — مدیریت کانال‌های عضویت اجباری\n"
    "🔹 <code>چیت بکاپ</code> (یا <code>/backup</code>) — دریافت فایل دیتابیس در پیوی\n"
    "🔹 <code>چیت بازیابی</code> (یا <code>/restore</code>) — بازیابی دیتابیس از فایل .db\n"
)

_EDU_LABELS = {
    0: "۰ (بی‌سواد / سیکل)",
    1: "۱ (دیپلم)",
    2: "۲ (کاردانی / فوق‌دیپلم)",
    3: "۳ (کارشناسی / لیسانس)",
    4: "۴ (کارشناسی ارشد / فوق‌لیسانس)",
    5: "۵ (دکتری / پروفسور)",
}


async def _resolve_target(
    message: Message, tokens: list[str], caller_id: int
) -> tuple[int, list[str]]:
    """شناسایی آیدی هدف بر اساس ریپلای، آیدی عددی، یوزرنیم یا کاربر فعلی."""
    if message.reply_to_message and message.reply_to_message.from_user:
        target_tg = message.reply_to_message.from_user
        await hydrate(target_tg.id, target_tg.full_name, target_tg.username)
        return target_tg.id, tokens

    if tokens:
        first = tokens[0].strip()
        cleaned = first.lstrip("@")
        if cleaned.isdigit():
            target_id = int(cleaned)
            await hydrate(target_id, f"Player {target_id}", None)
            return target_id, tokens[1:]

        if first.startswith("@"):
            async with db.read() as conn:
                row = await (
                    await conn.execute(
                        "SELECT user_id FROM players WHERE LOWER(username) = LOWER(?)",
                        (cleaned,),
                    )
                ).fetchone()
                if row:
                    return row["user_id"], tokens[1:]

    return caller_id, tokens


@router.message(
    CommandOrText(
        ["cheat", "admin", "set"],
        words={"چیت", "تقلب", "کد تقلب", "ادمین"},
        prefix_words=(
            "چیت",
            "تقلب",
            "کد تقلب",
            "ادمین",
            "cheat",
            "admin",
            "/cheat",
            "/admin",
            "/set",
        ),
    )
)
async def cmd_cheat(message: Message, command: CommandObject | None = None) -> None:
    """اجرای کدهای تقلب ادمین با اعتبارسنجی سطح دسترسی و گزارش تغییرات."""
    user = message.from_user
    if user is None:
        return

    # مسدودسازی دسترسی کاربران عادی بدون ایجاد جلب توجه
    if not is_admin_or_owner(user.id):
        return

    raw_text = (command.args if command and command.args else (message.text or "")).strip()

    # پاکسازی پیشوندها و کلمات کلیدی اولیه
    parts = raw_text.split()
    cmd_prefixes = ("/", "چیت", "تقلب", "ادمین", "cheat", "admin", "set")
    if parts and parts[0].lower().startswith(cmd_prefixes):
        parts = parts[1:]

    target_id, tokens = await _resolve_target(message, parts, user.id)
    target_player = await game.load_player(target_id)
    if target_player is None:
        target_player = await game.ensure_player(
            target_id,
            user.full_name if target_id == user.id else f"Player {target_id}",
            user.username if target_id == user.id else None,
        )

    if not tokens:
        await message.reply(_HELP_TEXT)
        return

    action = tokens[0].lower()
    val_str = tokens[1] if len(tokens) > 1 else ""

    try:
        # ۱. تنظیم سطح (Level)
        if action in ("لول", "سطح", "level", "lvl"):
            if not val_str.isdigit():
                await message.reply("⚠️ لطفاً عدد لول را مشخص کنید. مثال: <code>چیت لول 10</code>")
                return
            new_val = max(1, int(val_str))
            async with db.write() as conn:
                await conn.execute(
                    "UPDATE players SET level = ? WHERE user_id = ?",
                    (new_val, target_id),
                )
            await message.reply(
                f"⚡ <b>کد تقلب با موفقیت اجرا شد!</b> 🛠\n\n"
                f"👤 <b>هدف:</b> {esc(target_player.display_tag)} (<code>{target_id}</code>)\n"
                f"📊 <b>تغییر:</b> لول به <b>{new_val:,}</b> تغییر یافت."
            )
            return

        # ۲. تنظیم مدرک تحصیلی (Education)
        if action in ("سواد", "تحصیلات", "مدرک", "edu", "education"):
            if not val_str.isdigit():
                await message.reply(
                    "⚠️ لطفاً سطح سواد را بین ۰ تا ۵ مشخص کنید. مثال: <code>چیت سواد 3</code>"
                )
                return
            new_val = max(0, min(5, int(val_str)))
            async with db.write() as conn:
                await conn.execute(
                    "UPDATE players SET education_level = ? WHERE user_id = ?",
                    (new_val, target_id),
                )
            edu_name = _EDU_LABELS.get(new_val, str(new_val))
            await message.reply(
                f"⚡ <b>کد تقلب با موفقیت اجرا شد!</b> 🛠\n\n"
                f"👤 <b>هدف:</b> {esc(target_player.display_tag)} (<code>{target_id}</code>)\n"
                f"🎓 <b>تغییر:</b> مدرک تحصیلی به <b>{edu_name}</b> تنظیم شد."
            )
            return

        # ۳. تنظیم موجودی نقد کیف پول (Credits)
        if action in ("پول", "سکه", "نقد", "money", "cash", "credits", "coin", "coins"):
            if not val_str.isdigit():
                await message.reply(
                    "⚠️ لطفاً مبلغ رکسار را مشخص کنید. مثال: <code>چیت پول 100000</code>"
                )
                return
            new_val = int(val_str)
            async with db.write() as conn:
                await conn.execute(
                    "UPDATE wallets SET credits = ? WHERE user_id = ?",
                    (new_val, target_id),
                )
            await message.reply(
                f"⚡ <b>کد تقلب با موفقیت اجرا شد!</b> 🛠\n\n"
                f"👤 <b>هدف:</b> {esc(target_player.display_tag)} (<code>{target_id}</code>)\n"
                f"💰 <b>تغییر:</b> موجودی کیف پول به <b>{new_val:,}</b> سکه تنظیم شد."
            )
            return

        # ۴. تنظیم سپرده بانکی (Bank Balance)
        if action in ("بانک", "سپرده", "bank"):
            if not val_str.isdigit():
                await message.reply(
                    "⚠️ لطفاً مبلغ بانک را مشخص کنید. مثال: <code>چیت بانک 500000</code>"
                )
                return
            new_val = int(val_str)
            async with db.write() as conn:
                await conn.execute(
                    "UPDATE players SET bank_balance = ? WHERE user_id = ?",
                    (new_val, target_id),
                )
            await message.reply(
                f"⚡ <b>کد تقلب با موفقیت اجرا شد!</b> 🛠\n\n"
                f"👤 <b>هدف:</b> {esc(target_player.display_tag)} (<code>{target_id}</code>)\n"
                f"🏛 <b>تغییر:</b> موجودی حساب بانکی به <b>{new_val:,}</b> سکه تنظیم شد."
            )
            return

        # ۵. تنظیم انرژی کاراکتر (Energy)
        if action in ("انرژی", "energy"):
            if not val_str.isdigit():
                await message.reply(
                    "⚠️ لطفاً مقدار انرژی را مشخص کنید. مثال: <code>چیت انرژی 100</code>"
                )
                return
            new_val = max(0, int(val_str))
            async with db.write() as conn:
                await conn.execute(
                    "UPDATE players SET energy = ? WHERE user_id = ?",
                    (new_val, target_id),
                )
            await message.reply(
                f"⚡ <b>کد تقلب با موفقیت اجرا شد!</b> 🛠\n\n"
                f"👤 <b>هدف:</b> {esc(target_player.display_tag)} (<code>{target_id}</code>)\n"
                f"⚡ <b>تغییر:</b> انرژی به <b>{new_val:,}</b> واحد تنظیم شد."
            )
            return

        # ۶. تنظیم قدرت حمله پایه (ATK)
        if action in ("اتک", "حمله", "atk", "attack"):
            if not val_str.isdigit():
                await message.reply("⚠️ لطفاً مقدار حمله را مشخص کنید. مثال: <code>چیت اتک 50</code>")
                return
            new_val = max(1, int(val_str))
            async with db.write() as conn:
                await conn.execute(
                    "UPDATE players SET base_atk = ? WHERE user_id = ?",
                    (new_val, target_id),
                )
            await message.reply(
                f"⚡ <b>کد تقلب با موفقیت اجرا شد!</b> 🛠\n\n"
                f"👤 <b>هدف:</b> {esc(target_player.display_tag)} (<code>{target_id}</code>)\n"
                f"⚔️ <b>تغییر:</b> حمله پایه به <b>{new_val:,}</b> واحد تنظیم شد."
            )
            return

        # ۷. تنظیم قدرت دفاع پایه (DEF)
        if action in ("دفاع", "def", "defense"):
            if not val_str.isdigit():
                await message.reply(
                    "⚠️ لطفاً مقدار دفاع را مشخص کنید. مثال: <code>چیت دفاع 50</code>"
                )
                return
            new_val = max(1, int(val_str))
            async with db.write() as conn:
                await conn.execute(
                    "UPDATE players SET base_def = ? WHERE user_id = ?",
                    (new_val, target_id),
                )
            await message.reply(
                f"⚡ <b>کد تقلب با موفقیت اجرا شد!</b> 🛠\n\n"
                f"👤 <b>هدف:</b> {esc(target_player.display_tag)} (<code>{target_id}</code>)\n"
                f"🛡 <b>تغییر:</b> دفاع پایه به <b>{new_val:,}</b> واحد تنظیم شد."
            )
            return

        # ۸. تنظیم استایل و تیپ پایه (DRIP)
        if action in ("دریپ", "تیپ", "استایل", "drip", "style"):
            if not val_str.isdigit():
                await message.reply(
                    "⚠️ لطفاً مقدار دریپ را مشخص کنید. مثال: <code>چیت دریپ 50</code>"
                )
                return
            new_val = max(0, int(val_str))
            async with db.write() as conn:
                await conn.execute(
                    "UPDATE players SET base_drip = ? WHERE user_id = ?",
                    (new_val, target_id),
                )
            await message.reply(
                f"⚡ <b>کد تقلب با موفقیت اجرا شد!</b> 🛠\n\n"
                f"👤 <b>هدف:</b> {esc(target_player.display_tag)} (<code>{target_id}</code>)\n"
                f"✨ <b>تغییر:</b> استایل/دریپ پایه به <b>{new_val:,}</b> واحد تنظیم شد."
            )
            return

        # ۹. تغییر موقعیت شغلی (Job)
        if action in ("شغل", "job"):
            job_name = " ".join(tokens[1:]).strip() if len(tokens) > 1 else "بیکار"
            async with db.write() as conn:
                await conn.execute(
                    "UPDATE players SET job = ? WHERE user_id = ?",
                    (job_name, target_id),
                )
            await message.reply(
                f"⚡ <b>کد تقلب با موفقیت اجرا شد!</b> 🛠\n\n"
                f"👤 <b>هدف:</b> {esc(target_player.display_tag)} (<code>{target_id}</code>)\n"
                f"💼 <b>تغییر:</b> عنوان شغلی به <b>«{esc(job_name)}»</b> تغییر یافت."
            )
            return

        # ۱۰. آزادی فوری از بازداشتگاه (Jail Release)
        if action in ("ازادی", "آزادی", "زندان", "free", "jail"):
            async with db.write() as conn:
                await conn.execute(
                    "UPDATE players SET is_jailed_until = 0 WHERE user_id = ?",
                    (target_id,),
                )
            await message.reply(
                f"⚡ <b>کد تقلب با موفقیت اجرا شد!</b> 🛠\n\n"
                f"👤 <b>هدف:</b> {esc(target_player.display_tag)} (<code>{target_id}</code>)\n"
                f"🕊 <b>تغییر:</b> بازیکن فوراً از زندان آزاد شد و سابقه بازداشت پاک گردید!"
            )
            return

        # ۱۱. فعال‌سازی حالت خدا و ارتقای کامل (God Mode / Max)
        if action in ("مکس", "max", "god"):
            async with db.write() as conn:
                await conn.execute(
                    """
                    UPDATE players
                    SET level = 50,
                        education_level = 5,
                        base_atk = 100,
                        base_def = 100,
                        base_drip = 50,
                        energy = 200,
                        is_jailed_until = 0,
                        bank_balance = 5000000
                    WHERE user_id = ?
                    """,
                    (target_id,),
                )
                await conn.execute(
                    "UPDATE wallets SET credits = 1000000 WHERE user_id = ?",
                    (target_id,),
                )
            await message.reply(
                f"⚡ <b>حالت خدا (God Mode / Max) فعال شد!</b> 👑🔥\n\n"
                f"👤 <b>هدف:</b> {esc(target_player.display_tag)} (<code>{target_id}</code>)\n"
                f"🔹 لول: <b>50</b>\n"
                f"🎓 مدرک: <b>دکتری / پروفسور (۵)</b>\n"
                f"💰 کیف پول: <b>1,000,000</b> سکه\n"
                f"🏛 بانک: <b>5,000,000</b> سکه\n"
                f"⚡ انرژی: <b>200 / 200</b>\n"
                f"⚔️ قدرت حمله: <b>100</b>\n"
                f"🛡 قدرت دفاع: <b>100</b>\n"
                f"✨ دریپ / استایل: <b>50</b>\n"
                f"🕊 وضعیت زندان: <b>آزاد</b>"
            )
            return

        # ۱۲. بازنشانی وضعیت ویزارد ثبت‌نام و حذف کامل کاراکتر (Reset Character)
        if action in ("ریست", "reset", "reset_char"):
            async with db.write() as conn:
                await conn.execute("DELETE FROM player_avatar WHERE user_id = ?", (target_id,))
                await conn.execute("DELETE FROM player_wardrobe WHERE user_id = ?", (target_id,))
                await conn.execute("DELETE FROM loadout WHERE user_id = ?", (target_id,))
                await conn.execute("DELETE FROM inventory WHERE user_id = ?", (target_id,))
                await conn.execute("DELETE FROM contracts WHERE user_id = ?", (target_id,))
                await conn.execute("DELETE FROM properties WHERE owner_id = ?", (target_id,))
                await conn.execute("DELETE FROM holdings WHERE user_id = ?", (target_id,))
                await conn.execute("DELETE FROM safe_locks WHERE user_id = ?", (target_id,))
                await conn.execute("DELETE FROM wallets WHERE user_id = ?", (target_id,))
                await conn.execute("DELETE FROM players WHERE user_id = ?", (target_id,))
            await message.reply(
                f"⚡ <b>حساب کاربری و پروفایل به طور کامل ریست شد!</b> 🛠\n\n"
                f"👤 <b>هدف:</b> {esc(target_player.display_tag)} (<code>{target_id}</code>)\n"
                "🔄 <b>تغییر:</b> تمام سوابق، شناسنامه و کمد لباس کاراکتر کاملاً صفر شد.\n"
                "اکنون می‌توانید با ارسال <code>/start</code> کاراکتر جدید خود را از ابتدا بسازید."
            )
            return

        # ۱۳. استعلام فنی اطلاعات دیتابیس (Status Info)
        if action in ("وضعیت", "status", "info"):
            updated = await game.load_player(target_id)
            c, d = await economy.balances(target_id)
            edu_title = _EDU_LABELS.get(updated.education_level if updated else 0, "نامشخص")
            is_completed = updated and updated.onboarding_completed == 1
            await message.reply(
                f"📋 <b>مشخصات فنی کاراکتر در دیتابیس:</b>\n\n"
                f"👤 نام: <b>{esc(updated.display_name if updated else 'نامشخص')}</b> "
                f"(<code>{target_id}</code>)\n"
                f"🔹 لول: <b>{updated.level if updated else 1}</b> "
                f"(تجربه: {updated.exp if updated else 0})\n"
                f"🎓 سطح سواد: <b>{edu_title}</b>\n"
                f"💼 شغل: <b>{esc(updated.job if updated else 'بیکار')}</b>\n"
                f"💰 موجودی نقد: <b>{c:,}</b> سکه\n"
                f"🏛 موجودی بانک: <b>{updated.bank_balance if updated else 0:,}</b> سکه\n"
                f"⚡ انرژی: <b>{updated.energy if updated else 100} / 200</b>\n"
                f"⚔️ اتک پایه: <b>{updated.base_atk if updated else 10}</b>\n"
                f"🛡 دفاع پایه: <b>{updated.base_def if updated else 8}</b>\n"
                f"✨ دریپ پایه: <b>{updated.base_drip if updated else 5}</b>\n"
                f"🆔 وضعیت آنبوردینگ: <b>{'تکمیل‌شده' if is_completed else 'تکمیل‌نشده'}</b>"
            )
            return

        # دستور نامعتبر
        await message.reply(
            f"⚠️ دستور تقلب ناشناخته است: <code>{esc(action)}</code>\n\n{_HELP_TEXT}"
        )

    except Exception as exc:  # noqa: BLE001
        logger.exception("Admin cheat failed for user %s: %s", user.id, exc)
        await message.reply(f"❌ خطا در اجرای کد تقلب: <code>{esc(str(exc))}</code>")


@router.message(Command("reset", "reset_char", "admin_reset_char"))
@router.message(F.text.lower().in_({"ریست", "ریست کاراکتر", "ریست_کاراکتر"}))
async def cmd_direct_reset(message: Message) -> None:
    """دستور مستقیم ریست کامل کاراکتر برای ادمین و سازنده ربات."""
    user = message.from_user
    if user is None or not is_admin_or_owner(user.id):
        return
    parts = (message.text or "").split()
    cmd_prefixes = ("/", "ریست", "reset")
    if parts and any(parts[0].lower().startswith(p) for p in cmd_prefixes):
        parts = parts[1:]
    target_id, _ = await _resolve_target(message, parts, user.id)
    target_player = await game.load_player(target_id)
    tag = target_player.display_tag if target_player else str(target_id)

    async with db.write() as conn:
        await conn.execute("DELETE FROM player_avatar WHERE user_id = ?", (target_id,))
        await conn.execute("DELETE FROM player_wardrobe WHERE user_id = ?", (target_id,))
        await conn.execute("DELETE FROM loadout WHERE user_id = ?", (target_id,))
        await conn.execute("DELETE FROM inventory WHERE user_id = ?", (target_id,))
        await conn.execute("DELETE FROM contracts WHERE user_id = ?", (target_id,))
        await conn.execute("DELETE FROM properties WHERE owner_id = ?", (target_id,))
        await conn.execute("DELETE FROM holdings WHERE user_id = ?", (target_id,))
        await conn.execute("DELETE FROM safe_locks WHERE user_id = ?", (target_id,))
        await conn.execute("DELETE FROM wallets WHERE user_id = ?", (target_id,))
        await conn.execute("DELETE FROM players WHERE user_id = ?", (target_id,))

    await message.reply(
        f"⚡ <b>پروفایل و شناسنامه کاراکتر با موفقیت ریست شد!</b> 🛠\n\n"
        f"👤 <b>کاربر:</b> {esc(tag)} (<code>{target_id}</code>)\n"
        "تمامی سوابق، کمد لباس و دارایی‌های کاراکتر صفر شد. "
        "با ارسال <code>/start</code> فرآیند ساخت مجدد کاراکتر آغاز می‌شود."
    )


@router.message(Command("backup", "db_backup"))
@router.message(F.text.lower().in_({"بکاپ", "بک‌آپ", "پشتیبان", "backup"}))
async def cmd_manual_backup(message: Message) -> None:
    """دستور ارسال فوری و دستی فایل بکاپ دیتابیس برای مالک و ادمین."""
    user = message.from_user
    if user is None or not is_admin_or_owner(user.id):
        return

    from services.backup import send_backup_to_admins

    bot = message.bot
    if not bot:
        await message.reply("❌ دسترسی به ربات برای ارسال داکیومنت امکان‌پذیر نیست.")
        return

    status_msg = await message.reply("⏳ در حال تهیه نسخه پشتیبان از دیتابیس شهر...")
    try:
        count = await send_backup_to_admins(bot)
        if count > 0:
            await status_msg.edit_text(
                "✅ <b>نسخه پشتیبان دیتابیس با موفقیت تهیه و به پیوی ارسال شد!</b>"
            )
        else:
            await status_msg.edit_text("❌ خطا در تهیه یا ارسال فایل پشتیبان.")
    except Exception as exc:
        logger.exception("Manual DB backup failed: %s", exc)
        await status_msg.edit_text(f"❌ خطا: <code>{esc(str(exc))}</code>")


@router.message(Command("restore", "db_restore"))
@router.message(F.text.lower().in_({"بازیابی", "ریستور", "restore", "چیت بازیابی"}))
@router.message(
    F.document
    & (
        (F.caption.lower().in_({"بازیابی", "ریستور", "restore", "چیت بازیابی"}))
        | F.caption.startswith("/restore")
    )
)
async def cmd_admin_restore(message: Message) -> None:
    """بازیابی دیتابیس از فایل پشتیبان توسط مالک و ادمین."""
    user = message.from_user
    if user is None or not is_admin_or_owner(user.id):
        return

    bot = message.bot
    if not bot:
        return

    doc = message.document
    if doc is None and message.reply_to_message and message.reply_to_message.document:
        doc = message.reply_to_message.document

    if not doc:
        await message.reply(
            "⚠️ <b>راهنمای بازیابی دیتابیس شهر تیرامیکس:</b>\n\n"
            "برای بازیابی اطلاعات ذخیره‌شده بازیکنان:\n"
            "۱. روی فایل پشتیبان دیتابیس <b>ریپلای</b> کرده و بنویسید: <code>/restore</code>\n"
            "۲. یا فایل دیتابیس را همراه با کپشن <code>/restore</code> ارسال کنید."
        )
        return

    status_msg = await message.reply("⏳ در حال دریافت فایل پشتیبان و بررسی سلامت دیتابیس...")
    temp_dir = tempfile.gettempdir()
    temp_file = Path(temp_dir) / f"incoming_restore_{int(time.time())}.db"

    try:
        await bot.download(doc, destination=temp_file)
        from services.backup import restore_database_from_file

        success, note = await restore_database_from_file(temp_file)
        if success:
            await status_msg.edit_text(f"✅ <b>{note}</b>")
        else:
            await status_msg.edit_text(f"❌ <b>بازیابی ناموفق بود:</b>\n{note}")
    except Exception as exc:
        logger.exception("Restore failed: %s", exc)
        await status_msg.edit_text(f"❌ خطا در فرآیند بازیابی: <code>{esc(str(exc))}</code>")
    finally:
        with contextlib.suppress(Exception):
            if temp_file.exists():
                temp_file.unlink()


@router.message(
    F.text.startswith("چیت کانال")
    | F.text.startswith("کانال اجباری")
    | F.text.startswith("/channel")
)
async def cmd_admin_channels(message: Message) -> None:
    """مدیریت کانال‌های عضویت اجباری توسط مالک و ادمین‌های ربات."""
    user = message.from_user
    if user is None or not is_admin_or_owner(user.id):
        return

    from config import settings
    from services import channels

    raw = (message.text or "").strip()
    parts = raw.split()
    # Find action: skip command trigger (e.g. "چیت کانال", "/channel")
    if parts and parts[0] in ("/channel", "/channels"):
        sub_parts = parts[1:]
    elif (
        len(parts) >= 2
        and (
            (parts[0] == "چیت" and parts[1] == "کانال")
            or (parts[0] == "کانال" and parts[1] == "اجباری")
        )
    ):
        sub_parts = parts[2:]
    else:
        sub_parts = parts[1:]

    action = sub_parts[0].lower() if sub_parts else "list"

    if action in ("روشن", "on", "enable", "فعال"):
        settings.force_channel_join = True
        await message.reply("✅ <b>عضویت اجباری کانال‌ها با موفقیت فعال (روشن) شد!</b>")
        return

    if action in ("خاموش", "off", "disable", "غیرفعال"):
        settings.force_channel_join = False
        await message.reply("🛑 <b>عضویت اجباری کانال‌ها غیرفعال (خاموش) شد!</b>")
        return

    if action in ("افزودن", "add", "ثبت") and len(sub_parts) >= 2:
        username = sub_parts[1]
        name = " ".join(sub_parts[2:]).strip() if len(sub_parts) > 2 else username
        res = await channels.add_mandatory_channel(name=name, username=username)
        await message.reply(
            f"✅ <b>کانال به عضویت اجباری اضافه شد:</b>\n\n"
            f"📢 نام: <b>{res['name']}</b>\n"
            f"🆔 یوزرنیم: <code>{res['username']}</code>\n"
            f"🔗 لینک: {res['url']}\n\n"
            "⚠️ <i>توجه: ربات باید در کانال ادمین باشد تا عضویت را استعلام کند!</i>"
        )
        return

    if action in ("حذف", "del", "remove") and len(sub_parts) >= 2:
        username = sub_parts[1]
        ok = await channels.remove_mandatory_channel(username)
        if ok:
            await message.reply(f"🗑 کانال <code>{username}</code> از لیست عضویت اجباری حذف شد.")
        else:
            await message.reply(f"⚠️ کانال <code>{username}</code> در دیتابیس یافت نشد.")
        return

    # Default / list view
    cur_channels = await channels.get_active_channels()
    status_str = "🟢 روشن (فعال)" if settings.force_channel_join else "🔴 خاموش (غیرفعال)"
    lines = [
        "📢 <b>مدیریت کانال‌های عضویت اجباری (Sponsorship & Join Gate)</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        f"⚙️ <b>وضعیت سیستم:</b> {status_str}",
        f"📊 <b>تعداد کانال‌ها:</b> {len(cur_channels)} کانال فعال\n",
        "📋 <b>لیست کانال‌های ثبت‌شده:</b>",
    ]
    for idx, ch in enumerate(cur_channels, 1):
        lines.append(
            f"{idx}. <b>{ch.get('name', 'بدون نام')}</b> ({ch.get('username', '')})\n"
            f"   🔗 {ch.get('url', '')}"
        )

    lines.append("\n📌 <b>دستورات مدیریت:</b>")
    lines.append("• <code>چیت کانال روشن</code> — فعال‌سازی قفل عضویت اجباری")
    lines.append("• <code>چیت کانال خاموش</code> — غیرفعال‌سازی موقت")
    lines.append("• <code>چیت کانال افزودن @username [نام]</code> — اضافه کردن کانال جدید")
    lines.append("• <code>چیت کانال حذف @username</code> — حذف کانال")
    lines.append("\n⚠️ <b>نکته:</b> ربات حتماً باید در کانال‌ها <b>ادمین (Admin)</b> باشد.")

    await message.reply("\n".join(lines))
