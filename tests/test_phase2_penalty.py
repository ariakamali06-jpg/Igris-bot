"""دروازه — two-player penalty: hidden keeper save, shooter answers (temp DB).

Same double harness as the دوز tests: real handlers driven through
MutableMessage / FakeCall, exact balance assertions around the pot, and the
keeper's pick proven to stay hidden until the shooter has committed.
Player ids: 663_000+.
"""

from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path

import pytest_asyncio
from aiogram.types import Chat, Message, User
from pydantic import ConfigDict, PrivateAttr

# Point the DB at a scratch file before config/settings are first used.
os.environ.setdefault(
    "DB_PATH", str(Path(tempfile.mkdtemp(prefix="tgbot-pytest-")) / "test.db")
)

from config import settings  # noqa: E402
from database.connection import db  # noqa: E402
from database.seed import seed_catalog  # noqa: E402
from handlers import penalty  # noqa: E402
from models.enums import ActivityKind  # noqa: E402
from services import economy  # noqa: E402
from services.game import ensure_player  # noqa: E402

BASE_ID = 663_000
STAKE = 300
START_CREDITS = 5_000

LEFT, MIDDLE, RIGHT = 0, 1, 2


@pytest_asyncio.fixture(autouse=True)
async def _database() -> None:
    if db._closed:  # noqa: SLF001
        await db.connect()
        await seed_catalog()
    economy.clear_cooldowns()
    yield
    await db.close()


async def _funded(offset: int, amount: int = START_CREDITS):
    player = await ensure_player(BASE_ID + offset, f"Pen {offset}", None)
    credits, _ = await economy.balances(player.user_id)
    if credits < amount:
        await economy.grant(
            player.user_id, credits=amount - credits, kind=ActivityKind.SYSTEM
        )
    elif credits > amount:
        await economy.spend(
            player.user_id, credits=credits - amount, kind=ActivityKind.SYSTEM
        )
    return player


async def _balance(user_id: int) -> int:
    credits, _ = await economy.balances(user_id)
    return credits


async def _round(round_id: int) -> dict:
    row = await db.fetchone("SELECT * FROM penalty_rounds WHERE id = ?", (round_id,))
    assert row is not None
    return dict(row)


async def _round_id_for(shooter_id: int) -> int:
    row = await db.fetchone(
        "SELECT id FROM penalty_rounds WHERE shooter_id = ? ORDER BY id DESC LIMIT 1",
        (shooter_id,),
    )
    assert row is not None
    return int(row["id"])


class MutableMessage(Message):
    model_config = ConfigDict(frozen=False)
    _replies: list = PrivateAttr(default_factory=list)

    def __init__(self, text: str = "", user: User | None = None, reply_to_message=None) -> None:
        super().__init__(
            message_id=6631,
            date=time.time(),
            chat=Chat(id=-100660004, type="supergroup", title="تیرامیکس"),
            from_user=user,
            text=text,
            reply_to_message=reply_to_message,
        )
        self._replies = []

    @property
    def replies(self) -> list:
        return self._replies

    async def reply(self, text: str, reply_markup=None, **kw):  # noqa: ANN001, ARG002
        self._replies.append({"text": text, "reply_markup": reply_markup})
        return self

    async def answer(self, text: str, reply_markup=None, **kw):  # noqa: ANN001, ARG002
        self._replies.append({"text": text, "reply_markup": reply_markup})
        return self

    async def edit_text(self, text: str, reply_markup=None, **kw):  # noqa: ANN001, ARG002
        self._replies.append({"text": text, "reply_markup": reply_markup})
        return self


class FakeCall:
    def __init__(self, data: str, message: Message, user: User) -> None:
        self.data = data
        self.message = message
        self.from_user = user
        self.alerts: list[str] = []
        self.answers: list[str] = []

    async def answer(self, text: str = "", show_alert: bool = False, **kw) -> None:
        self.answers.append(text)
        if show_alert:
            self.alerts.append(text)


def _user(player) -> User:  # noqa: ANN001
    return User(
        id=player.user_id, is_bot=False, first_name=player.display_name, username=None
    )


async def _challenge(shooter, keeper_user: User, stake: int = STAKE):  # noqa: ANN001
    reply_to = MutableMessage(text="بیا", user=keeper_user)
    msg = MutableMessage(
        text=f"دروازه {stake}", user=_user(shooter), reply_to_message=reply_to
    )
    await penalty.cmd_penalty(msg)
    return msg


async def _start_round(shooter, keeper):  # noqa: ANN001
    """Challenge + keeper accept; returns (round_id, card)."""
    await _challenge(shooter, _user(keeper))
    round_id = await _round_id_for(shooter.user_id)
    card = MutableMessage(text="دروازه", user=_user(keeper))
    await penalty.cb_accept(FakeCall(f"pen:accept:{round_id}", card, _user(keeper)))
    return round_id, card


