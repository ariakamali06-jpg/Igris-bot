"""Admin & Creator Cheat Codes for Igris Bot.

Allows the project owner (Rex Lapis: 5765828495) or authorized admins to inspect
and modify player attributes (level, education, credits, bank, energy, stats, job, jail)
for themselves or for other players via reply, username, or user ID.
"""

from __future__ import annotations

import logging
from aiogram import Router
from aiogram.filters import CommandObject
from aiogram.types import Message

from config import settings
from database.connection import db
from handlers.common import CommandOrText, esc, hydrate, is_admin_or_owner
from models.enums import ActivityKind
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
    "🔹 <code>چیت سواد [۰ تا ۵]</code> — تغییر مدرک تحصیلی (۰: بی‌سواد، ۱: دیپلم، ۲: فوق‌دیپلم، ۳: لیسانس، ۴: فوق‌لیسانس، ۵: دکتری)\n"
    "🔹 <code>چیت پول [عدد]</code> — تنظیم سکه کیف پول\n"
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
)

_EDU_LABELS = {
    0: "۰ (بی‌سواد / سیکل)",
    1: "۱ (دیپلم)",
    2: "۲ (کاردانی / فوق‌دیپلم)",
    3: "۳ (کارشناسی / لیسانس)",
    4: "۴ (کارشناسی ارشد / فوق‌لیسانس)",
    5: "۵ (دکتری / پروفسور)",
}


