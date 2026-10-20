"""ابزارها و لوله‌کشی‌های مشترک هندلرهای بات تیرامیکس.

شامل:
- استخراج هویت و لودینگ بازیکن (hydrate)
- رندرینگ آواتار شفاف ۴ برابری اوراسکایز روی پس‌زمینه دارک (card_bytes)
- مدیریت یکپارچه خطاها و کول‌داون‌های بازی (answer_error)
- فیلترهای استاندارد فرامین متنی و اسلش (CommandOrText)
- شناسایی بازیکن هدف بر اساس ریپلای، آیدی یا یوزرنیم (resolve_target_player)
"""

from __future__ import annotations

import asyncio
import html
import io
import logging

from aiogram.filters import BaseFilter, CommandObject
from aiogram.types import CallbackQuery, Message
from PIL import Image

from models import Player
from services import economy
from services.compositor import render_card
from services.game import GameError, ensure_player, render_request

logger = logging.getLogger(__name__)

__all__ = [
    "esc",
    "fmt",
    "ensure_player",
    "hydrate",
    "card_bytes",
    "avatar_png_to_card_jpeg",
    "resolve_target_player",
    "answer_error",
    "message_error",
    "energy_bar",
    "editable_message",
    "extract_args",
    "CommandOrText",
    "is_admin_or_owner",
]


def is_admin_or_owner(user_id: int) -> bool:
    """بررسی اینکه آیا کاربر مالک اصلی پروژه (Rex Lapis) یا ادمین سرور است."""
    from config import settings

    return settings.is_admin(user_id) or user_id == 5765828495


class CommandOrText(BaseFilter):
    """فیلتر هوشمند و دوگانه تلگرام برای پذیرش همزمان دستورات اسلش و کلمات متنی فارسی."""

    def __init__(
        self,
        commands: list[str] | tuple[str, ...] | set[str] | None = None,
        words: set[str] | list[str] | tuple[str, ...] | None = None,
        prefix_words: set[str] | list[str] | tuple[str, ...] | None = None,
    ) -> None:
        self.commands = {c.lower() for c in (commands or ())}
        self.words = {w.lower() for w in (words or ())}
        self.prefix_words = tuple(p.lower() for p in (prefix_words or ()))

    async def __call__(self, message: Message) -> bool:
        if not message.text:
            return False
        text = message.text.strip()
        parts = text.split()
        if not parts:
            return False
        first_token = parts[0].lower()
        if first_token.startswith("/"):
            cmd = first_token[1:].split("@")[0]
            if cmd in self.commands:
                return True
        if first_token in self.words or text.lower() in self.words:
            return True
        return bool(self.prefix_words and text.lower().startswith(self.prefix_words))


def editable_message(call: CallbackQuery) -> Message | None:
    """استخراج پیام معتبر و قابل ویرایش از کال‌بک کوئری تلگرام."""
    message = call.message
    return message if isinstance(message, Message) else None


def esc(text: str | None) -> str:
    """ایمن‌سازی و اسکیپ کاراکترهای خطرناک HTML در متون ورودی کاربران."""
    return html.escape(text or "")


def fmt(value: int) -> str:
    """فرمت‌بندی زیبای ارقام اقتصادی (مثلاً 1.2M یا 15k)."""
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if value >= 10_000:
        return f"{value / 1_000:.1f}k"
    return f"{value:,}"


def extract_args(message: Message, command: CommandObject | None = None) -> list[str]:
    """استخراج آرگومان‌های ارسالی بعد از کلمه کلیدی دستور (چه با اسلش چه فارسی)."""
    if command and command.args:
        return list(command.args.split())
    text = (message.text or "").strip()
    parts = text.split()
    return list(parts[1:]) if len(parts) > 1 else []


async def hydrate(user_id: int, display_name: str, username: str | None) -> Player:
    """بارگذاری یا ثبت تنبل (Lazy Load) اطلاعات بازیکن بر اساس مشخصات کاربر تلگرام."""
    return await ensure_player(user_id, display_name, username)


def avatar_png_to_card_jpeg(png_bytes: bytes) -> bytes:
    """کامپوزیت تصویر شفاف آواتار اوراسکایز روی پس‌زمینه دارک نئونی و تبدیل به فرمت استاندارد JPEG.

    این تبدیل تضمین می‌کند تصویر بدون پس‌زمینه اوراسکایز، با کیفیت بالا (Quality 92)
    و هماهنگ با پس‌زمینه نئونی شهر تیرامیکس (18, 20, 29) روی تمامی کلاینت‌های تلگرام رندر شود.
    """
    avatar_img = Image.open(io.BytesIO(png_bytes)).convert("RGBA")
    w, h = avatar_img.size
    bg = Image.new("RGBA", (w, h), (18, 20, 29, 255))
    bg.alpha_composite(avatar_img)
    buffer = io.BytesIO()
    bg.convert("RGB").save(buffer, format="JPEG", quality=92, optimize=True)
    return buffer.getvalue()


