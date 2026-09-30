"""Gatekeeper middleware: forces character creation and channel join before allowing group interactions."""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    TelegramObject,
)

from handlers.common import hydrate, is_admin_or_owner

logger = logging.getLogger(__name__)


class OnboardingGateMiddleware(BaseMiddleware):
    """Intercepts group commands when a player has not yet completed character creation."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        # 1. Handle Messages
        if isinstance(event, Message):
            chat = event.chat
            user = event.from_user
            if not user or user.is_bot or not chat:
                return await handler(event, data)

            # In group and supergroup chats:
            if chat.type in ("group", "supergroup"):
                if is_admin_or_owner(user.id):
                    return await handler(event, data)
                player = await hydrate(user.id, user.full_name, user.username)
                if player.onboarding_completed != 1:
                    # Resolve bot username for deep link
                    bot = data.get("bot") or getattr(event, "bot", None)
                    bot_username = ""
                    if bot:
                        try:
                            me = await bot.get_me()
                            bot_username = me.username or ""
                        except Exception:
                            pass

                    start_url = (
                        f"https://t.me/{bot_username}?start=create"
                        if bot_username
                        else "https://t.me/"
                    )
                    buttons = [
                        [
                            InlineKeyboardButton(
                                text="👤 ساخت کاراکتر در پیوی ربات",
                                url=start_url,
                            )
                        ]
                    ]
                    prompt_text = (
                        f"👋 سلام مسافر جدید {user.mention_html()}!\n\n"
                        "🍁 شما هنوز در شهر تیرامیکس شناسنامه و کاراکتر نساخته‌اید!\n"
                        "برای شروع بازی، عضویت در کانال‌های رسمی و دریافت شناسنامه شهروندی، روی دکمه زیر کلیک کنید و در پیوی ربات کاراکتر خود را بسازید:"
                    )
                    try:
                        await event.reply(
                            prompt_text,
                            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
                        )
                    except Exception as e:
                        logger.warning("Could not reply with onboarding gate prompt: %s", e)
                    return None

        # 2. Handle CallbackQueries
        elif isinstance(event, CallbackQuery):
            chat = event.message.chat if event.message else None
            user = event.from_user
            if user and not user.is_bot and chat and chat.type in ("group", "supergroup"):
                player = await hydrate(user.id, user.full_name, user.username)
                if player.onboarding_completed != 1:
                    try:
                        await event.answer(
                            "⚠️ شما هنوز کاراکتر نساخته‌اید! ابتدا وارد پیوی ربات شوید و کاراکتر خود را بسازید.",
                            show_alert=True,
                        )
                    except Exception:
                        pass
                    return None

        return await handler(event, data)
