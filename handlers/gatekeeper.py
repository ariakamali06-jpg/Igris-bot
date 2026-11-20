"""میدل‌ور دروازه‌بان (Gatekeeper) شهر تیرامیکس.

الزام به ثبت‌نام و ساخت کاراکتر پیش از اجازه دسترسی به سایر دستورات ربات.
تا زمانی که کاربر شناسنامه نساخته باشد، ربات به هیچ دستوری پاسخ نمی‌دهد (سکوت کامل).
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardMarkup,
    Message,
    TelegramObject,
)

from config import settings
from handlers.common import (
    CommandOrText,
    deep_link_button,
    esc,
    hydrate,
    is_admin_or_owner,
)
from services import avatar

logger = logging.getLogger(__name__)

_ONBOARDING_COMMANDS = {
    "start",
    "create",
    "new_char",
    "شروع",
    "استارت",
    "ساخت",
    "شخصیت جدید",
    "تغییر چهره",
}

def _is_onboarding_message(text: str | None) -> bool:
    if not text:
        return False
    parts = text.strip().split()
    if not parts:
        return False
    first = parts[0].lower().lstrip("/")
    first = first.split("@")[0]
    return first in _ONBOARDING_COMMANDS


def _is_intended_as_command(
    text: str | None, chat_type: str, bot_username: str | None = None
) -> bool:
    """تشخیص اینکه آیا پیام کاربر یک دستور بازی است یا خیر."""
    if not text:
        return False
    # در پیوی ربات، هر پیامی معطوف به ربات است
    if chat_type == "private":
        return True

    clean = text.strip()
    if clean.startswith("/"):
        return True

    if bot_username and f"@{bot_username.lower()}" in clean.lower():
        return True

    # قاعده مشترک سراسری: پیام فقط وقتی «فرمان» است که خودِ فرمان (یا فرمان +
    # آرگومان مستندش) باشد؛ جملات گفتگویی مثل «کار من خیلی سخته» فرمان نیستند.
    return CommandOrText.is_game_command(clean)


class OnboardingGateMiddleware(BaseMiddleware):
    """رهگیری پیام‌ها و کلیک‌ها تا پیش از تکمیل ساخت کاراکتر و دریافت شناسنامه شهروندی."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        # ۱. بررسی و رهگیری پیام‌های متنی
        if isinstance(event, Message):
            user = event.from_user
            if not user or user.is_bot:
                return await handler(event, data)

            if is_admin_or_owner(user.id):
                return await handler(event, data)

            player = await hydrate(user.id, user.full_name, user.username)
            if player.onboarding_completed != 1:
                # اگر کاربر در حال ساخت کاراکتر است یا دستور /start فرستاده، مجاز است
                state = data.get("state")
                cur_state = await state.get_state() if state else None
                if cur_state and str(cur_state).startswith("OnboardingState"):
                    return await handler(event, data)

                if _is_onboarding_message(event.text):
                    return await handler(event, data)

                # اگر پیام یک دستور یا تعامل با بازی باشد، راهنمایی برای ساخت پروفایل ارسال می‌شود
                bot = data.get("bot") or getattr(event, "bot", None)
                bot_username = None
                if bot:
                    with contextlib.suppress(Exception):
                        me = await bot.get_me()
                        bot_username = me.username
                if not bot_username:
                    bot_username = (
                        settings.bot_username or avatar.RUNTIME_BOT_USERNAME or "IgrisLifeBot"
                    )

                chat_type = getattr(event.chat, "type", "private") if event.chat else "private"
                if not _is_intended_as_command(event.text, chat_type, bot_username):
                    # پیام‌های گفتگوی عادی در گروه‌ها بدون پاسخ رد می‌شوند تا اسپم ایجاد نشود
                    logger.debug(
                        "Gatekeeper silently dropped non-command message from user %s",
                        user.id,
                    )
                    return None

                if chat_type == "private":
                    prompt_text = (
                        f"👋 سلام <b>{esc(user.full_name)}</b> عزیز!\n\n"
                        "⚠️ شما هنوز در شهر تیرامیکس شناسنامه و کاراکتر نساخته‌اید.\n"
                        "برای ورود به بازی، کسب رکسار و شروع ماجراجویی، "
                        "روی دکمه زیر بزنید یا دستور <code>/start</code> را ارسال کنید:"
                    )
                    markup = InlineKeyboardMarkup(
                        inline_keyboard=[
                            [
                                deep_link_button(
                                    "🚀 ساخت کاراکتر و دریافت شناسنامه",
                                    "new",
                                    username=bot_username,
                                )
                            ]
                        ]
                    )
                else:
                    prompt_text = (
                        f"👤 <b>{esc(user.full_name)}</b> عزیز، شما هنوز پروفایل بازی ندارید!\n\n"
                        "📜 برای اجرای دستورات، دریافت شناسنامه شهروندی و کسب رکسار، "
                        "روی دکمه زیر بزنید و در پیوی ربات کاراکتر خود را بسازید:"
                    )
                    markup = InlineKeyboardMarkup(
                        inline_keyboard=[
                            [
                                deep_link_button(
                                    "🚀 ساخت پروفایل و ورود به بازی (در پیوی)",
                                    "new",
                                    username=bot_username,
                                )
                            ]
                        ]
                    )

                with contextlib.suppress(Exception):
                    await event.reply(prompt_text, reply_markup=markup)

                return None

        # ۲. رهگیری کلیک دکمه‌های شیشه‌ای
        elif isinstance(event, CallbackQuery):
            user = event.from_user
            if not user or user.is_bot:
                return await handler(event, data)

            if is_admin_or_owner(user.id):
                return await handler(event, data)

            player = await hydrate(user.id, user.full_name, user.username)
            if player.onboarding_completed != 1:
                # فقط دکمه‌های مربوط به ثبت‌نام مجاز هستند
                call_data = getattr(event, "data", "") or ""
                is_ob = (
                    call_data.startswith("ob:")
                    or call_data.startswith("wear_")
                    or call_data.startswith("w_")
                )
                if is_ob:
                    return await handler(event, data)

                alert_text = (
                    "⚠️ شما هنوز در بازی ثبت‌نام نکرده‌اید! ابتدا در پیوی ربات کاراکتر خود را بسازید."
                )
                with contextlib.suppress(Exception):
                    await event.answer(alert_text, show_alert=True)
                return None

        return await handler(event, data)
