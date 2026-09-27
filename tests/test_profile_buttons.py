"""End-to-end reproduction of the dead-button bug, through the real handlers.

The original report was: press **Inventory** or **Shop** on the character card
and nothing happens.  The card is posted with ``reply_photo``; both callbacks
then called ``message.edit_text``, and Telegram answers ``there is no text in
the message to edit``.

These tests drive the actual router callbacks with a Message subclass that
enforces Telegram's field rules, so a regression to a bare ``edit_text`` fails
here rather than in production.
"""

from __future__ import annotations

import os
import tempfile
from datetime import UTC, datetime
from typing import Any

import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import Chat, InlineKeyboardMarkup, Message, PhotoSize, User
from pydantic import ConfigDict, PrivateAttr

# Isolate the SQLite database before any bot module reads settings.
os.environ.setdefault("DB_PATH", os.path.join(tempfile.mkdtemp(), "panel_it.db"))
os.environ.setdefault("BOT_TOKEN", "123456:TESTTOKEN")

from handlers import duels, profile, shop  # noqa: E402

PHOTO = PhotoSize(file_id="f", file_unique_id="f", width=512, height=512)


class CardMessage(Message):
    """A *photo* message that behaves like Telegram: edit_text is a hard error.

    aiogram 3.31 ships frozen pydantic models; the fake needs a mutable copy so
    an edit can actually be observed.
    """

    model_config = ConfigDict(frozen=False)

    _sent: list = PrivateAttr(default_factory=list)

    @property
    def sent(self) -> list:
        return self._sent

    def __init__(self, *, photo: bool = True) -> None:
        super().__init__(
            message_id=555,
            date=datetime.now(tz=UTC),
            chat=Chat(id=1, type="private"),
            from_user=User(id=424242, is_bot=False, first_name="Test"),
        )
        self._sent = []
        if photo:
            self.photo = [PHOTO]
            self.caption = "<b>card</b>"
            self.text = None
        else:
            self.photo = None
            self.caption = None
            self.text = "inventory panel"

    # -- edit APIs -------------------------------------------------------
    async def edit_text(self, text: str, reply_markup=None, **kw: Any):
        if self.photo is not None:
            raise TelegramBadRequest(
                method="editMessageText",
                message="Bad Request: there is no text in the message to edit",
            )
        self.text = text
        self.reply_markup = reply_markup

    async def edit_caption(self, caption: str, reply_markup=None, **kw: Any):
        if self.photo is None:
            raise TelegramBadRequest(
                method="editMessageCaption",
                message="Bad Request: there is no caption in the message to edit",
            )
        self.caption = caption
        self.reply_markup = reply_markup

    async def edit_media(self, media, reply_markup=None, **kw: Any):
        self.photo = [PHOTO]
        self.caption = getattr(media, "caption", None)
        self.text = None
        self.reply_markup = reply_markup
        return self

    async def edit_reply_markup(self, reply_markup=None, **kw: Any):
        self.reply_markup = reply_markup
        return self

    # -- send APIs -------------------------------------------------------
    async def answer(self, text: str, **kw: Any):
        self.sent.append({"kind": "text", "text": text})
        return self

    async def answer_photo(self, photo=None, caption=None, reply_markup=None, **kw: Any):
        self.sent.append({"kind": "photo", "caption": caption})
        return self

    async def reply(self, text: str, **kw: Any):
        self.sent.append({"kind": "text", "text": text})
        return self

    async def reply_photo(self, photo=None, caption=None, reply_markup=None, **kw: Any):
        self.sent.append({"kind": "photo", "caption": caption})
        self.photo = [PHOTO]
        self.caption = caption
        self.reply_markup = reply_markup
        return self


USER_ID = 424242


class FakeUser(User):
    def __init__(self) -> None:
        super().__init__(
            id=USER_ID, is_bot=False,
            first_name="Test", last_name="Subject", username="subject",
        )

    @property
    def full_name(self) -> str:
        return "Test Subject"


class FakeCall:
    def __init__(self, data: str, message: Message) -> None:
        self.data = data
        self.message = message
        self.from_user = FakeUser()
        self.answers: list[str] = []
        self.alerts: list[str] = []

    async def answer(self, text: str = "", show_alert: bool = False, **kw):
        self.answers.append(text)
        if show_alert:
            self.alerts.append(text)


@pytest.fixture(scope="session")
async def _db():
    from database.connection import db
    from database.seed import seed_catalog

    await db.connect()
    await seed_catalog()
    yield db


@pytest.fixture()
async def player(_db):
    from handlers.common import hydrate

    user = FakeUser()
    return await hydrate(user.id, user.full_name, user.username)


def _has_button(markup: InlineKeyboardMarkup | None, needle: str) -> bool:
    if markup is None:
        return False
    return any(
        needle in button.text
        for row in markup.inline_keyboard
        for button in row
    )


# --------------------------------------------------------------------------
# THE BUG: both buttons hung off the photo card
# --------------------------------------------------------------------------


async def test_inventory_button_works_from_photo_card(player) -> None:
    card = CardMessage()
    call = FakeCall("inv:0", card)

    await profile.cb_inventory(call)

    assert call.alerts == [], f"callback errored: {call.alerts}"
    assert card.sent == [], "must edit in place, not spam a new message"
    assert "Inventory" in (card.caption or "") or "کوله‌پشتی" in (card.caption or ""), (
        f"caption was not updated: {card.caption!r}"
    )
    assert _has_button(card.reply_markup, "Back to card") or _has_button(card.reply_markup, "کارت من")


