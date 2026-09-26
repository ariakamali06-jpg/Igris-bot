"""Panel swaps for inline-keyboard messages.

Telegram's Bot API stores one *content type* per message: a photo message can
only be re-titled with ``editMessageCaption``; a text message can only be
re-titled with ``editMessageText``.  Calling the wrong one is not a soft
failure — the API answers::

    Bad Request: there is no text in the message to edit

and the button appears dead to the user.

This module is the single place that knows the rule, so handlers never have to
branch on ``message.photo`` themselves.  It also absorbs the three failures
that would otherwise spam the dispatcher log:

* ``message is not modified`` — re-rendering identical content
* ``message to edit not found`` — the message was deleted mid-flight
* ``failed to get HTTP URL content`` — a cached card is no longer fetchable
  by Telegram, so the media swap is impossible

:func:`render_panel` guarantees the user always ends up looking at the panel
they asked for: if the in-place edit is impossible it sends a fresh message
instead of dying quietly.
"""

from __future__ import annotations

import logging
from typing import Any

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import (
    BufferedInputFile,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    Message,
)

logger = logging.getLogger(__name__)

__all__ = [
    "render_panel",
    "refresh_markup",
    "swap_to_card",
    "is_transient",
    "photo_bytes",
]

_CARD_FILENAME = "character.jpg"

# Substrings Telegram uses for failures that are expected in normal play.
_TRANSIENT = (
    "message is not modified",
    "message to edit not found",
    "message can't be deleted",
    "message identifier is not specified",
)


def photo_bytes(data: bytes, filename: str = _CARD_FILENAME) -> BufferedInputFile:
    """Wrap rendered card bytes for upload."""
    return BufferedInputFile(data, filename=filename)


def is_transient(exc: Exception) -> bool:
    """True when Telegram's refusal is an expected race, not a real bug."""
    message = str(exc).lower()
    if any(token in message for token in _TRANSIENT):
        return True
    return isinstance(exc, TelegramBadRequest) and "there is no text" in message


def _is_media_gone(exc: Exception) -> bool:
    message = str(exc).lower()
    return "http url content" in message or "wrong file identifier" in message


def _same_content(message: Message, *, text: str | None, photo: bytes | None) -> bool:
    """Cheap guard so we don't ask Telegram to edit a message into itself."""
    if photo is not None:
        return False
    if message.photo:
        return message.caption == text
    return message.text == text


async def _fallback_message(message: Message, *, text: str | None,
                            photo: bytes | None,
                            reply_markup: InlineKeyboardMarkup | None) -> None:
    """Post the panel as a new message when the in-place edit is impossible.

    Uses ``message.answer*`` rather than the chat shortcuts: those resolve
    ``bot`` from a context var that a background callback may not carry.
    """
    try:
        if photo is not None:
            await message.answer_photo(
                photo=photo_bytes(photo),
                caption=text,
                reply_markup=reply_markup,
            )
            logger.info("panel: media swap failed, sent new photo message")
        else:
            await message.answer(
                text or "",
                parse_mode="HTML",
                reply_markup=reply_markup,
            )
            logger.info("panel: in-place edit failed, sent new text message")
    except Exception:  # noqa: BLE001 - last resort, never raise into dispatcher
        logger.exception("panel: fallback message also failed")


async def _try_edit(message: Message, text: str | None, photo: bytes | None,
                    reply_markup: InlineKeyboardMarkup | None) -> bool:
    """Attempt the type-correct in-place edit. Returns True on success."""
    is_photo_message = bool(message.photo)

    if photo is None:
        # Text panel: a photo message can only be re-titled via its caption.
        try:
            if is_photo_message:
                await message.edit_caption(caption=text, reply_markup=reply_markup)
            else:
                await message.edit_text(text or "", reply_markup=reply_markup)
            return True
        except Exception as exc:  # noqa: BLE001
            if _is_media_gone(exc):
                return False
            if is_transient(exc):
                logger.debug("panel: transient edit refusal", exc_info=True)
                return True  # effectively fine — content already matches
            raise
    else:
        # Photo panel.
        if is_photo_message:
            # Same media type: only the caption (and buttons) change.
            try:
                await message.edit_caption(caption=text, reply_markup=reply_markup)
                return True
            except Exception as exc:  # noqa: BLE001
                if _is_media_gone(exc):
                    return False
                if is_transient(exc):
                    logger.debug("panel: transient edit refusal", exc_info=True)
                    return True
                raise
        else:
            # Type switch: text message must become a photo.
            media = InputMediaPhoto(
                media=photo_bytes(photo), caption=text
            )
            try:
                await message.edit_media(media=media, reply_markup=reply_markup)
                return True
            except Exception as exc:  # noqa: BLE001
                if _is_media_gone(exc):
                    return False
                if is_transient(exc):
                    return True
                raise


async def render_panel(
    message: Message | None,
    *,
    text: str | None = None,
    photo: bytes | None = None,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> None:
    """Render a panel into ``message``, matching content type to message type.

    Call this instead of ``message.edit_text`` / ``message.edit_caption`` for
    every callback that changes what a button press shows.  When the in-place
    edit is impossible, a new message carrying the same panel is sent.
    """
    if message is None:
        return
    if text is None and photo is None:
        return

    try:
        if _same_content(message, text=text, photo=photo):
            # Buttons may still differ; refresh just the markup.
            await refresh_markup(message, reply_markup)
            return
    except Exception:  # noqa: BLE001
        logger.debug("panel: content compare failed", exc_info=True)

    try:
        ok = await _try_edit(message, text, photo, reply_markup)
        if ok:
            return
    except Exception:  # noqa: BLE001
        logger.exception("panel: edit failed, falling back to a new message")
        ok = False

    await _fallback_message(
        message, text=text, photo=photo, reply_markup=reply_markup
    )


async def refresh_markup(
    message: Message | None, reply_markup: InlineKeyboardMarkup | None
) -> None:
    """Swap only the inline keyboard, leaving content and media untouched."""
    if message is None:
        return
    try:
        await message.edit_reply_markup(reply_markup=reply_markup)
    except Exception:  # noqa: BLE001
        logger.debug("panel: markup refresh failed", exc_info=True)


async def swap_to_card(
    message: Message | None,
    player: Any,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> None:
    """Convenience: put the freshly rendered character card back on screen."""
    from handlers.common import card_bytes

    try:
        photo = await card_bytes(player)
    except Exception:  # noqa: BLE001
        logger.exception("panel: card render failed")
        return
    await render_panel(message, photo=photo, text=None, reply_markup=reply_markup)