# ---------------------------------------------------------------------------
# Challenge
# ---------------------------------------------------------------------------


async def test_challenge_escrows_stake_and_offers_buttons() -> None:
    shooter = await _funded(1)
    keeper = await _funded(2)
    start = await _balance(shooter.user_id)

    msg = await _challenge(shooter, _user(keeper))
    assert await _balance(shooter.user_id) == start - STAKE

    round_row = await _round(await _round_id_for(shooter.user_id))
    assert round_row["status"] == "pending"
    assert round_row["stake"] == STAKE
    assert round_row["shooter_id"] == shooter.user_id
    assert round_row["keeper_id"] == keeper.user_id

    keyboard = msg.replies[0]["reply_markup"].inline_keyboard
    assert "قبول دروازه" in keyboard[0][0].text
    assert "رد چالش" in keyboard[0][1].text


async def test_challenge_usage_errors() -> None:
    shooter = await _funded(3)
    user = _user(shooter)

    bad = MutableMessage(text="دروازه", user=user)
    await penalty.cmd_penalty(bad)
    assert "دروازه [مبلغ شرط]" in bad.replies[0]["text"]

    huge = MutableMessage(text=f"دروازه {settings.penalty_max_bet + 1}", user=user)
    await penalty.cmd_penalty(huge)
    assert "شرط باید بین" in huge.replies[0]["text"]

    start = await _balance(shooter.user_id)
    orphan = MutableMessage(text=f"دروازه {STAKE}", user=user)
    await penalty.cmd_penalty(orphan)
    assert "روی پیام حریف ریپلای بزن" in orphan.replies[0]["text"]
    assert await _balance(shooter.user_id) == start


async def test_decline_refunds_shooter() -> None:
    shooter = await _funded(4)
    keeper = await _funded(5)
    start = await _balance(shooter.user_id)
    await _challenge(shooter, _user(keeper))
    round_id = await _round_id_for(shooter.user_id)

    card = MutableMessage(text="چالش", user=_user(keeper))
    stranger = User(id=BASE_ID + 99, is_bot=False, first_name="Nobody")
    blocked = FakeCall(f"pen:decline:{round_id}", card, stranger)
    await penalty.cb_decline(blocked)
    assert blocked.alerts[0] == "این چالش مال تو نیست."
    assert await _balance(shooter.user_id) == start - STAKE

    decline = FakeCall(f"pen:decline:{round_id}", card, _user(keeper))
    await penalty.cb_decline(decline)
    assert await _balance(shooter.user_id) == start
    assert (await _round(round_id))["status"] == "declined"


async def test_challenge_cooldown_blocks_second_challenge() -> None:
    shooter = await _funded(6)
    keeper = await _funded(7)
    rival = await _funded(8)
    await _challenge(shooter, _user(keeper))

    again = await _challenge(shooter, _user(rival))
    assert "cools down" in again.replies[-1]["text"]
    rows = await db.fetchall(
        "SELECT id FROM penalty_rounds WHERE shooter_id = ?", (shooter.user_id,)
    )
    assert len(rows) == 1


# ---------------------------------------------------------------------------
# Hidden save → shot
# ---------------------------------------------------------------------------


async def test_accept_puts_keeper_on_the_line_and_save_stays_hidden() -> None:
    shooter = await _funded(9)
    keeper = await _funded(10)
    keeper_start = await _balance(keeper.user_id)

    round_id, card = await _start_round(shooter, keeper)
    round_row = await _round(round_id)
    assert round_row["status"] == "keeper_pick"
    assert round_row["keeper_pick"] is None
    assert await _balance(keeper.user_id) == keeper_start - STAKE  # escrowed at accept

    # Only the keeper may commit a save.
    stranger = User(id=BASE_ID + 98, is_bot=False, first_name="Nobody")
    stray = FakeCall(f"pen:save:{round_id}:{LEFT}", card, stranger)
    await penalty.cb_save(stray)
    assert stray.alerts, "stranger must be rejected"

    shooter_press = FakeCall(f"pen:save:{round_id}:{LEFT}", card, _user(shooter))
    await penalty.cb_save(shooter_press)
    assert shooter_press.alerts, "the shooter cannot save for the keeper"

    keeper_press = FakeCall(f"pen:save:{round_id}:{MIDDLE}", card, _user(keeper))
    await penalty.cb_save(keeper_press)
    round_row = await _round(round_id)
    assert round_row["status"] == "shooter_pick"
    assert round_row["keeper_pick"] == "وسط"

    # The panel advanced to shot buttons, and the save is nowhere in the text.
    panel = card.replies[-1]
    assert "شلیک" in panel["text"]
    reveal = f"{penalty._SIDE_EMOJI['وسط']} وسط"  # noqa: SLF001
    assert reveal not in panel["text"]
    keyboard = panel["reply_markup"].inline_keyboard
    assert keyboard[0][0].callback_data.startswith(f"pen:shoot:{round_id}:")
    assert keeper_press.answers and "ثبت شد" in keeper_press.answers[0]


