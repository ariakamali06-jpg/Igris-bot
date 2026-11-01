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
    Message,
    TelegramObject,
)

from handlers.common import hydrate, is_admin_or_owner

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

                # در غیر این صورت کاربر ثبت‌نام نکرده و هیچ پاسخی ارسال نمی‌شود (سکوت مطلق)
                logger.debug(
                    "Gatekeeper silently dropped command from non-onboarded user %s", user.id
                )
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

                # دکمه‌های دیگر بدون هیچ پیام و الرتی بی‌اثر می‌شوند
                with contextlib.suppress(Exception):
                    await event.answer()
                return None

        return await handler(event, data)
