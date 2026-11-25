"""هندلرهای مدیریت و کدهای تقلب سازنده ربات تیرامیکس (Developer & Admin Cheats).

این ماژول به مالک پروژه و ادمین‌های مجاز اجازه می‌دهد مشخصات بازیکنان
(لول، تحصیلات، رکسار نقد، موجودی بانک، انرژی، قدرت حمله، دفاع، استایل، شغل و زندان)
را برای خود یا سایر بازیکنان (از طریق ریپلای، یوزرنیم یا آیدی عددی) تنظیم کنند.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import tempfile
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from aiogram import F, Router
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter
from aiogram.filters import CommandObject
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from database.connection import db
from handlers.common import (
    CommandOrText,
    esc,
    extract_args,
    hydrate,
    is_admin_or_owner,
)
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
    "🔹 <code>چیت پاداش [مبلغ]</code> (یا <code>/airdrop</code>) — واریز رکسار به همه\n"
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


def _parse_amount(raw: str) -> int | None:
    """پارس مبالغ با پشتیبانی از کاما، پسوندهای k و m و علائم فارسی."""
    cleaned = (
        raw.replace(",", "")
        .replace("_", "")
        .replace("،", "")
        .strip()
        .lower()
    )
    mult = 1
    if cleaned.endswith("k"):
        mult = 1_000
        cleaned = cleaned[:-1]
    elif cleaned.endswith("m"):
        mult = 1_000_000
        cleaned = cleaned[:-1]
    elif cleaned.endswith("b"):
        mult = 1_000_000_000
        cleaned = cleaned[:-1]

    if cleaned.isdigit():
        val = int(cleaned) * mult
        return val if val > 0 else None
    return None


async def _execute_global_airdrop(message: Message, amount: int) -> None:
    """واریز اتمیک رکسار همگانی به کیف پول تمام بازیکنان بازی توسط سازنده."""
    now_ts = int(time.time())
    async with db.write() as conn:
        # ۱. اطمینان از وجود سطر کیف پول برای تمام بازیکنان
        await conn.execute(
            """
            INSERT INTO wallets (user_id, credits)
            SELECT user_id, 0 FROM players
            WHERE user_id NOT IN (SELECT user_id FROM wallets)
            """
        )
        # ۲. افزایش اعتبار تمام بازیکنان
        cur = await conn.execute(
            """
            UPDATE wallets SET credits = credits + ?
            WHERE user_id IN (SELECT user_id FROM players)
            """,
            (amount,),
        )
        player_count = cur.rowcount
        if player_count == 0:
            await message.reply("⚠️ هیچ بازیکنی در دیتابیس بازی یافت نشد.")
            return

        # ۳. ثبت در دفتر کل حسابداری (ledger)
        await conn.execute(
            """
            INSERT INTO ledger (
                user_id, kind, credits_delta, shards_delta,
                balance_after_credits, balance_after_shards,
                ref, created_at
            )
            SELECT
                w.user_id, ?, ?, 0,
                w.credits, w.soul_shards,
                'admin:global_airdrop', ?
            FROM wallets w
            WHERE w.user_id IN (SELECT user_id FROM players)
            """,
            (ActivityKind.ADMIN.value, amount, now_ts),
        )

    total_injected = amount * player_count
    await message.reply(
        f"👑 <b>پاداش همگانی سازنده با موفقیت واریز شد!</b> 🎉\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 مبلغ به ازای هر بازیکن: <b>{amount:,}</b> رکسار\n"
        f"👥 تعداد دریافت‌کنندگان: <b>{player_count:,}</b> نفر\n"
        f"💎 مجموع رکسار تزریق‌شده: <b>{total_injected:,}</b> رکسار\n\n"
        f"✨ <i>مبلغ به طور مستقیم به کیف پول تمام شهروندان تیرامیکس اضافه شد.</i>"
    )


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


_CHEAT_ARG_WORDS = {
    # کدهای تقلب
    "لول", "سطح", "level", "lvl",
    "سواد", "تحصیلات", "مدرک", "edu", "education",
    "پول", "سکه", "نقد", "money", "cash", "credits", "coin", "coins",
    "بانک", "سپرده", "bank",
    "انرژی", "energy",
    "اتک", "حمله", "atk", "attack",
    "دفاع", "def", "defense",
    "دریپ", "تیپ", "استایل", "drip", "style",
    "شغل", "job",
    "ازادی", "آزادی", "زندان", "free", "jail",
    "مکس", "max", "god",
    "ریست", "reset", "reset_char",
    "وضعیت", "status", "info",
    # پاداش همگانی (چیت پاداش/عیدی/ایردراپ)
    "پاداش", "عیدی", "ایردراپ", "airdrop", "giveall",
    "پاداش_همگانی", "پاداش‌همگانی", "جایزه_همگانی", "همگانی",
}


@router.message(
    CommandOrText(
        ["cheat", "admin", "set"],
        words={"چیت", "تقلب", "کد تقلب", "ادمین"},
        arg_words=_CHEAT_ARG_WORDS,
        name_tail=True,
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

    parts = _cheat_tokens(message, command)

    # دستورات سراسری و همگانی (پاداش همگانی به تمام بازیکنان)
    if parts and parts[0].lower() in _GLOBAL_AIRDROP_WORDS:
        await _cheat_airdrop(message, parts)
        return

    # شناسایی هدف: خود ادمین، ریپلای روی پیام هدف یا آیدی/یوزرنیم
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
    ctx = _CheatContext(message, target_id, target_player.display_tag)

    try:
        spec = _FIELD_CHEATS.get(action)
        if spec is not None:
            await _cheat_set_field(ctx, val_str, spec)
            return
        special = _SPECIAL_CHEATS.get(action)
        if special is not None:
            await special(ctx, tokens)
            return

        # دستور نامعتبر
        await message.reply(
            f"⚠️ دستور تقلب ناشناخته است: <code>{esc(action)}</code>\n\n{_HELP_TEXT}"
        )

    except Exception as exc:  # noqa: BLE001
        logger.exception("Admin cheat failed for user %s: %s", user.id, exc)
        await message.reply(f"❌ خطا در اجرای کد تقلب: <code>{esc(str(exc))}</code>")


# ---------------------------------------------------------------------------
# زیرساخت کدهای تقلب: هر کد، یک رکورد کوچک + جدول dispatch؛ بدون زنجیره if/elif
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _CheatContext:
    """بافت اجرای کد تقلب: پیام، شناسه هدف و برچسب نمایشی او."""

    message: Message
    target_id: int
    tag: str


@dataclass(frozen=True)
class _FieldCheat:
    """مشخصات یک کد تقلب تنظیم فیلد: ستون دیتابیس، محدوده و پیام‌های استاندارد."""

    column: str
    usage: str
    change: Callable[[int], str]
    kind: str = "int"
    minimum: int | None = None
    maximum: int | None = None
    table: str = "players"
    timestamp_col: str | None = None


def _cheat_tokens(message: Message, command: CommandObject | None) -> list[str]:
    """جدا کردن پیشوند فرمان (چیت/تقلب/ادمین/...) و برگرداندن توکن‌های باقی‌مانده."""
    raw_text = (command.args if command and command.args else (message.text or "")).strip()
    parts = raw_text.split()
    cmd_prefixes = ("/", "چیت", "تقلب", "ادمین", "cheat", "admin", "set")
    if parts and parts[0].lower().startswith(cmd_prefixes):
        parts = parts[1:]
    return parts


_GLOBAL_AIRDROP_WORDS = frozenset(
    {
        "پاداش", "عیدی", "ایردراپ", "airdrop", "giveall",
        "پاداش_همگانی", "پاداش‌همگانی", "جایزه_همگانی", "همگانی",
    }
)


async def _cheat_airdrop(message: Message, parts: list[str]) -> None:
    """پاداش همگانی: واریز رکسار به حساب تمام بازیکنان بازی."""
    val_str = parts[1] if len(parts) > 1 else ""
    parsed = _parse_amount(val_str)
    if not parsed:
        await message.reply(
            "⚠️ لطفاً مبلغ پاداش همگانی را مشخص کنید.\n"
            "مثال: <code>چیت پاداش 50000</code> یا <code>چیت عیدی 100k</code>"
        )
        return
    await _execute_global_airdrop(message, parsed)


async def _reply_cheat_ok(ctx: _CheatContext, change_line: str) -> None:
    """گزارش استاندارد موفقیت کد تقلب به‌همراه هدف و تغییر اعمال‌شده."""
    await ctx.message.reply(
        f"⚡ <b>کد تقلب با موفقیت اجرا شد!</b> 🛠\n\n"
        f"👤 <b>هدف:</b> {esc(ctx.tag)} (<code>{ctx.target_id}</code>)\n"
        f"{change_line}"
    )


async def _cheat_set_field(ctx: _CheatContext, val_str: str, spec: _FieldCheat) -> None:
    """اعتبارسنجی مقدار، نوشتن در دیتابیس و گزارش استاندارد تغییر فیلد عددی."""
    if spec.kind == "int":
        if not val_str.isdigit():
            await ctx.message.reply(spec.usage)
            return
        new_val: int = int(val_str)
    else:
        parsed = _parse_amount(val_str)
        if not parsed and val_str != "0":
            await ctx.message.reply(spec.usage)
            return
        new_val = parsed if parsed is not None else 0

    if spec.minimum is not None:
        new_val = max(spec.minimum, new_val)
    if spec.maximum is not None:
        new_val = min(spec.maximum, new_val)

    async with db.write() as conn:
        if spec.timestamp_col:
            await conn.execute(
                f"UPDATE {spec.table} SET {spec.column} = ?, {spec.timestamp_col} = ? "
                "WHERE user_id = ?",
                (new_val, int(time.time()), ctx.target_id),
            )
        else:
            await conn.execute(
                f"UPDATE {spec.table} SET {spec.column} = ? WHERE user_id = ?",
                (new_val, ctx.target_id),
            )
    await _reply_cheat_ok(ctx, spec.change(new_val))


async def _cheat_job(ctx: _CheatContext, tokens: list[str]) -> None:
    """تغییر موقعیت شغلی (Job)."""
    job_name = " ".join(tokens[1:]).strip() if len(tokens) > 1 else "بیکار"
    async with db.write() as conn:
        await conn.execute(
            "UPDATE players SET job = ? WHERE user_id = ?",
            (job_name, ctx.target_id),
        )
    await _reply_cheat_ok(
        ctx, f"💼 <b>تغییر:</b> عنوان شغلی به <b>«{esc(job_name)}»</b> تغییر یافت."
    )


async def _cheat_free(ctx: _CheatContext, tokens: list[str]) -> None:
    """آزادی فوری از بازداشتگاه (Jail Release)."""
    async with db.write() as conn:
        await conn.execute(
            "UPDATE players SET is_jailed_until = 0 WHERE user_id = ?",
            (ctx.target_id,),
        )
    await _reply_cheat_ok(
        ctx,
        "🕊 <b>تغییر:</b> بازیکن فوراً از زندان آزاد شد و سابقه بازداشت پاک گردید!",
    )


async def _cheat_god(ctx: _CheatContext, tokens: list[str]) -> None:
    """فعال‌سازی حالت خدا و ارتقای کامل (God Mode / Max)."""
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
            (ctx.target_id,),
        )
        await conn.execute(
            "UPDATE wallets SET credits = 1000000 WHERE user_id = ?",
            (ctx.target_id,),
        )
    await ctx.message.reply(
        f"⚡ <b>حالت خدا (God Mode / Max) فعال شد!</b> 👑🔥\n\n"
        f"👤 <b>هدف:</b> {esc(ctx.tag)} (<code>{ctx.target_id}</code>)\n"
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


async def _cheat_reset(ctx: _CheatContext, tokens: list[str]) -> None:
    """بازنشانی وضعیت ویزارد ثبت‌نام و حذف کامل کاراکتر (Reset Character)."""
    async with db.write() as conn:
        await conn.execute("DELETE FROM player_avatar WHERE user_id = ?", (ctx.target_id,))
        await conn.execute("DELETE FROM player_wardrobe WHERE user_id = ?", (ctx.target_id,))
        await conn.execute("DELETE FROM loadout WHERE user_id = ?", (ctx.target_id,))
        await conn.execute("DELETE FROM inventory WHERE user_id = ?", (ctx.target_id,))
        await conn.execute("DELETE FROM contracts WHERE user_id = ?", (ctx.target_id,))
        await conn.execute("DELETE FROM properties WHERE owner_id = ?", (ctx.target_id,))
        await conn.execute("DELETE FROM holdings WHERE user_id = ?", (ctx.target_id,))
        await conn.execute("DELETE FROM safe_locks WHERE user_id = ?", (ctx.target_id,))
        await conn.execute("DELETE FROM wallets WHERE user_id = ?", (ctx.target_id,))
        await conn.execute("DELETE FROM players WHERE user_id = ?", (ctx.target_id,))
    await ctx.message.reply(
        f"⚡ <b>حساب کاربری و پروفایل به طور کامل ریست شد!</b> 🛠\n\n"
        f"👤 <b>هدف:</b> {esc(ctx.tag)} (<code>{ctx.target_id}</code>)\n"
        "🔄 <b>تغییر:</b> تمام سوابق، شناسنامه و کمد لباس کاراکتر کاملاً صفر شد.\n"
        "اکنون می‌توانید با ارسال <code>/start</code> کاراکتر جدید خود را از ابتدا بسازید."
    )


async def _cheat_status(ctx: _CheatContext, tokens: list[str]) -> None:
    """استعلام فنی اطلاعات دیتابیس (Status Info)."""
    updated = await game.load_player(ctx.target_id)
    c, d = await economy.balances(ctx.target_id)
    edu_title = _EDU_LABELS.get(updated.education_level if updated else 0, "نامشخص")
    is_completed = updated and updated.onboarding_completed == 1
    await ctx.message.reply(
        f"📋 <b>مشخصات فنی کاراکتر در دیتابیس:</b>\n\n"
        f"👤 نام: <b>{esc(updated.display_name if updated else 'نامشخص')}</b> "
        f"(<code>{ctx.target_id}</code>)\n"
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


def _register(spec: _FieldCheat, *keys: str) -> dict[str, _FieldCheat]:
    """نگاشت همهٔ کلیدهای یک کد تقلب به رکورد مشخصاتش."""
    return dict.fromkeys(keys, spec)


_FIELD_CHEATS: dict[str, _FieldCheat] = {
    **_register(
        _FieldCheat(
            column="level",
            usage="⚠️ لطفاً عدد لول را مشخص کنید. مثال: <code>چیت لول 10</code>",
            change=lambda v: f"📊 <b>تغییر:</b> لول به <b>{v:,}</b> تغییر یافت.",
            minimum=1,
        ),
        "لول", "سطح", "level", "lvl",
    ),
    **_register(
        _FieldCheat(
            column="education_level",
            usage=(
                "⚠️ لطفاً سطح سواد را بین ۰ تا ۵ مشخص کنید. "
                "مثال: <code>چیت سواد 3</code>"
            ),
            change=lambda v: (
                f"🎓 <b>تغییر:</b> مدرک تحصیلی به <b>{_EDU_LABELS.get(v, str(v))}</b> تنظیم شد."
            ),
            minimum=0,
            maximum=5,
        ),
        "سواد", "تحصیلات", "مدرک", "edu", "education",
    ),
    **_register(
        _FieldCheat(
            column="credits",
            usage="⚠️ لطفاً مبلغ رکسار را مشخص کنید. مثال: <code>چیت پول 100000</code>",
            change=lambda v: f"💰 <b>تغییر:</b> موجودی کیف پول به <b>{v:,}</b> رکسار تنظیم شد.",
            kind="amount",
            table="wallets",
        ),
        "پول", "سکه", "نقد", "money", "cash", "credits", "coin", "coins",
    ),
    **_register(
        _FieldCheat(
            column="bank_balance",
            usage="⚠️ لطفاً مبلغ بانک را مشخص کنید. مثال: <code>چیت بانک 500000</code>",
            change=lambda v: (
                f"🏛 <b>تغییر:</b> موجودی حساب بانکی به <b>{v:,}</b> رکسار تنظیم شد."
            ),
            kind="amount",
        ),
        "بانک", "سپرده", "bank",
    ),
    **_register(
        _FieldCheat(
            column="energy",
            usage="⚠️ لطفاً مقدار انرژی را مشخص کنید. مثال: <code>چیت انرژی 100</code>",
            change=lambda v: f"⚡ <b>تغییر:</b> انرژی به <b>{v:,}</b> واحد تنظیم شد.",
            minimum=0,
            timestamp_col="energy_updated_at",
        ),
        "انرژی", "energy",
    ),
    **_register(
        _FieldCheat(
            column="base_atk",
            usage="⚠️ لطفاً مقدار حمله را مشخص کنید. مثال: <code>چیت اتک 50</code>",
            change=lambda v: f"⚔️ <b>تغییر:</b> حمله پایه به <b>{v:,}</b> واحد تنظیم شد.",
            minimum=1,
        ),
        "اتک", "حمله", "atk", "attack",
    ),
    **_register(
        _FieldCheat(
            column="base_def",
            usage="⚠️ لطفاً مقدار دفاع را مشخص کنید. مثال: <code>چیت دفاع 50</code>",
            change=lambda v: f"🛡 <b>تغییر:</b> دفاع پایه به <b>{v:,}</b> واحد تنظیم شد.",
            minimum=1,
        ),
        "دفاع", "def", "defense",
    ),
    **_register(
        _FieldCheat(
            column="base_drip",
            usage="⚠️ لطفاً مقدار دریپ را مشخص کنید. مثال: <code>چیت دریپ 50</code>",
            change=lambda v: f"✨ <b>تغییر:</b> استایل/دریپ پایه به <b>{v:,}</b> واحد تنظیم شد.",
            minimum=0,
        ),
        "دریپ", "تیپ", "استایل", "drip", "style",
    ),
}

_SPECIAL_CHEATS: dict[str, Callable[[_CheatContext, list[str]], Awaitable[None]]] = {
    **dict.fromkeys(("شغل", "job"), _cheat_job),
    **dict.fromkeys(("ازادی", "آزادی", "زندان", "free", "jail"), _cheat_free),
    **dict.fromkeys(("مکس", "max", "god"), _cheat_god),
    **dict.fromkeys(("ریست", "reset", "reset_char"), _cheat_reset),
    **dict.fromkeys(("وضعیت", "status", "info"), _cheat_status),
}



@router.message(
    CommandOrText(
        ["reset", "reset_char", "admin_reset_char"],
        words={"ریست", "ریست کاراکتر", "ریست_کاراکتر"},
    )
)
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


@router.message(
    CommandOrText(
        ["backup", "db_backup"],
        words={"بکاپ", "بک‌آپ", "پشتیبان", "backup", "چیت بکاپ"},
    )
)
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


@router.message(
    CommandOrText(
        ["restore", "db_restore"],
        words={"بازیابی", "ریستور", "restore", "چیت بازیابی"},
    )
)
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


_CHANNEL_ARG_WORDS = {
    "روشن", "خاموش", "فعال", "غیرفعال", "افزودن", "حذف", "ثبت", "لیست", "نمایش",
    "on", "off", "enable", "disable", "add", "remove", "del", "list", "show",
}


@router.message(
    CommandOrText(
        ["channel", "channels"],
        words={"چیت کانال", "کانال اجباری"},
        arg_words=_CHANNEL_ARG_WORDS,
        name_tail=True,
    )
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


@router.message(
    CommandOrText(
        ["airdrop", "giveall"],
        words={"ایردراپ", "پاداش همگانی", "عیدی همگانی"},
    )
)
async def cmd_direct_airdrop(message: Message, command: CommandObject | None = None) -> None:
    """دستور مستقیم و سریع واریز رکسار همگانی به تمام بازیکنان توسط سازنده/ادمین."""
    user = message.from_user
    if user is None or not is_admin_or_owner(user.id):
        return

    args = extract_args(message, command)
    raw_args = args[0] if args else ""

    parsed = _parse_amount(raw_args) if raw_args else None
    if not parsed:
        await message.reply(
            "🎁 <b>دستور پاداش همگانی سازنده (Global Airdrop):</b>\n\n"
            "برای واریز رکسار به حساب تمام بازیکنان بازی، مبلغ را مشخص کنید:\n"
            "• <code>/airdrop 50000</code>\n"
            "• <code>ایردراپ 100k</code>\n"
            "• <code>پاداش همگانی 250000</code>"
        )
        return

    await _execute_global_airdrop(message, parsed)


# ---------------------------------------------------------------------------
# پیام همگانی (Broadcast): ارسال پیام دلخواه ادمین به پیوی تمام بازیکنان
# ---------------------------------------------------------------------------

_BROADCAST_WORDS = {
    "پیام همگانی",
    "پیام سراسری",
    "پیام عمومی",
    "اطلاعیه همگانی",
    "اعلان همگانی",
}

# متن در انتظار تأیید برای هر ادمین: admin_id -> متن پیام
_PENDING_BROADCAST: dict[int, str] = {}


def _broadcast_keyboard(total: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"✅ ارسال به {total} بازیکن", callback_data="bc:send"
                ),
                InlineKeyboardButton(text="❌ لغو", callback_data="bc:cancel"),
            ]
        ]
    )


async def _try_edit(query: CallbackQuery, text: str) -> None:
    """ویرایش پیام کالبک‌کوئری؛ اگر پیام حذف/غیرقابل‌دسترس شده بود بی‌صدا رد شو."""
    msg = query.message
    if msg is not None and hasattr(msg, "edit_text"):
        with contextlib.suppress(Exception):
            await msg.edit_text(text)


@router.message(
    CommandOrText(
        ["broadcast", "announce"],
        words=_BROADCAST_WORDS,
        # هر متنی به‌عنوان بدنهٔ پیام مجاز است (برخلاف بقیه فرمان‌ها)
        arg_pattern=r".+",
    )
)
async def cmd_broadcast(message: Message, command: CommandObject | None = None) -> None:
    """آماده‌سازی پیام همگانی و گرفتن تأیید نهایی از ادمین پیش از ارسال."""
    user = message.from_user
    if user is None or not is_admin_or_owner(user.id):
        return

    body = " ".join(extract_args(message, command)).strip()
    if not body:
        await message.reply(
            "📢 <b>ارسال پیام همگانی</b>\n\n"
            "متن پیام را بعد از دستور بنویس:\n"
            "• <code>پیام همگانی فردا ساعت ۹ رویداد شهری داریم!</code>\n"
            "• <code>/broadcast سرور جدید راه افتاد 🎉</code>\n\n"
            "⚠️ پیام به پیوی همهٔ بازیکنان ثبت‌شده ارسال می‌شود "
            "(کسانی که ربات را بلاک کرده باشند دریافت نمی‌کنند)."
        )
        return

    async with db.read() as conn:
        cur = await conn.execute("SELECT COUNT(*) FROM players")
        row = await cur.fetchone()
    total = int(row[0]) if row else 0

    _PENDING_BROADCAST[user.id] = body
    await message.reply(
        f"📢 <b>پیش‌نمایش پیام همگانی</b>\n\n{esc(body)}\n\n"
        f"👥 مخاطبان: <b>{total}</b> بازیکن\n"
        "ارسال شود؟",
        reply_markup=_broadcast_keyboard(total),
    )


@router.callback_query(F.data == "bc:cancel")
async def cb_broadcast_cancel(query: CallbackQuery) -> None:
    """لغو ارسال پیام همگانی در انتظار تأیید."""
    user = query.from_user
    if user is None or not is_admin_or_owner(user.id):
        await query.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    _PENDING_BROADCAST.pop(user.id, None)
    await query.answer("لغو شد.")
    await _try_edit(query, "❌ ارسال پیام همگانی لغو شد.")


@router.callback_query(F.data == "bc:send")
async def cb_broadcast_send(query: CallbackQuery) -> None:
    """ارسال واقعی پیام همگانی به پیوی تمام بازیکنان با گزارش موفق/ناموفق."""
    user = query.from_user
    if user is None or not is_admin_or_owner(user.id):
        await query.answer("⛔ دسترسی ندارید.", show_alert=True)
        return

    body = _PENDING_BROADCAST.pop(user.id, None)
    if not body:
        await query.answer(
            "⚠️ پیامی برای ارسال موجود نیست. دستور را دوباره بزن.",
            show_alert=True,
        )
        return

    await query.answer("⏳ در حال ارسال…")

    async with db.read() as conn:
        cur = await conn.execute(
            "SELECT user_id FROM players ORDER BY last_seen DESC"
        )
        targets = [int(r[0]) for r in await cur.fetchall()]

    bot = query.bot
    sent = 0
    blocked = 0
    failed = 0
    for uid in targets:
        try:
            await bot.send_message(uid, body, parse_mode=None)
            sent += 1
        except TelegramRetryAfter as exc:
            await asyncio.sleep(exc.retry_after + 1)
            try:
                await bot.send_message(uid, body, parse_mode=None)
                sent += 1
            except Exception:  # noqa: BLE001
                failed += 1
        except TelegramForbiddenError:
            blocked += 1
        except Exception as exc:  # noqa: BLE001
            failed += 1
            logger.debug("broadcast to %s failed: %s", uid, exc)
        # احترام به محدودیت نرخ تلگرام (~۳۰ پیام در ثانیه)
        await asyncio.sleep(0.05)

    report = (
        "📢 <b>ارسال پیام همگانی پایان یافت</b>\n\n"
        f"✅ ارسال موفق: <b>{sent}</b>\n"
        f"🚫 بلاک کرده‌اند: <b>{blocked}</b>\n"
        f"⚠️ ناموفق: <b>{failed}</b>\n"
        f"👥 کل مخاطبان: <b>{len(targets)}</b>"
    )
    logger.info(
        "broadcast by %s: sent=%s blocked=%s failed=%s total=%s",
        user.id,
        sent,
        blocked,
        failed,
        len(targets),
    )
    if query.message is not None:
        await _try_edit(query, report)
    else:
        await query.bot.send_message(user.id, report)