async def test_shooter_wins_when_shot_misses_the_save() -> None:
    shooter = await _funded(11)
    keeper = await _funded(12)
    shooter_start = await _balance(shooter.user_id)
    keeper_start = await _balance(keeper.user_id)

    round_id, card = await _start_round(shooter, keeper)
    await penalty.cb_save(FakeCall(f"pen:save:{round_id}:{LEFT}", card, _user(keeper)))

    shot = FakeCall(f"pen:shoot:{round_id}:{RIGHT}", card, _user(shooter))
    await penalty.cb_shoot(shot)
    assert not shot.alerts

    round_row = await _round(round_id)
    assert round_row["status"] == "resolved"
    assert round_row["winner_id"] == shooter.user_id
    assert round_row["keeper_pick"] == "چپ"
    assert round_row["shooter_pick"] == "راست"

    # Shooter takes the whole pot: -stake escrow + 2×stake payout.
    assert await _balance(shooter.user_id) == shooter_start + STAKE
    assert await _balance(keeper.user_id) == keeper_start - STAKE

    final = card.replies[-1]
    assert "⚽ ضربه گل شد" in final["text"]
    assert f"{penalty._SIDE_EMOJI['چپ']} چپ" in final["text"]  # noqa: SLF001
    assert f"{penalty._SIDE_EMOJI['راست']} راست" in final["text"]  # noqa: SLF001
    assert f"{STAKE * 2:,}" in final["text"]


async def test_keeper_wins_when_save_matches_the_shot() -> None:
    shooter = await _funded(13)
    keeper = await _funded(14)
    shooter_start = await _balance(shooter.user_id)
    keeper_start = await _balance(keeper.user_id)

    round_id, card = await _start_round(shooter, keeper)
    await penalty.cb_save(FakeCall(f"pen:save:{round_id}:{MIDDLE}", card, _user(keeper)))

    shot = FakeCall(f"pen:shoot:{round_id}:{MIDDLE}", card, _user(shooter))
    await penalty.cb_shoot(shot)
    assert not shot.alerts

    round_row = await _round(round_id)
    assert round_row["status"] == "resolved"
    assert round_row["winner_id"] == keeper.user_id
    assert await _balance(keeper.user_id) == keeper_start + STAKE
    assert await _balance(shooter.user_id) == shooter_start - STAKE
    assert "🥅 توپ رفت تور" in card.replies[-1]["text"]


async def test_shot_button_authority() -> None:
    shooter = await _funded(15)
    keeper = await _funded(16)

    # Before the save, a shot press is meaningless.
    round_id, card = await _start_round(shooter, keeper)
    early = FakeCall(f"pen:shoot:{round_id}:{LEFT}", card, _user(shooter))
    await penalty.cb_shoot(early)
    assert early.alerts[0] == "الان نوبت شلیک نیست."

    await penalty.cb_save(FakeCall(f"pen:save:{round_id}:{LEFT}", card, _user(keeper)))

    # Wrong role after the save.
    stranger = User(id=BASE_ID + 97, is_bot=False, first_name="Nobody")
    stray = FakeCall(f"pen:shoot:{round_id}:{RIGHT}", card, stranger)
    await penalty.cb_shoot(stray)
    assert "مهاجمه" in stray.alerts[0]

    keeper_shot = FakeCall(f"pen:shoot:{round_id}:{RIGHT}", card, _user(keeper))
    await penalty.cb_shoot(keeper_shot)
    assert "شلیک با مهاجمه" in keeper_shot.alerts[0]

    # The round is still waiting for the real shooter.
    assert (await _round(round_id))["status"] == "shooter_pick"


async def test_insufficient_funds_on_accept_voids_challenge() -> None:
    shooter = await _funded(17)
    keeper = await _funded(18)
    shooter_start = await _balance(shooter.user_id)
    await _challenge(shooter, _user(keeper))
    round_id = await _round_id_for(shooter.user_id)

    credits, _ = await economy.balances(keeper.user_id)
    await economy.spend(keeper.user_id, credits=credits, kind=ActivityKind.SYSTEM)

    card = MutableMessage(text="دروازه", user=_user(keeper))
    await penalty.cb_accept(FakeCall(f"pen:accept:{round_id}", card, _user(keeper)))

    assert (await _round(round_id))["status"] == "declined"
    assert await _balance(shooter.user_id) == shooter_start
    assert card.replies, "the broke keeper must see an error"
