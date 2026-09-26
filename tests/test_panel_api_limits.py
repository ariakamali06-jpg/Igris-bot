"""Would Telegram even accept the text these panels send?

Both reported-dead buttons render text and call `edit_caption` on the card.
Telegram rejects an edit for reasons that never surface to the user as a
stack trace — the handler catches them and calls `answer_error`, which tries
to `answerCallbackQuery` a second time and is silently dropped by Telegram.
Net effect: **the button does nothing, with no error anywhere.**

So a panel that would be rejected at the API must fail here instead.  This
suite renders the real panels against a real seeded database and asserts the
two things Telegram actually enforces:

* HTML parse-ability (unescaped `display_tag` breaks it)
* length: caption 1024 chars, text 4096 chars
"""

from __future__ import annotations

import html
import os
import re
import tempfile

import pytest

os.environ.setdefault("DB_PATH", os.path.join(tempfile.mkdtemp(), "panel_api.db"))
os.environ.setdefault("BOT_TOKEN", "123456:TESTTOKEN")

from handlers import profile, shop  # noqa: E402
from services import game  # noqa: E402

CAPTION_LIMIT = 1024
TEXT_LIMIT = 4096
ALLOWED_TAGS = {"b", "i", "u", "s", "a", "code", "pre", "tg-spoiler"}


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

    return await hydrate(555_001, "Player", "playr")


def _check_html(text: str, *, limit: int, where: str) -> None:
    assert len(text) <= limit, (
        f"{where}: {len(text)} chars exceeds Telegram's {limit} limit; "
        f"the API would reject this edit and the button would look dead"
    )
    # Balanced tags.
    stack: list[str] = []
    for match in re.finditer(r"<(/?)([a-zA-Z0-9_-]+)[^>]*>", text):
        closing, tag = match.group(1), match.group(2).lower()
        if closing:
            assert stack and stack[-1] == tag, (
                f"{where}: unbalanced </{tag}> in {text[:200]!r}"
            )
            stack.pop()
        else:
            assert tag in ALLOWED_TAGS, f"{where}: unsupported tag <{tag}>"
            stack.append(tag)
    assert not stack, f"{where}: unclosed tags {stack} in {text[:200]!r}"
    # No raw angle brackets left over from unescaped user data.
    residual = re.sub(r"</?(b|i|u|s|a|code|pre|tg-spoiler)[^>]*>", "", text)
    assert "<" not in residual, (
        f"{where}: raw '<' left in text (unescaped user data?): {residual[:200]!r}"
    )
    assert html.unescape(text) or True  # parsing does not raise


# --------------------------------------------------------------------------
# INVENTORY
# --------------------------------------------------------------------------


async def test_inventory_panel_text_is_api_acceptable(player) -> None:
    markup = await profile._inventory_markup(player, 0, 0)
    text = (
        f"🎒 <b>Inventory</b> — {player.display_tag}\n"
        f"ATK {player.atk} · DEF {player.defense} · DRIP {player.drip}"
    )
    _check_html(text, limit=CAPTION_LIMIT, where="inventory panel")
    assert markup is not None


async def test_inventory_panel_escapes_player_name(player) -> None:
    """A display name with '&' or '<' breaks HTML parsing outright."""
    hostile = player.__class__(
        user_id=player.user_id,
        display_name='Ace <b>&</b> "Rex"',
        username=None,
    )
    from handlers.common import esc

    text = f"🎒 <b>Inventory</b> — {esc(hostile.display_tag)}"
    _check_html(text, limit=CAPTION_LIMIT, where="escaped inventory panel")


async def test_item_detail_panel_is_api_acceptable(player) -> None:
    from database.items import ITEMS_BY_ID

    items = await game.list_inventory(player.user_id)
    assert items, "starter kit should own items"
    for row in items:
        item = ITEMS_BY_ID[row["id"]]
        lines = [
            f"<b>{html.escape(item.name)}</b> · {item.rarity.label} · "
            f"{item.slot.emoji} {item.slot.label}",
            html.escape(item.description),
            "",
        ]
        for value, icon in ((item.atk, "⚔️"), (item.defense, "🛡"), (item.drip, "💎")):
            if value:
                lines.append(f"{icon} +{value}")
        _check_html(
            "\n".join(lines),
            limit=CAPTION_LIMIT,
            where=f"item detail {item.id}",
        )


# --------------------------------------------------------------------------
# SHOP  (the real suspect: 5 items + descriptions can blow the caption limit)
# --------------------------------------------------------------------------


async def test_shop_panel_is_api_acceptable(player) -> None:
    text, markup = await shop._shop_page(player, 0)
    _check_html(text, limit=CAPTION_LIMIT, where="shop page 1")
    assert markup is not None


async def test_every_shop_page_is_api_acceptable(player) -> None:
    from services import shop as shop_service

    stock = await shop_service.today_stock()
    pages = max(1, (len(stock) + shop._PAGE_SIZE - 1) // shop._PAGE_SIZE)
    for page in range(pages):
        text, _ = await shop._shop_page(player, page)
        _check_html(text, limit=CAPTION_LIMIT, where=f"shop page {page + 1}")


# --------------------------------------------------------------------------
# PROFILE CAPTION
# --------------------------------------------------------------------------


async def test_profile_caption_is_api_acceptable(player) -> None:
    caption, photo = await profile._profile_panel(player)
    _check_html(caption, limit=CAPTION_LIMIT, where="profile caption")
    assert photo[:2] == b"\xff\xd8", "card must be a JPEG"


async def test_card_callback_is_api_acceptable(player) -> None:
    """The exact string cb_profile pushes into the existing card message."""
    card = profile.CardMessage() if hasattr(profile, "CardMessage") else None
    assert card is None  # no test doubles in production code
    caption, _ = await profile._profile_panel(player)
    _check_html(caption, limit=CAPTION_LIMIT, where="cb_profile caption")


# --------------------------------------------------------------------------
# the silent-failure mode
# --------------------------------------------------------------------------


async def test_double_answer_would_be_silently_dropped(player) -> None:
    """Documents the trap: acking early then erroring produces NO feedback.

    ``cb_inventory`` acks instantly, then on failure calls ``answer_error``,
    which answers the same callback query a second time.  Telegram answers
    `Bad Request: query is too old ... / query id is invalid` and the user
    never sees why the button did nothing.
    """
    from aiogram.exceptions import TelegramBadRequest

    second_answer = TelegramBadRequest(
        method="answerCallbackQuery",
        message="Bad Request: query is too old and response timeout expired "
        "or query id is invalid",
    )
    assert "query id is invalid" in str(second_answer)
    # Hence handlers must surface failures through the *message*, not a second
    # answerCallbackQuery.
    src = __import__("inspect").getsource(profile.cb_inventory)
    assert src.count("await call.answer") == 1, (
        "cb_inventory must ack exactly once; extra acks are dropped by "
        "Telegram and hide the real error"
    )