async def card_bytes(player: Player) -> bytes:
    """رندر کارت شناسنامه و آواتار کاراکتر با کامپوزیت روی پس‌زمینه دارک.

    ابتدا تلاش می‌کند تصویر شفاف ۴ برابری زنده اوراسکایز بازیکن را با لباس‌های فعلی کمد دریافت کند.
    در صورت بروز هرگونه تایم‌اوت یا اختلال اینترنت، فوراً به رندرر محلی (Fallback) سوئیچ می‌کند
    تا کاربر هیچ‌وقت با پیام خطا یا تصویر ناقص مواجه نشود.
    """
    try:
        from services import avatar

        render_url = await avatar.get_player_render_url(player.user_id)
        if render_url:
            png_bytes = await avatar.fetch_upscaled_avatar(render_url, scale=4)
            if png_bytes:
                return avatar_png_to_card_jpeg(png_bytes)
    except Exception as exc:
        logger.debug(
            "card_bytes: everskies render failed for user %s: %s",
            player.user_id,
            exc,
        )

    # فال‌بک امن به کامپوزیتور آفلاین محلی
    request = render_request(player)
    return await asyncio.to_thread(render_card, request)


async def resolve_target_player(
    message: Message,
    token: str = "",
) -> Player | None:
    """شناسایی هوشمند بازیکن هدف در تعاملات چندنفره (دوئل، دزدی، روابط، اخاذی، حیوانات).

    اولویت تشخیص:
    ۱. ریپلای روی پیام کاربر مقصد در گروه.
    ۲. ارسال شناسه عددی کاربری تلگرام (User ID).
    ۳. ارسال نام کاربری تلگرام (با یا بدون @) و جستجوی دیتابیس شهروندان تیرامیکس.
    """
    # ۱. اگر پیام روی کاربر دیگری ریپلای شده باشد
    if message.reply_to_message and message.reply_to_message.from_user:
        target_user = message.reply_to_message.from_user
        return await hydrate(target_user.id, target_user.full_name, target_user.username)

    # ۲. اعتبارسنجی توکن متنی
    cleaned = token.lstrip("@").strip()
    if not cleaned:
        return None

    if cleaned.isdigit():
        user_id = int(cleaned)
        return await hydrate(user_id, f"Player {user_id}", None)

    # ۳. جستجو بر اساس یوزرنیم در پایگاه داده
    from database.connection import db
    from services.game import _load_player

    async with db.read() as conn:
        cursor = await conn.execute(
            "SELECT user_id, display_name FROM players WHERE LOWER(username) = LOWER(?) LIMIT 1",
            (cleaned,),
        )
        row = await cursor.fetchone()
        await cursor.close()
        if row:
            return await _load_player(conn, row["user_id"], row["display_name"])

    return None


async def answer_error(
    exc: Exception,
    *,
    callback: CallbackQuery | None = None,
    message: Message | None = None,
) -> None:
    """مدیریت و نمایش استاندارد خطاهای بازی به بازیکن به صورت روان و شفاف.

    خطاهای بازی و اتمام انرژی/کول‌داون به شکلی مدیریت می‌شوند که کاربر پیامی واضح دریافت کند
    و بات در مواجهه با خطاهای متداول هرگز کرش نکند.
    """
    text = _error_text(exc)
    if message is not None:
        await message.reply(text)
    elif callback is not None:
        msg = editable_message(callback)
        if msg is not None:
            await msg.reply(text)
        else:
            try:
                await callback.answer(text, show_alert=True)
            except Exception:  # noqa: BLE001 - در صورت پاسخ قبلی نادیده گرفته می‌شود
                logger.debug("callback alert dropped (already answered)", exc_info=True)
    logger.warning("surfaced domain error to chat: %s", exc, exc_info=exc)


def _error_text(exc: Exception) -> str:
    """تبدیل خطاهای اقتصادی، منطقی و کول‌داون به متن پیام‌های استاندارد."""
    if isinstance(exc, economy.OnCooldown):
        minutes, seconds = divmod(int(exc.remaining), 60)
        clock = f"{minutes}m {seconds:02d}s" if minutes else f"{seconds}s"
        return f"⏳ {exc.action} cools down in {clock}."
    if isinstance(exc, GameError):
        return f"⛔ {exc}"
    return "⛔ Something went wrong. Try again."


async def message_error(exc: Exception, message: Message) -> None:
    """ارسال سریع پیام خطا در پاسخ به پیام کاربر."""
    await message.reply(_error_text(exc))


def energy_bar(energy: int, max_energy: int, width: int = 10) -> str:
    """رسم گرافیکی نوار انرژی کاراکتر با کاراکترهای بصری."""
    filled = round(width * energy / max(1, max_energy))
    return "▮" * filled + "▯" * (width - filled)
