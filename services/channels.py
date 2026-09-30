"""Sponsor and required channel membership verification service.

Validates that users have joined mandatory community channels before allowing
them to create their initial citizenship character.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from config import settings

if TYPE_CHECKING:
    from aiogram import Bot

logger = logging.getLogger(__name__)


async def get_missing_channels(bot: Bot | None, user_id: int) -> list[dict[str, str]]:
    """Return a list of required channels the user has not yet joined.

    Bypasses and returns an empty list if:
    - force_channel_join setting is False
    - bot is None (e.g. running in offline test or smoke test)
    - settings.dry_run is True
    """
    if not settings.force_channel_join or bot is None or settings.dry_run:
        return []

    missing: list[dict[str, str]] = []
    for ch in settings.required_channels:
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
            elif "chat not found" in err_msg or "admin" in err_msg or "rights" in err_msg or "forbidden" in err_msg:
                # The bot lacks permissions in this channel or channel does not exist yet.
                # Log warning so admin can fix, but do not hard-block users due to bot misconfig.
                logger.warning(
                    "Bot lacks permissions or cannot find channel %s (%s). Bypassing channel check.",
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
