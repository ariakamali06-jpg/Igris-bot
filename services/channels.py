"""Sponsor and required channel membership verification service.

Validates that users have joined mandatory community channels before allowing
them to create their initial citizenship character.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from config import settings
from database.connection import db

if TYPE_CHECKING:
    from aiogram import Bot

logger = logging.getLogger(__name__)


async def get_active_channels() -> list[dict[str, str]]:
    """دریافت لیست کانال‌های فعال عضویت اجباری از دیتابیس (یا کانفیگ پیش‌فرض)."""
    try:
        async with db.read() as conn:
            cursor = await conn.execute(
                "SELECT name, username, url FROM mandatory_channels WHERE is_active = 1"
            )
            rows = await cursor.fetchall()
            await cursor.close()
            if rows:
                return [
                    {"name": r["name"], "username": r["username"], "url": r["url"]}
                    for r in rows
                ]
    except Exception as exc:
        logger.warning("Error fetching mandatory channels from database: %s", exc)

    # Fallback to settings.required_channels
    return list(settings.required_channels)


async def add_mandatory_channel(name: str, username: str, url: str = "") -> dict[str, Any]:
    """افزودن کانال جدید به لیست عضویت اجباری در دیتابیس."""
    clean_u = ("@" + username.lstrip("@")).strip()
    clean_url = url.strip() or f"https://t.me/{clean_u.lstrip('@')}"
    clean_name = name.strip() or clean_u
    now_ts = int(time.time())

    async with db.write() as conn:
        await conn.execute(
            """
            INSERT INTO mandatory_channels (name, username, url, is_active, created_at)
            VALUES (?, ?, ?, 1, ?)
            ON CONFLICT(username) DO UPDATE SET
                name = excluded.name,
                url = excluded.url,
                is_active = 1
            """,
            (clean_name, clean_u, clean_url, now_ts),
        )

    return {"success": True, "name": clean_name, "username": clean_u, "url": clean_url}


async def remove_mandatory_channel(username: str) -> bool:
    """حذف یا غیرفعال کردن یک کانال از عضویت اجباری."""
    clean_u = ("@" + username.lstrip("@")).strip()
    async with db.write() as conn:
        cursor = await conn.execute(
            "DELETE FROM mandatory_channels WHERE LOWER(username) = LOWER(?)",
            (clean_u,),
        )
        deleted = cursor.rowcount > 0
        await cursor.close()
    return deleted


async def get_missing_channels(bot: Bot | None, user_id: int) -> list[dict[str, str]]:
    """Return a list of required channels the user has not yet joined.

    Bypasses and returns an empty list if:
    - force_channel_join setting is False
    - bot is None (e.g. running in offline test or smoke test)
    - settings.dry_run is True
    """
    if not settings.force_channel_join or bot is None or settings.dry_run:
        return []

    channels = await get_active_channels()
    missing: list[dict[str, str]] = []

    for ch in channels:
        username = ch.get("username", "").strip()
        if not username:
            continue
        try:
            member = await bot.get_chat_member(chat_id=username, user_id=user_id)
            # Valid membership states: creator, administrator, member, restricted (not kicked/left)
            if member.status in ("creator", "administrator", "member", "restricted"):
                continue
            missing.append(ch)
        except Exception as exc:
            err_msg = str(exc).lower()
            if "user not found" in err_msg or "participant_id_invalid" in err_msg:
                # User has definitely not joined the channel
                missing.append(ch)
            elif (
                "chat not found" in err_msg
                or "admin" in err_msg
                or "rights" in err_msg
                or "forbidden" in err_msg
            ):
                # The bot lacks permissions in this channel or channel does not exist yet.
                logger.warning(
                    "Bot lacks permissions or cannot find channel %s (%s). Bypassing.",
                    username,
                    exc,
                )
            else:
                logger.warning(
                    "Unexpected error checking membership for user %d in %s: %s",
                    user_id,
                    username,
                    exc,
                )
                missing.append(ch)

    return missing


def channel_join_markup(channels: list[dict[str, str]]) -> InlineKeyboardMarkup:
    """Build an inline keyboard with channel links and a verification button."""
    buttons: list[list[InlineKeyboardButton]] = []
    for idx, ch in enumerate(channels, 1):
        name = ch.get("name", f"کانال {idx}")
        url = ch.get("url") or f"https://t.me/{ch.get('username', '').lstrip('@')}"
        buttons.append([InlineKeyboardButton(text=f"📢 {name}", url=url)])

    buttons.append(
        [
            InlineKeyboardButton(
                text="✅ عضو شدم / تایید عضویت",
                callback_data="ob:check_channels",
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=buttons)
