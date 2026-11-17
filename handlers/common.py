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
import re
from collections.abc import Callable, Collection
from typing import ClassVar, cast

from aiogram.filters import BaseFilter, CommandObject
from aiogram.types import CallbackQuery, InaccessibleMessage, Message
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
    """بررسی اینکه آیا کاربر در فهرست ادمین‌های پیکربندی‌شده (ADMIN_IDS) است."""
    from config import settings

    return settings.is_admin(user_id)


# ---------------------------------------------------------------------------
# قاعده مشترک فراخوانی دستورات متنی (Single Source of Truth)
#
# هر پیام متنی فقط وقتی «فرمان» شمرده می‌شود که یا دقیقاً خودِ کلید فرمان باشد
# (پیام خالی از هر متن اضافه) یا کلید فرمان به‌همراه آرگومان‌های مستندش آمده
# باشد: عدد/مبلغ، منشن @username، شناسه عددی، لینک، کلمات کلیدی مجاز همان
# دستور (arg_words) یا نام‌های شناخته‌شده (arg_names).
# هر متن جمله‌ای و گفتگویی — مثل «کار من خیلی سخته» — بی‌صدا نادیده گرفته
# می‌شود تا ربات در گروه‌ها مزاحم گفتگوهای عادی نشود.
# ---------------------------------------------------------------------------

_AMOUNT_RE = re.compile(r"^-?\d[\d,_]*(?:\.\d+)?[kKmM]?$")
_PERSIAN_DIGITS_RE = re.compile(r"^[۰-۹٠-٩][۰-۹٠-٩,_]*$")
_MENTION_RE = re.compile(r"^@[A-Za-z0-9_]{3,32}$")
_LINK_RE = re.compile(r"^(?:https?://|www\.|t\.me/)\S+$", re.IGNORECASE)


def _is_structured_arg(token: str) -> bool:
    """آرگومان‌های مستند و بدون ابهام: مبلغ/عدد، منشن، شناسه عددی یا لینک."""
    return bool(
        _AMOUNT_RE.match(token)
        or _PERSIAN_DIGITS_RE.match(token)
        or _MENTION_RE.match(token)
        or _LINK_RE.match(token)
    )


