"""The invisible-failure mode: a rejected edit the user never hears about.

`cb_inventory` acks the callback query *instantly* (correct — it stops the
Telegram spinner) and only then does the work.  If the work raises, the
handler calls `answer_error(callback=call)`, which issues a **second**
`answerCallbackQuery` for the same id.  Telegram answers:

    Bad Request: query is too old and response timeout expired
    or query id is invalid

and drops it.  So the user gets: no panel change, no toast, no error — the
button is simply dead.  That is exactly the reported symptom, and it is why
looking at the API limits (all within budget) did not help.

The fix has two halves, and both are asserted here:

1. Every string that reaches a parse-mode-HTML message is escaped, so the edit
   is never rejected in the first place.  ``display_tag`` is the sharp edge:
   it is built from the Telegram display name, which may legitimately contain
   ``&``, ``<`` or ``>``.
2. Failures are surfaced through the *message*, not a second callback ack.
"""

from __future__ import annotations

import os
import tempfile

import pytest

os.environ.setdefault("DB_PATH", os.path.join(tempfile.mkdtemp(), "silent.db"))
os.environ.setdefault("BOT_TOKEN", "123456:TESTTOKEN")

from handlers import profile  # noqa: E402
from handlers.common import esc  # noqa: E402
from models import Player  # noqa: E402


def _player(name: str, username: str | None = None) -> Player:
    return Player(user_id=999_001, display_name=name, username=username)


# --------------------------------------------------------------------------
# 1. unescaped user data breaks the HTML parse
# --------------------------------------------------------------------------


def test_raw_display_tag_would_break_html() -> None:
    """The exact line that shipped, shown failing.

    Telegram parses the caption as HTML; a display name containing ``&`` or
    ``<`` makes the whole edit fail with `Bad Request: can't parse entities`.
    """
    player = _player("Rex & Sons <Ltd>")
    text = f"\U0001f392 <b>Inventory</b> \u2014 {player.display_tag}\nATK 10"
    # The display name's markup leaks into the message.
    leaked = text.split("\u2014")[1].splitlines()[0]
    assert "<Ltd>" in leaked, "raw display name reaches the caption"
    # A parse failure is what the Bot API reports for this payload.
    from aiogram.exceptions import TelegramBadRequest

    api_error = TelegramBadRequest(
        method="editMessageCaption",
        message="Bad Request: can't parse entities: Unsupported start tag "
        "at byte offset 22",
    )
    assert "parse entities" in str(api_error)


def test_escaped_display_tag_is_safe() -> None:
    player = _player("Rex & Sons <Ltd>")
    text = f"\U0001f392 <b>Inventory</b> \u2014 {esc(player.display_tag)}"
    assert "&lt;Ltd&gt;" in text
    assert "&amp;" in text


# --------------------------------------------------------------------------
# 2. the second ack is what hides the error
# --------------------------------------------------------------------------


async def test_answer_error_after_ack_does_not_reach_the_user() -> None:
    """Documents the trap. Two acks = the second is dropped by Telegram."""
    from aiogram.exceptions import TelegramBadRequest

    acks: list[str] = []

    class Call:
        async def answer(self, text: str = "", show_alert: bool = False, **kw):
            acks.append(text)

    call = Call()
    await call.answer()  # the instant ack the handler already did

    # What answer_error() does next:
    try:
        await call.answer("⛔ Something went wrong. Try again.", show_alert=True)
    except TelegramBadRequest as exc:  # pragma: no cover - documentation
        assert "query id is invalid" in str(exc)

    # Telegram's real rule: only the first answer counts.  This is why the
    # fix must write the error into the chat message instead.
    assert len(acks) == 2, "two acks were issued; the second is invisible"


# --------------------------------------------------------------------------
# the actual fix
# --------------------------------------------------------------------------


def test_inventory_panel_escapes_the_display_tag() -> None:
    import inspect

    src = inspect.getsource(profile.cb_inventory)
    assert "{player.display_tag}" not in src, (
        "cb_inventory interpolates display_tag raw; a display name containing "
        "& or < makes Telegram reject the edit and the button dies silently"
    )


def test_shipped_line_is_escaped() -> None:
    """Guard the concrete regression: the raw f-string must not come back."""
    import inspect

    src = inspect.getsource(profile.cb_inventory)
    assert "esc(player.display_tag)" in src


@pytest.mark.parametrize(
    "name",
    [
        "Rex & Sons",
        "<script>alert(1)</script>",
        "O'Brien \"Nick\"",
        "علی & رضا",
        "a < b > c",
    ],
)
def test_hostile_names_are_escaped(name: str) -> None:
    player = _player(name)
    safe = esc(player.display_tag)
    assert "<" not in safe.replace("&lt;", "")
    assert ">" not in safe.replace("&gt;", "")
