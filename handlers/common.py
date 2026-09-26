"""Shared handler plumbing: player hydration, card rendering, error surface.

Every handler funnels domain failures (:class:`GameError` and subclasses)
through :func:`answer_error`, so players get a readable line instead of a
traceback, and Telegram never sees an unhandled exception for expected
conditions (broke, on cooldown, out of energy).
"""

from __future__ import annotations

import asyncio
import html

from aiogram.types import CallbackQuery, Message

from models import Player
from services import economy
from services.compositor import render_card
from services.game import GameError, ensure_player, render_request

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
]


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
    """Ack a callback instantly and surface the domain error text."""
    text = _error_text(exc)
    if callback is not None:
        # Always ack first: Telegram clients freeze buttons otherwise.
        await callback.answer(text, show_alert=True)
    elif message is not None:
        await message.reply(text)


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
