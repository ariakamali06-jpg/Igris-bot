"""Shared handler plumbing: player hydration, card rendering, error surface.

Every handler funnels domain failures (:class:`GameError` and subclasses)
through :func:`answer_error`, so players get a readable line instead of a
traceback, and Telegram never sees an unhandled exception for expected
conditions (broke, on cooldown, out of energy).
"""

from __future__ import annotations

import asyncio
import html
import logging

from aiogram.filters import BaseFilter
from aiogram.types import CallbackQuery, Message

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
    "answer_error",
    "message_error",
    "energy_bar",
    "editable_message",
    "CommandOrText",
    "is_admin_or_owner",
]


def is_admin_or_owner(user_id: int) -> bool:
    """Check if the user is the project owner (Rex Lapis: 5765828495) or configured in admin_ids."""
    from config import settings
    return settings.is_admin(user_id) or user_id == 5765828495


class CommandOrText(BaseFilter):
    """Reliable filter matching both /commands and plain text / Persian words."""

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
        if self.prefix_words and text.lower().startswith(self.prefix_words):
            return True
        return False


def editable_message(call: CallbackQuery) -> Message | None:
    """The callback's message when it is a real, editable :class:`Message`.

    ``CallbackQuery.message`` is typed ``Message | InaccessibleMessage``;
    inaccessible messages have no text/caption to edit, so handlers must
    skip UI swaps for them instead of crashing.

    Content type is the caller's problem to solve through
    :mod:`handlers.panel` — a photo message is re-titled with ``edit_caption``,
    never ``edit_text``.
    """
    message = call.message
    return message if isinstance(message, Message) else None


def esc(text: str | None) -> str:
    """HTML-escape untrusted text (usernames, display names)."""
    return html.escape(text or "")


def fmt(value: int) -> str:
    """12.4k / 1.2M style grouping for chat-facing numbers."""
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if value >= 10_000:
        return f"{value / 1_000:.1f}k"
    return f"{value:,}"


async def hydrate(user_id: int, display_name: str, username: str | None) -> Player:
    """Load (or lazily create) the player behind a Telegram user."""
    return await ensure_player(user_id, display_name, username)


async def card_bytes(player: Player) -> bytes:
    """Render the character card off-loop (CPU-bound PIL work)."""
    request = render_request(player)
    return await asyncio.to_thread(render_card, request)


async def answer_error(
    exc: Exception,
    *,
    callback: CallbackQuery | None = None,
    message: Message | None = None,
) -> None:
    """Surface a domain error to the player.

    Telegram accepts **only the first** ``answerCallbackQuery`` for a given
    callback id; every later one is dropped with ``query id is invalid``.
    Handlers ack instantly to clear the button spinner, so an error raised
    after that ack cannot be reported through the callback — the user would
    see nothing at all and conclude the button is dead.

    Therefore, when the callback was already acked we write the error into the
    chat (and, if the message is an editable panel, into the panel itself), and
    always log the traceback so the failure is debuggable.
    """
    text = _error_text(exc)
    if message is not None:
        await message.reply(text)
    elif callback is not None:
        message = editable_message(callback)
        if message is not None:
            await message.reply(text)
        else:
            # Nothing editable — an alert is the only channel left, and it
            # only lands if this is the very first answer.
            try:
                await callback.answer(text, show_alert=True)
            except Exception:  # noqa: BLE001 - already acked; nothing to do
                logger.debug("callback alert dropped (already answered)", exc_info=True)
    logger.warning("surfaced domain error to chat: %s", exc, exc_info=exc)


def _error_text(exc: Exception) -> str:
    if isinstance(exc, economy.OnCooldown):
        minutes, seconds = divmod(int(exc.remaining), 60)
        clock = f"{minutes}m {seconds:02d}s" if minutes else f"{seconds}s"
        return f"⏳ {exc.action} cools down in {clock}."
    if isinstance(exc, GameError):
        return f"⛔ {exc}"
    return "⛔ Something went wrong. Try again."


async def message_error(exc: Exception, message: Message) -> None:
    await message.reply(_error_text(exc))


def energy_bar(energy: int, max_energy: int, width: int = 10) -> str:
    filled = round(width * energy / max(1, max_energy))
    return "▮" * filled + "▯" * (width - filled)
