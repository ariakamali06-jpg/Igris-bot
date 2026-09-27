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
    """True when Telegram's refusal is an expected race (e.g. content unmodified)."""
    message = str(exc).lower()
    return any(token in message for token in _TRANSIENT)


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
    """Post the panel as a new message when the in-place edit is impossible."""
    try:
        if photo is not None:
            await message.answer_photo(
                photo=photo_bytes(photo),
                caption=text,
                reply_markup=reply_markup,
            )
            logger.info("panel: in-place photo edit failed, sent new photo message")
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
                    reply_markup: InlineKeyboardMarkup | None,
                    force_media: bool = False) -> bool:
    """Attempt the type-correct in-place edit. Returns True on success."""
    is_photo_msg = bool(message.photo or message.caption is not None)

    if photo is not None:
        # If it's already a photo message and not forced, retitle via caption first
        if message.photo and not force_media:
            try:
                await message.edit_caption(caption=text, reply_markup=reply_markup)
                return True
            except Exception:
                pass
        # Otherwise swap to photo media
        media = InputMediaPhoto(
            media=photo_bytes(photo),
            caption=text,
        )
        try:
            await message.edit_media(media=media, reply_markup=reply_markup)
            return True
        except TelegramBadRequest as exc:
            err = str(exc).lower()
            if is_transient(exc):
                return True
            logger.warning("panel: edit_media rejected: %s", exc)
            return False
        except Exception as exc:
            logger.warning("panel: edit_media error: %s", exc)
            return False

    # Text / Caption panel (photo is None)
    # If it is a photo/caption message, edit_caption; else edit_text.
    # If Telegram rejects with "no text" or "no caption", swap and retry immediately!
    first_fn = message.edit_caption if is_photo_msg else message.edit_text
    second_fn = message.edit_text if is_photo_msg else message.edit_caption
    first_kw = {"caption": text, "reply_markup": reply_markup} if is_photo_msg else {"text": text or "", "reply_markup": reply_markup}
    second_kw = {"text": text or "", "reply_markup": reply_markup} if is_photo_msg else {"caption": text, "reply_markup": reply_markup}

    try:
        await first_fn(**first_kw)
        return True
    except TelegramBadRequest as exc:
        err = str(exc).lower()
        if "message is not modified" in err:
            try:
                await message.edit_reply_markup(reply_markup=reply_markup)
            except Exception:
                pass
            return True
        if "there is no text" in err or "there is no caption" in err:
            try:
                await second_fn(**second_kw)
                return True
            except Exception as second_exc:
                logger.warning("panel: fallback edit attempt failed: %s", second_exc)
                return False
        if is_transient(exc):
            return True
        logger.warning("panel: edit rejected by Telegram: %s", exc)
        return False
    except Exception as exc:
        logger.warning("panel: unexpected edit exception: %s", exc)
        return False


async def render_panel(
    message: Message | None,
    *,
    text: str | None = None,
    photo: bytes | None = None,
    reply_markup: InlineKeyboardMarkup | None = None,
    force_media: bool = False,
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
        if not force_media and _same_content(message, text=text, photo=photo):
            # Buttons may still differ; refresh just the markup.
            await refresh_markup(message, reply_markup)
            return
    except Exception:  # noqa: BLE001
        logger.debug("panel: content compare failed", exc_info=True)

    message_type = "photo" if message.photo else "text"
    target = "caption" if message.photo else "text"
    try:
        ok = await _try_edit(message, text, photo, reply_markup, force_media=force_media)
        if ok:
            logger.debug(
                "panel: rendered %s panel on a %s message via edit_%s",
                "photo" if photo is not None else target,
                message_type,
                target if photo is None else (
                    "caption" if (message.photo and not force_media) else "media"
                ),
            )
            return
    except Exception as exc:  # noqa: BLE001
        # This is the line that was previously invisible: Telegram refusing an
        # edit looks exactly like a dead button to the user, so log it loudly.
        logger.error(
            "panel: Telegram rejected the edit (%s message, %d chars): %s: %s",
            message_type, len(text or ""), type(exc).__name__, exc,
            exc_info=True,
        )
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