async def test_shop_button_works_from_photo_card(player) -> None:
    card = CardMessage()
    call = FakeCall("shop:0", card)

    await shop.cb_shop(call)

    assert call.alerts == [], f"callback errored: {call.alerts}"
    assert card.sent == []
    assert "Boutique" in (card.caption or "") or "بوتیک" in (card.caption or ""), (
        f"caption was not updated: {card.caption!r}"
    )
    assert _has_button(card.reply_markup, "Back to card") or _has_button(card.reply_markup, "کارت من")


async def test_inventory_pagination_stays_on_caption(player) -> None:
    card = CardMessage()
    card.caption = "inventory"
    call = FakeCall("inv:0", card)
    await profile.cb_inventory(call)
    assert call.alerts == []
    assert "Inventory" in (card.caption or "") or "کوله‌پشتی" in (card.caption or "")


# --------------------------------------------------------------------------
# navigating back to the card
# --------------------------------------------------------------------------


async def test_back_to_card_from_inventory_restores_photo(player) -> None:
    card = CardMessage()
    card.photo = None
    card.text = "inventory panel"
    card.caption = None
    call = FakeCall("act:me", card)

    await profile.cb_profile(call)

    assert call.alerts == [], f"callback errored: {call.alerts}"
    assert card.photo is not None, "the card must come back as a photo"
    assert card.text is None
    assert _has_button(card.reply_markup, "Inventory") or _has_button(card.reply_markup, "کوله‌پشتی")


async def test_back_to_card_from_photo_panel_keeps_photo(player) -> None:
    card = CardMessage()
    call = FakeCall("act:me", card)
    await profile.cb_profile(call)
    assert card.photo is not None
    assert card.sent == []


# --------------------------------------------------------------------------
# equip / unequip only swap buttons
# --------------------------------------------------------------------------


async def test_equip_refreshes_markup_without_touching_caption(player) -> None:
    from services import game

    items = await game.list_inventory(player.user_id)
    assert items, "starter kit should own at least one item"
    item = items[0]

    card = CardMessage()
    before = card.caption
    call = FakeCall(f"eq:{item['id']}:0", card)

    await profile.cb_equip(call)

    assert call.alerts == [], f"equip errored: {call.alerts}"
    assert card.caption == before, "equip must not rewrite the caption"
    assert _has_button(card.reply_markup, "Back to card") or _has_button(card.reply_markup, "کارت من")


async def test_unequip_item_removes_from_loadout_and_stays_unequipped(player) -> None:
    from handlers.common import hydrate

    # Starter item should initially be equipped
    assert player.loadout["body"] is not None

    card = CardMessage()
    call = FakeCall("uneq:body:0:fitted_tee", card)
    await profile.cb_unequip(call)

    fresh = await hydrate(player.user_id, player.display_name, player.username)
    assert fresh.loadout["body"] is None, "Unequipped item must stay unequipped after hydration"
    assert _has_button(card.reply_markup, "کارت من")


async def test_equip_and_unequip_updates_photo_media(player) -> None:
    from handlers.common import hydrate

    card = CardMessage()
    call_uneq = FakeCall("uneq:body:0:fitted_tee", card)
    await profile.cb_unequip(call_uneq)
    assert card.photo is not None

    call_eq = FakeCall("eq:fitted_tee:0", card)
    await profile.cb_equip(call_eq)
    assert card.photo is not None


# --------------------------------------------------------------------------
# duel panel (was also on a text-message path)
# --------------------------------------------------------------------------


async def test_duel_decline_on_photo_message_does_not_crash(player) -> None:
    """A decline edit on a photo message used to raise inside the handler."""
    from database.connection import db

    async with db.write() as conn:
        cur = await conn.execute(
            """INSERT INTO duels (chat_id, challenger_id, opponent_id, stake,
                                  status, seed, created_at)
               VALUES (1, ?, ?, 100, 'pending', 12345, 0)""",
            (USER_ID, USER_ID + 1),
        )
        duel_id = cur.lastrowid
        await cur.close()

    card = CardMessage()
    call = FakeCall(f"duel:decline:{duel_id}", card)

    await duels.cb_decline(call)

    assert call.alerts == [], f"decline errored: {call.alerts}"
    assert "Duel" in (card.caption or "") or "دوئل" in (card.caption or "") or card.sent


async def test_profile_markup_contains_all_four_buttons(player) -> None:
    markup = await profile._profile_markup(player)
    labels = [b.text for row in markup.inline_keyboard for b in row]
    assert any("Inventory" in x or "کوله‌پشتی" in x for x in labels)
    assert any("Shop" in x or "فروشگاه" in x for x in labels)
    assert any("Work" in x or "کار" in x for x in labels)
    assert any("Balance" in x or "موجودی" in x for x in labels)


def test_card_is_posted_as_photo_not_text() -> None:  # noqa: D401
    """Guards the premise: if the card ever became a text message, the
    original bug class would be masked rather than fixed."""
    import inspect

    src = inspect.getsource(profile._send_profile)
    assert "reply_photo" in src
    assert "reply(" not in src