class CommandOrText(BaseFilter):
    """فیلتر هوشمند و دوگانه تلگرام برای پذیرش همزمان دستورات اسلش و کلمات متنی فارسی.

    قاعده سراسری (طراحی‌شده یک‌بار و استفاده‌شده در تمام کلیدها):
    در فرم متنی، پیام باید «خودِ فرمان» یا «فرمان + آرگومان مستند» باشد؛
    هر جمله یا متن گفتگویی که کلید فرمان داخل آن آمده باشد بی‌صدا رد می‌شود.
    """

    _REGISTRY: ClassVar[list[CommandOrText]] = []

    def __init__(
        self,
        commands: list[str] | tuple[str, ...] | set[str] | None = None,
        words: set[str] | list[str] | tuple[str, ...] | None = None,
        prefix_words: set[str] | list[str] | tuple[str, ...] | None = None,
        *,
        arg_words: set[str] | list[str] | tuple[str, ...] | None = None,
        name_tail: bool = False,
        arg_names: Collection[str] | Callable[[], Collection[str]] | None = None,
        arg_pattern: str | re.Pattern[str] | None = None,
    ) -> None:
        self.commands = {c.lower().lstrip("/") for c in (commands or ())}
        raw_phrases = list(words or ()) + list(prefix_words or ())
        cleaned = {p.casefold().strip().lstrip("/") for p in raw_phrases}
        # عبارت‌های بلندتر اول بررسی می‌شوند تا عبارت‌های چندکلمه‌ای درست بخورند
        self.phrases = tuple(sorted((p for p in cleaned if p), key=len, reverse=True))
        self.arg_words = {w.casefold() for w in (arg_words or ())}
        self.name_tail = name_tail
        self.arg_names = arg_names
        self.arg_pattern = (
            re.compile(arg_pattern) if isinstance(arg_pattern, str) else arg_pattern
        )
        CommandOrText._REGISTRY.append(self)

    async def __call__(self, message: Message) -> bool:
        if not message.text:
            return False
        return self.match_text(message.text)

    def match_text(self, text: str) -> bool:
        """تطابق خام متن با فرمان (بدون نیاز به شیء پیام) — مرجع واحد قاعده."""
        norm = " ".join(text.split()).casefold()
        if not norm:
            return False
        # ۱. فرم اسلش: /cmd آرگومان‌ها — اعلام صریح فرمان، آرگومان‌ها آزادند
        if norm.startswith("/"):
            head = norm.split()[0][1:].split("@")[0]
            return head in self.commands
        # ۲. فرم متنی: فقط «خودِ فرمان» یا «فرمان + آرگومان مستند»
        for phrase in self.phrases:
            if norm == phrase:
                return True
            if norm.startswith(phrase + " ") and self._args_ok(norm[len(phrase) + 1 :]):
                return True
        return False

    def _args_ok(self, args: str) -> bool:
        """اعتبارسنجی آرگومان‌های متنی: فقط شکل‌های مستند، نه متن جمله‌ای."""
        tokens = args.split()
        if not tokens:
            return True
        if self.arg_pattern is not None and self.arg_pattern.fullmatch(args):
            return True
        if self._known_name(args):
            return True
        keyword_seen = False
        for token in tokens:
            t = token.casefold()
            if t in self.arg_words:
                keyword_seen = True
                continue
            if _is_structured_arg(token):
                continue
            # نام آزاد (مثل نام کالا یا کلن) فقط بعد از کلمه کلیدی مجاز است
            if self.name_tail and keyword_seen:
                continue
            return False
        return True

    def _known_name(self, args: str) -> bool:
        """تطابق آرگومان با نام‌های شناخته‌شده (کاتالوگ غذا، عناوین شغل و ...)."""
        if not self.arg_names:
            return False
        names = self.arg_names() if callable(self.arg_names) else self.arg_names
        q = args.casefold()
        for name in names:
            n = str(name).casefold()
            if q == n or (len(q) >= 2 and (q in n or n in q)):
                return True
        return False

    @classmethod
    def is_game_command(cls, text: str) -> bool:
        """آیا این متن یک فراخوانی فرمان بازی است؟ (همان قاعده سراسری فیلترها)"""
        return any(f.match_text(text) for f in cls._REGISTRY)

    @classmethod
    def leading_phrase(cls, text: str) -> str | None:
        """بلندترین عبارت فرمان شناخته‌شده که ابتدای متن را می‌گیرد (برای جداسازی آرگومان‌ها)."""
        norm = " ".join(text.split()).casefold()
        best: str | None = None
        for f in cls._REGISTRY:
            for phrase in f.phrases:
                if (norm == phrase or norm.startswith(phrase + " ")) and (
                    best is None or len(phrase) > len(best)
                ):
                    best = phrase
        return best


def editable_message(call: CallbackQuery) -> Message | None:
    """استخراج پیام معتبر و قابل ویرایش از کال‌بک کوئری تلگرام.

    پیام‌های None و InaccessibleMessage رد می‌شوند؛ اشیاء سازگار (duck-typed)
    مثل استنداین‌های تست پذیرفته می‌شوند.
    """
    message = call.message
    if message is None or isinstance(message, InaccessibleMessage):
        return None
    return cast("Message", message)


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
    tokens = text.split()
    if not tokens:
        return []
    if tokens[0].startswith("/"):
        return tokens[1:]
    # عبارت فرمان — حتی چندکلمه‌ای مثل «کارت به کارت» — از ابتدای متن جدا می‌شود
    phrase = CommandOrText.leading_phrase(text)
    drop = len(phrase.split()) if phrase else 1
    return tokens[drop:]


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

    async with db.read() as conn:
        cursor = await conn.execute(
            "SELECT user_id, display_name FROM players WHERE LOWER(username) = LOWER(?) LIMIT 1",
            (cleaned,),
        )
        row = await cursor.fetchone()
        await cursor.close()
        if row:
            return await hydrate(int(row["user_id"]), str(row["display_name"]), cleaned)

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
    logger.exception("Unhandled domain error: %s", exc)
    return "⛔ Something went wrong. Try again."


async def message_error(exc: Exception, message: Message) -> None:
    """ارسال سریع پیام خطا در پاسخ به پیام کاربر."""
    await message.reply(_error_text(exc))


def energy_bar(energy: int, max_energy: int, width: int = 10) -> str:
    """رسم گرافیکی نوار انرژی کاراکتر با کاراکترهای بصری."""
    ratio = min(1.0, max(0.0, energy / max(1, max_energy)))
    filled = round(width * ratio)
    return "▮" * filled + "▯" * (width - filled)