async def _resolve_target(message: Message, tokens: list[str], caller_id: int) -> tuple[int, list[str]]:
    """Resolve target user_id and return remaining tokens.

    Priority:
    1. If message is a reply to another user -> that user.
    2. If first token is @username or numeric user_id -> that user.
    3. Default -> caller_id (self).
    """
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
        prefix_words=("چیت", "تقلب", "کد تقلب", "ادمین", "cheat", "admin", "/cheat", "/admin", "/set"),
    )
)
async def cmd_cheat(message: Message, command: CommandObject | None = None) -> None:
    user = message.from_user
    if user is None:
        return

    # Strictly limit to owner or admins
    if not is_admin_or_owner(user.id):
        # Silently ignore non-admins so players don't know the command exists
        return

    raw_text = (command.args if command and command.args else (message.text or "")).strip()

    # Split into words and clean the command keyword itself
    parts = raw_text.split()
    if parts and parts[0].lower().startswith(("/", "چیت", "تقلب", "ادمین", "cheat", "admin", "set")):
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
        # 1. Level (لول / سطح)
        if action in ("لول", "سطح", "level", "lvl"):
            if not val_str.isdigit():
                await message.reply("⚠️ لطفاً عدد لول را مشخص کنید. مثال: <code>چیت لول 10</code>")
                return
            new_val = max(1, int(val_str))
            async with db.write() as conn:
                await conn.execute("UPDATE players SET level = ? WHERE user_id = ?", (new_val, target_id))
            await message.reply(
                f"⚡ <b>کد تقلب با موفقیت اجرا شد!</b> 🛠\n\n"
                f"👤 <b>هدف:</b> {esc(target_player.display_tag)} (<code>{target_id}</code>)\n"
                f"📊 <b>تغییر:</b> لول به <b>{new_val:,}</b> تغییر یافت."
            )
            return

        # 2. Education (سواد / تحصیلات / مدرک)
        if action in ("سواد", "تحصیلات", "مدرک", "edu", "education"):
            if not val_str.isdigit():
                await message.reply("⚠️ لطفاً سطح سواد را بین ۰ تا ۵ مشخص کنید. مثال: <code>چیت سواد 3</code>")
                return
            new_val = max(0, min(5, int(val_str)))
            async with db.write() as conn:
                await conn.execute("UPDATE players SET education_level = ? WHERE user_id = ?", (new_val, target_id))
            edu_name = _EDU_LABELS.get(new_val, str(new_val))
            await message.reply(
                f"⚡ <b>کد تقلب با موفقیت اجرا شد!</b> 🛠\n\n"
                f"👤 <b>هدف:</b> {esc(target_player.display_tag)} (<code>{target_id}</code>)\n"
                f"🎓 <b>تغییر:</b> مدرک تحصیلی به <b>{edu_name}</b> تنظیم شد."
            )
            return

        # 3. Credits / Money (پول / سکه / نقد)
        if action in ("پول", "سکه", "نقد", "money", "cash", "credits", "coin", "coins"):
            if not val_str.isdigit():
                await message.reply("⚠️ لطفاً مبلغ سکه را مشخص کنید. مثال: <code>چیت پول 100000</code>")
                return
            new_val = int(val_str)
            async with db.write() as conn:
                await conn.execute("UPDATE wallets SET credits = ? WHERE user_id = ?", (new_val, target_id))
            await message.reply(
                f"⚡ <b>کد تقلب با موفقیت اجرا شد!</b> 🛠\n\n"
                f"👤 <b>هدف:</b> {esc(target_player.display_tag)} (<code>{target_id}</code>)\n"
                f"💰 <b>تغییر:</b> موجودی کیف پول به <b>{new_val:,}</b> سکه تنظیم شد."
            )
            return

        # 4. Bank Balance (بانک / حساب)
        if action in ("بانک", "سپرده", "bank"):
            if not val_str.isdigit():
                await message.reply("⚠️ لطفاً مبلغ بانک را مشخص کنید. مثال: <code>چیت بانک 500000</code>")
                return
            new_val = int(val_str)
            async with db.write() as conn:
                await conn.execute("UPDATE players SET bank_balance = ? WHERE user_id = ?", (new_val, target_id))
            await message.reply(
                f"⚡ <b>کد تقلب با موفقیت اجرا شد!</b> 🛠\n\n"
                f"👤 <b>هدف:</b> {esc(target_player.display_tag)} (<code>{target_id}</code>)\n"
                f"🏛 <b>تغییر:</b> موجودی حساب بانکی به <b>{new_val:,}</b> سکه تنظیم شد."
            )
            return

        # 5. Energy (انرژی)
        if action in ("انرژی", "energy"):
            if not val_str.isdigit():
                await message.reply("⚠️ لطفاً مقدار انرژی را مشخص کنید. مثال: <code>چیت انرژی 100</code>")
                return
            new_val = max(0, int(val_str))
            async with db.write() as conn:
                await conn.execute("UPDATE players SET energy = ? WHERE user_id = ?", (new_val, target_id))
            await message.reply(
                f"⚡ <b>کد تقلب با موفقیت اجرا شد!</b> 🛠\n\n"
                f"👤 <b>هدف:</b> {esc(target_player.display_tag)} (<code>{target_id}</code>)\n"
                f"⚡ <b>تغییر:</b> انرژی به <b>{new_val:,}</b> واحد تنظیم شد."
            )
            return

        # 6. Attack (اتک / حمله)
        if action in ("اتک", "حمله", "atk", "attack"):
            if not val_str.isdigit():
                await message.reply("⚠️ لطفاً مقدار حمله را مشخص کنید. مثال: <code>چیت اتک 50</code>")
                return
            new_val = max(1, int(val_str))
            async with db.write() as conn:
                await conn.execute("UPDATE players SET base_atk = ? WHERE user_id = ?", (new_val, target_id))
            await message.reply(
                f"⚡ <b>کد تقلب با موفقیت اجرا شد!</b> 🛠\n\n"
                f"👤 <b>هدف:</b> {esc(target_player.display_tag)} (<code>{target_id}</code>)\n"
                f"⚔️ <b>تغییر:</b> حمله پایه به <b>{new_val:,}</b> واحد تنظیم شد."
            )
            return

        # 7. Defense (دفاع)
        if action in ("دفاع", "def", "defense"):
            if not val_str.isdigit():
                await message.reply("⚠️ لطفاً مقدار دفاع را مشخص کنید. مثال: <code>چیت دفاع 50</code>")
                return
            new_val = max(1, int(val_str))
            async with db.write() as conn:
                await conn.execute("UPDATE players SET base_def = ? WHERE user_id = ?", (new_val, target_id))
            await message.reply(
                f"⚡ <b>کد تقلب با موفقیت اجرا شد!</b> 🛠\n\n"
                f"👤 <b>هدف:</b> {esc(target_player.display_tag)} (<code>{target_id}</code>)\n"
                f"🛡 <b>تغییر:</b> دفاع پایه به <b>{new_val:,}</b> واحد تنظیم شد."
            )
            return

        # 8. Drip (دریپ / تیپ / استایل)
        if action in ("دریپ", "تیپ", "استایل", "drip", "style"):
            if not val_str.isdigit():
                await message.reply("⚠️ لطفاً مقدار دریپ را مشخص کنید. مثال: <code>چیت دریپ 50</code>")
                return
            new_val = max(0, int(val_str))
            async with db.write() as conn:
                await conn.execute("UPDATE players SET base_drip = ? WHERE user_id = ?", (new_val, target_id))
            await message.reply(
                f"⚡ <b>کد تقلب با موفقیت اجرا شد!</b> 🛠\n\n"
                f"👤 <b>هدف:</b> {esc(target_player.display_tag)} (<code>{target_id}</code>)\n"
                f"✨ <b>تغییر:</b> استایل/دریپ پایه به <b>{new_val:,}</b> واحد تنظیم شد."
            )
            return

        # 9. Job (شغل)
        if action in ("شغل", "job"):
            job_name = " ".join(tokens[1:]).strip() if len(tokens) > 1 else "بیکار"
            async with db.write() as conn:
                await conn.execute("UPDATE players SET job = ? WHERE user_id = ?", (job_name, target_id))
            await message.reply(
                f"⚡ <b>کد تقلب با موفقیت اجرا شد!</b> 🛠\n\n"
                f"👤 <b>هدف:</b> {esc(target_player.display_tag)} (<code>{target_id}</code>)\n"
                f"💼 <b>تغییر:</b> عنوان شغلی به <b>«{esc(job_name)}»</b> تغییر یافت."
            )
            return

        # 10. Jail Release (آزادی از زندان)
        if action in ("ازادی", "آزادی", "زندان", "free", "jail"):
            async with db.write() as conn:
                await conn.execute("UPDATE players SET is_jailed_until = 0 WHERE user_id = ?", (target_id,))
            await message.reply(
                f"⚡ <b>کد تقلب با موفقیت اجرا شد!</b> 🛠\n\n"
                f"👤 <b>هدف:</b> {esc(target_player.display_tag)} (<code>{target_id}</code>)\n"
                f"🕊 <b>تغییر:</b> بازیکن فوراً از زندان آزاد شد و سابقه بازداشت پاک گردید!"
            )
            return

        # 11. God Mode / Max (مکس / خدا)
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
                await conn.execute("UPDATE wallets SET credits = 1000000 WHERE user_id = ?", (target_id,))
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

        # 12. Reset Onboarding (ریست کاراکتر)
        if action in ("ریست", "reset"):
            async with db.write() as conn:
                await conn.execute("UPDATE players SET onboarding_completed = 0 WHERE user_id = ?", (target_id,))
            await message.reply(
                f"⚡ <b>کد تقلب با موفقیت اجرا شد!</b> 🛠\n\n"
                f"👤 <b>هدف:</b> {esc(target_player.display_tag)} (<code>{target_id}</code>)\n"
                f"🔄 <b>تغییر:</b> وضعیت ثبت‌نام صفر شد. بازیکن می‌تواند مجدداً ویزارد ساخت کاراکتر را طی کند."
            )
            return

        # 13. Player Stats Info (وضعیت)
        if action in ("وضعیت", "status", "info"):
            updated = await game.load_player(target_id)
            c, d = await economy.balances(target_id)
            edu_title = _EDU_LABELS.get(updated.education_level if updated else 0, "نامشخص")
            await message.reply(
                f"📋 <b>مشخصات فنی کاراکتر در دیتابیس:</b>\n\n"
                f"👤 نام: <b>{esc(updated.display_name if updated else 'نامشخص')}</b> (<code>{target_id}</code>)\n"
                f"🔹 لول: <b>{updated.level if updated else 1}</b> (تجربه: {updated.exp if updated else 0})\n"
                f"🎓 سطح سواد: <b>{edu_title}</b>\n"
                f"💼 شغل: <b>{esc(updated.job if updated else 'بیکار')}</b>\n"
                f"💰 موجودی نقد: <b>{c:,}</b> سکه\n"
                f"🏛 موجودی بانک: <b>{updated.bank_balance if updated else 0:,}</b> سکه\n"
                f"⚡ انرژی: <b>{updated.energy if updated else 100} / 200</b>\n"
                f"⚔️ اتک پایه: <b>{updated.base_atk if updated else 10}</b>\n"
                f"🛡 دفاع پایه: <b>{updated.base_def if updated else 8}</b>\n"
                f"✨ دریپ پایه: <b>{updated.base_drip if updated else 5}</b>\n"
                f"🆔 وضعیت آنبوردینگ: <b>{'تکمیل‌شده' if (updated and updated.onboarding_completed == 1) else 'تکمیل‌نشده'}</b>"
            )
            return

        # Unknown action -> show help
        await message.reply(
            f"⚠️ دستور تقلب ناشناخته است: <code>{esc(action)}</code>\n\n{_HELP_TEXT}"
        )

    except Exception as exc:  # noqa: BLE001
        logger.exception("Admin cheat failed for user %s: %s", user.id, exc)
        await message.reply(f"❌ خطا در اجرای کد تقلب: <code>{esc(str(exc))}</code>")
