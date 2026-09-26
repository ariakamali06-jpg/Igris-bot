"""Panel-swap contract: a callback must never edit the WRONG message field.

Root cause this suite locks down: the character card is posted with
``reply_photo`` (a *photo* message), but the inventory/shop callbacks used
``edit_text``.  The Bot API rejects that with::

    Bad Request: there is no text in the message to edit

so the two buttons under the card failed.  ``raids.py`` had already grown a
private ``if message.photo: edit_caption else: edit_text`` fork; the fix
promotes that to one shared helper so every panel swap obeys the same rules:

1. content type must match the message type (photo↔caption, text↔text)
2. changing content type (text→photo) goes through ``edit_media`` first
3. "message is not modified" and stale-media errors are swallowed, never
   raised into the dispatcher's exception log
4. if a swap is impossible, a new message is sent instead of dying silently
"""

from __future__ import annotations

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import BufferedInputFile, InlineKeyboardButton, InlineKeyboardMarkup

from handlers.panel import refresh_markup, render_panel


def _markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="x", callback_data="x")]]
    )


class FakeMessage:
    """Minimal stand-in that enforces Telegram's real field rules."""

    def __init__(self, *, photo: bool = False) -> None:
        self.photo = [{"file_id": "abc"}] if photo else None
        self.caption = "old caption" if photo else None
        self.text = None if photo else "old text"
        self.calls: list[tuple[str, dict]] = []
        self.sent: list[dict] = []
        self.deleted = False
        # handlers may await message.answer(...)
        self.chat = type("Chat", (), {"id": 1})()

    def _record(self, name: str, **kwargs) -> None:
        self.calls.append((name, kwargs))

    async def edit_text(self, text: str, reply_markup=None, **kw):
        if self.photo:
            raise TelegramBadRequest(
                method=TelegramBadRequest.__name__,
                message="Bad Request: there is no text in the message to edit",
            )
        self._record("edit_text", text=text, reply_markup=reply_markup)
        self.text = text

    async def edit_caption(self, caption: str, reply_markup=None, **kw):
        if not self.photo:
            raise TelegramBadRequest(
                method=TelegramBadRequest.__name__,
                message="Bad Request: there is no caption in the message to edit",
            )
        self._record("edit_caption", caption=caption, reply_markup=reply_markup)
        self.caption = caption

    async def edit_media(self, media, reply_markup=None, **kw):
        if kw.pop("_strict", False):
            raise TelegramBadRequest(
                method=TelegramBadRequest.__name__,
                message="Bad Request: failed to get HTTP URL content",
            )
        self._record("edit_media", media=media, reply_markup=reply_markup)
        self.photo = [{"file_id": "new"}]
        self.caption = getattr(media, "caption", None)
        self.text = None

    async def edit_reply_markup(self, reply_markup=None, **kw):
        self._record("edit_reply_markup", reply_markup=reply_markup)

    async def answer(self, text: str, **kw):
        self.sent.append({"text": text, **kw})

    async def answer_photo(self, photo=None, caption=None, reply_markup=None, **kw):
        self.sent.append({"photo": photo, "caption": caption,
                          "reply_markup": reply_markup})
        return self

    async def delete(self):
        self.deleted = True


PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


def _photo_bytes() -> bytes:
    return PNG


# --------------------------------------------------------------------------
# text panels
# --------------------------------------------------------------------------


async def test_text_panel_on_text_message_uses_edit_text() -> None:
    msg = FakeMessage(photo=False)
    await render_panel(msg, text="hello", reply_markup=_markup())
    assert [c[0] for c in msg.calls] == ["edit_text"]
    assert msg.text == "hello"


async def test_text_panel_on_photo_message_uses_caption_not_text() -> None:
    """The bug: inventory opened from the photo card must not call edit_text."""
    msg = FakeMessage(photo=True)
    await render_panel(msg, text="inventory", reply_markup=_markup())
    assert [c[0] for c in msg.calls] == ["edit_caption"]
    assert msg.caption == "inventory"


# --------------------------------------------------------------------------
# photo panels
# --------------------------------------------------------------------------


async def test_photo_panel_on_photo_message_reuses_caption_only() -> None:
    """Same media type -> just retitle; no re-upload needed."""
    msg = FakeMessage(photo=True)
    await render_panel(
        msg, photo=_photo_bytes(), text="card", reply_markup=_markup()
    )
    assert [c[0] for c in msg.calls] == ["edit_caption"]
    assert msg.caption == "card"


async def test_photo_panel_on_text_message_switches_media() -> None:
    """Back-to-card from a text panel must become a photo, not a caption edit."""
    msg = FakeMessage(photo=False)
    await render_panel(
        msg, photo=_photo_bytes(), text="card", reply_markup=_markup()
    )
    assert [c[0] for c in msg.calls] == ["edit_media"]
    assert msg.photo is not None


async def test_photo_panel_falls_back_to_new_message_when_media_fails() -> None:
    """Dead media file -> send a fresh message instead of raising."""
    msg = FakeMessage(photo=False)

    async def boom(*a, **k):
        raise TelegramBadRequest(
            method="editMessageMedia",
            message="Bad Request: failed to get HTTP URL content",
        )

    msg.edit_media = boom  # type: ignore[method-assign]
    await render_panel(
        msg, photo=_photo_bytes(), text="card", reply_markup=_markup()
    )
    assert msg.sent, "must send a new message when the media swap is impossible"
    assert msg.sent[0]["caption"] == "card"


# --------------------------------------------------------------------------
# resilience
# --------------------------------------------------------------------------


async def test_not_modified_is_swallowed() -> None:
    """Re-rendering identical content is a no-op, not an error."""

    class NotModified(FakeMessage):
        async def edit_text(self, text: str, reply_markup=None, **kw):
            raise TelegramBadRequest(
                method="editMessageText",
                message="Bad Request: message is not modified",
            )

    msg = NotModified(photo=False)
    await render_panel(msg, text="same", reply_markup=_markup())
    assert msg.sent == []


async def test_unexpected_error_still_sends_fallback_message() -> None:
    class Boom(FakeMessage):
        async def edit_text(self, text: str, reply_markup=None, **kw):
            raise RuntimeError("database on fire")

    msg = Boom(photo=False)
    await render_panel(msg, text="resilient", reply_markup=_markup())
    assert msg.sent and msg.sent[0]["text"] == "resilient"


async def test_none_message_is_a_safe_noop() -> None:
    await render_panel(None, text="x", reply_markup=None)


# --------------------------------------------------------------------------
# markup-only refresh
# --------------------------------------------------------------------------


async def test_markup_only_refresh_uses_edit_reply_markup() -> None:
    """Equip/unequip only swap buttons; content is untouched."""
    msg = FakeMessage(photo=False)
    await refresh_markup(msg, _markup())
    assert [c[0] for c in msg.calls] == ["edit_reply_markup"]


async def test_markup_only_refresh_on_photo_message() -> None:
    msg = FakeMessage(photo=True)
    await refresh_markup(msg, _markup())
    assert [c[0] for c in msg.calls] == ["edit_reply_markup"]


def test_buffered_input_file_filename_is_stable() -> None:
    f = BufferedInputFile(PNG, filename="character.jpg")
    assert f.filename == "character.jpg"
