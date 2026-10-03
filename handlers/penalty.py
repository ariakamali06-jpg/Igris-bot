"""دروازه — two-player penalty: keeper commits a hidden save, shooter answers.

Flow mirrors ``handlers/duels.py`` (reply-challenge + accept button + escrow
at creation), then runs a two-step commit: the keeper picks چپ/وسط/راست first
and their choice stays hidden in the row until the shooter has picked too.
``shot != save`` hands the pot to the shooter, otherwise the keeper keeps it —
the pot is rebuilt from the ``penalty_rounds`` row, never from memory.
"""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import CommandObject
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from config import settings
from database.connection import db
from handlers.common import (
    CommandOrText,
    answer_error,
    editable_message,
    esc,
    extract_args,
    hydrate,
)
from handlers.panel import render_panel
from models import ActivityKind, Player
from services import economy
from services.game import GameError, InsufficientFunds, now

logger = logging.getLogger(__name__)
router = Router(name="penalty")

PENALTY_WORDS = {"دروازه"}
ACTION_PENALTY = "penalty"

# Goal sides, in button order. Callbacks carry the index, never the Persian.
SIDES: tuple[str, ...] = ("چپ", "وسط", "راست")
_SIDE_ALIASES: dict[str, int] = {
    "چپ": 0,
    "left": 0,
    "وسط": 1,
    "middle": 1,
    "center": 1,
    "راست": 2,
    "right": 2,
}
_SIDE_EMOJI = {"چپ": "⬅️", "وسط": "⬆️", "راست": "➡️"}

_USAGE = (
    "🥅 <b>دروازه — پنالتی دو نفره در کوچه‌های تیرامیکس</b>\n"
    "روی پیام حریف ریپلای بزن و بنویس:\n"
    "<code>دروازه [مبلغ شرط]</code>\n"
    f"مبلغ: {settings.penalty_min_bet:,} تا {settings.penalty_max_bet:,} سکه · "
    "گلر اول ضربه رو ثبت می‌کنه (پنهان)، مهاجم بعداً شلیک می‌کنه.\n"
    "گل نشدن ضربه = برنده مهاجم · گرفتنش = برنده گلر."
)


# ---------------------------------------------------------------------------
# Creation
# ---------------------------------------------------------------------------


async def _resolve_target(message: Message, token: str) -> Player | None:
    if message.reply_to_message and message.reply_to_message.from_user:
        target = message.reply_to_message.from_user
        return await hydrate(target.id, target.full_name, target.username)
    cleaned = token.lstrip("@")
    if cleaned.isdigit():
        return await hydrate(int(cleaned), f"Player {cleaned}", None)
    return None


@router.message(CommandOrText(["penalty"], words=PENALTY_WORDS))
async def cmd_penalty(message: Message, command: CommandObject | None = None) -> None:
    user = message.from_user
    if user is None:
        return
    try:
        args = extract_args(message, command)
        if not args or not args[0].isdigit():
            raise GameError(_USAGE)
        stake = int(args[0])
        if not settings.penalty_min_bet <= stake <= settings.penalty_max_bet:
            raise GameError(
                f"شرط باید بین {settings.penalty_min_bet:,} تا "
                f"{settings.penalty_max_bet:,} سکه باشد."
            )

        shooter = await hydrate(user.id, user.full_name, user.username)
        keeper = await _resolve_target(message, " ".join(args[1:]))
        if keeper is None:
            await message.reply(
                "🥅 برای دعوت، روی پیام حریف ریپلای بزن و بنویس:\n"
                "<code>دروازه [مبلغ]</code>"
            )
            return
        if keeper.user_id == shooter.user_id:
            raise GameError("خودت که نمی‌تونی هم شلیک کنی هم گلری! 😏")

        cash, _ = await economy.balances(shooter.user_id)
        if cash < stake:
            raise GameError(f"جیبت خالیه! (موجودی: {cash:,} سکه برای شرط {stake:,})")
        keeper_cash, _ = await economy.balances(keeper.user_id)
        if keeper_cash < stake:
            raise GameError(
                f"موجودی {esc(keeper.display_tag)} برای این شرط کافی نیست "
                f"(نیاز: {stake:,} سکه)."
            )

        economy.require_ready(user.id, ACTION_PENALTY, settings.cooldown_penalty)

        async with db.write() as conn:
            await economy.mutate(
                conn,
                shooter.user_id,
                credits=-stake,
                kind=ActivityKind.ARCADE_STAKE,
                ref="penalty:escrow",
            )
            cursor = await conn.execute(
                """
                INSERT INTO penalty_rounds (chat_id, shooter_id, keeper_id, stake,
                                            status, created_at, updated_at)
                VALUES (?, ?, ?, ?, 'pending', ?, ?)
                """,
                (message.chat.id, shooter.user_id, keeper.user_id, stake, now(), now()),
            )
            round_id = cursor.lastrowid
            await cursor.close()

        markup = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=f"🥅 قبول دروازه ({stake:,} سکه)",
                        callback_data=f"pen:accept:{round_id}",
                    ),
                    InlineKeyboardButton(
                        text="🚫 رد چالش",
                        callback_data=f"pen:decline:{round_id}",
                    ),
                ]
            ]
        )
        await message.reply(
            f"🥅 <b>{esc(shooter.display_tag)}</b> پشت نقطه پنالتی وایساد و "
            f"<b>{esc(keeper.display_tag)}</b> روی خط دروازه!\n\n"
            f"💰 شرط هر نفر: <b>{stake:,}</b> سکه | پات: <b>{stake * 2:,}</b> سکه\n"
            f"⏳ {esc(keeper.display_tag)}، تا "
            f"{settings.penalty_challenge_seconds // 60} دقیقه دیگه قبول کن!",
            reply_markup=markup,
        )
    except Exception as exc:  # noqa: BLE001
        await answer_error(exc, message=message)


# ---------------------------------------------------------------------------
# Load / settle / void helpers
# ---------------------------------------------------------------------------


async def _load(round_id: int) -> dict | None:
    row = await db.fetchone("SELECT * FROM penalty_rounds WHERE id = ?", (round_id,))
    return dict(row) if row else None


def _is_expired(round_row: dict) -> bool:
    if round_row["status"] == "pending":
        return now() - int(round_row["created_at"]) > settings.penalty_challenge_seconds
    if round_row["status"] in ("keeper_pick", "shooter_pick"):
        return now() - int(round_row["updated_at"]) > settings.penalty_turn_seconds
    return False


async def _void(round_row: dict, status: str) -> None:
    refundees = [round_row["shooter_id"]]
    if round_row["status"] in ("keeper_pick", "shooter_pick"):
        refundees.append(round_row["keeper_id"])
    async with db.write() as conn:
        for payer in refundees:
            await economy.mutate(
                conn,
                int(payer),
                credits=int(round_row["stake"]),
                kind=ActivityKind.ARCADE_REFUND,
                ref=f"penalty:{round_row['id']}:refund",
            )
        await conn.execute(
            "UPDATE penalty_rounds SET status = ?, resolved_at = ?, updated_at = ? "
            "WHERE id = ?",
            (status, now(), now(), round_row["id"]),
        )


async def _settle(round_row: dict, winner_id: int) -> None:
    """Winner takes the whole pot — one transaction, ledger rows for both legs."""
    pot = int(round_row["stake"]) * 2
    async with db.write() as conn:
        await economy.mutate(
            conn,
            winner_id,
            credits=pot,
            kind=ActivityKind.ARCADE_PAYOUT,
            ref=f"penalty:{round_row['id']}:win",
        )
        await conn.execute(
            "UPDATE penalty_rounds SET status = 'resolved', winner_id = ?, "
            "resolved_at = ?, updated_at = ? WHERE id = ?",
            (winner_id, now(), now(), round_row["id"]),
        )


def _side_markup(round_id: int, action: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"{_SIDE_EMOJI[side]} {side}",
                    callback_data=f"pen:{action}:{round_id}:{index}",
                )
                for index, side in enumerate(SIDES)
            ]
        ]
    )


async def _names(round_row: dict) -> tuple[Player, Player]:
    shooter = await hydrate(int(round_row["shooter_id"]), "Shooter", None)
    keeper = await hydrate(int(round_row["keeper_id"]), "Keeper", None)
    return shooter, keeper


async def _render(call: CallbackQuery, round_row: dict, text: str) -> None:
    message = editable_message(call)
    if message is None:
        return
    action = "save" if round_row["status"] == "keeper_pick" else "shoot"
    await render_panel(
        message,
        text=text,
        reply_markup=_side_markup(int(round_row["id"]), action),
    )


# ---------------------------------------------------------------------------
# Accept / decline
# ---------------------------------------------------------------------------


class _RaceLost(GameError):
    """Another accept/decline won the race; nothing to do (rolled back)."""


async def _accept_escrow(round_row: dict, keeper: Player) -> None:
    async with db.write() as conn:
        cursor = await conn.execute(
            "SELECT status FROM penalty_rounds WHERE id = ?", (round_row["id"],)
        )
        row = await cursor.fetchone()
        await cursor.close()
        if row is None or row["status"] != "pending":
            raise _RaceLost("already resolved")
        await economy.mutate(
            conn,
            keeper.user_id,
            credits=-int(round_row["stake"]),
            kind=ActivityKind.ARCADE_STAKE,
            ref=f"penalty:{round_row['id']}:escrow",
        )
        await conn.execute(
            "UPDATE penalty_rounds SET status = 'keeper_pick', updated_at = ? WHERE id = ?",
            (now(), round_row["id"]),
        )


@router.callback_query(F.data.startswith("pen:accept:"))
async def cb_accept(call: CallbackQuery) -> None:
    user = call.from_user
    if user is None or call.data is None:
        return
    round_id = int(call.data.rsplit(":", 1)[1])
    try:
        round_row = await _load(round_id)
        if round_row is None:
            await call.answer("این چالش پاک شده است.", show_alert=True)
            return
        if round_row["status"] != "pending":
            await call.answer("این پنالتی قبلاً خاتمه یافته است.", show_alert=True)
            return
        if _is_expired(round_row):
            await _void(round_row, "expired")
            await call.answer("مهلت چالش تمام شد — شرط برگشت.", show_alert=True)
            return
        if int(round_row["keeper_id"]) != user.id:
            await call.answer("فقط گلرِ دعوت‌شده می‌تونه قبول کنه!", show_alert=True)
            return

        keeper = await hydrate(user.id, user.full_name, user.username)
        await _accept_escrow(round_row, keeper)
        round_row = await _load(round_id)
        assert round_row is not None
        shooter, guard = await _names(round_row)
        await call.answer("قلم‌ها روی خط — اول گلر انتخاب می‌کنه!")
        await _render(
            call,
            round_row,
            (
                f"🥅 پنالتی #{round_id} — شرط <b>{int(round_row['stake']):,}</b> سکه\n\n"
                f"🧤 گلر <b>{esc(guard.display_tag)}</b>: سمت ضربه رو ثبت کن — "
                "انتخابت تا شلیک مهاجم پنهون می‌مونه 🔒\n"
                f"🎯 مهاجم <b>{esc(shooter.display_tag)}</b>: صبر کن، گلر داره فکر می‌کنه."
            ),
        )
    except _RaceLost:
        await call.answer("دیر شد — چالش قبلاً پاسخ داده شده.", show_alert=True)
    except InsufficientFunds as exc:
        await answer_error(exc, callback=call)
        round_row = await _load(round_id)
        if (
            round_row
            and round_row["status"] == "pending"
            and int(round_row["keeper_id"]) == user.id
        ):
            await _void(round_row, "declined")
    except Exception as exc:  # noqa: BLE001
        await answer_error(exc, callback=call)


@router.callback_query(F.data.startswith("pen:decline:"))
async def cb_decline(call: CallbackQuery) -> None:
    user = call.from_user
    if user is None or call.data is None:
        return
    round_id = int(call.data.rsplit(":", 1)[1])
    try:
        round_row = await _load(round_id)
        if round_row is None or round_row["status"] != "pending":
            await call.answer("چیزی برای رد کردن نیست.", show_alert=True)
            return
        if user.id not in (int(round_row["shooter_id"]), int(round_row["keeper_id"])):
            await call.answer("این چالش مال تو نیست.", show_alert=True)
            return
        status = "declined" if user.id == int(round_row["keeper_id"]) else "cancelled"
        await _void(round_row, status)
        who = "رد" if status == "declined" else "لغو"
        await call.answer(f"چالش {who} شد — شرط مسترد گردید.")
        message = editable_message(call)
        if message is not None:
            await render_panel(
                message,
                text=(
                    f"🥅 پنالتی #{round_id} توسط <b>{esc(user.full_name)}</b> {who} شد؛ "
                    "شرط به جیبش برگشت."
                ),
            )
    except Exception as exc:  # noqa: BLE001
        await answer_error(exc, callback=call)


# ---------------------------------------------------------------------------
# Keeper save → shooter shot
# ---------------------------------------------------------------------------


@router.callback_query(F.data.startswith("pen:save:"))
async def cb_save(call: CallbackQuery) -> None:
    user = call.from_user
    if user is None or call.data is None:
        return
    try:
        _, _, raw_id, raw_side = call.data.split(":")
        round_id, side_idx = int(raw_id), int(raw_side)
    except ValueError:
        await call.answer("سمت نامعتبر است.", show_alert=True)
        return

    try:
        if not 0 <= side_idx < len(SIDES):
            await call.answer("سمت نامعتبر است.", show_alert=True)
            return
        round_row = await _load(round_id)
        if round_row is None or round_row["status"] != "keeper_pick":
            await call.answer("الان نوبت انتخاب گلر نیست.", show_alert=True)
            return
        if _is_expired(round_row):
            await _void(round_row, "expired")
            await call.answer("وقت تموم شد — شرط‌ها برگشت.", show_alert=True)
            return
        if int(round_row["keeper_id"]) != user.id:
            await call.answer("دستت به این دکمه نمی‌ره، مهاجم! 🧤", show_alert=True)
            return

        pick = SIDES[side_idx]
        async with db.write() as conn:
            cursor = await conn.execute(
                "SELECT status FROM penalty_rounds WHERE id = ?", (round_id,)
            )
            row = await cursor.fetchone()
            await cursor.close()
            if row is None or row["status"] != "keeper_pick":
                raise _RaceLost("save already taken")
            await conn.execute(
                "UPDATE penalty_rounds SET keeper_pick = ?, status = 'shooter_pick', "
                "updated_at = ? WHERE id = ?",
                (pick, now(), round_id),
            )
        round_row = await _load(round_id)
        assert round_row is not None
        shooter, guard = await _names(round_row)
        # The keeper's pick never appears in this text — hidden until both land.
        await call.answer("ثبت شد؛ حالا نوبت مهاجمه! 🎯")
        await _render(
            call,
            round_row,
            (
                f"🥅 پنالتی #{round_id} — شرط <b>{int(round_row['stake']):,}</b> سکه\n\n"
                f"🧤 گلر <b>{esc(guard.display_tag)}</b> پشت خط ایستاد (انتخابش پنهانه 🔒)\n"
                f"🎯 مهاجم <b>{esc(shooter.display_tag)}</b>: سمت شلیک رو بزن — "
                "چپ، وسط یا راست؟"
            ),
        )
    except _RaceLost:
        await call.answer("گلر زودتر از تو ثبت کرد.", show_alert=True)
    except Exception as exc:  # noqa: BLE001
        await answer_error(exc, callback=call)


@router.callback_query(F.data.startswith("pen:shoot:"))
async def cb_shoot(call: CallbackQuery) -> None:
    user = call.from_user
    if user is None or call.data is None:
        return
    try:
        _, _, raw_id, raw_side = call.data.split(":")
        round_id, side_idx = int(raw_id), int(raw_side)
    except ValueError:
        await call.answer("سمت نامعتبر است.", show_alert=True)
        return

    try:
        if not 0 <= side_idx < len(SIDES):
            await call.answer("سمت نامعتبر است.", show_alert=True)
            return
        round_row = await _load(round_id)
        if round_row is None or round_row["status"] != "shooter_pick":
            await call.answer("الان نوبت شلیک نیست.", show_alert=True)
            return
        if _is_expired(round_row):
            await _void(round_row, "expired")
            await call.answer("وقت تموم شد — شرط‌ها برگشت.", show_alert=True)
            return
        if int(round_row["shooter_id"]) != user.id:
            await call.answer("تو گلری، شلیک با مهاجمه! 🎯", show_alert=True)
            return

        shot = SIDES[side_idx]
        save = str(round_row["keeper_pick"])
        shooter_won = shot != save
        winner_id = (
            int(round_row["shooter_id"]) if shooter_won else int(round_row["keeper_id"])
        )

        # Re-validate status inside the settle transaction (race guard).
        async with db.write() as conn:
            cursor = await conn.execute(
                "SELECT status, keeper_pick FROM penalty_rounds WHERE id = ?", (round_id,)
            )
            fresh = await cursor.fetchone()
            await cursor.close()
            if fresh is None or fresh["status"] != "shooter_pick":
                raise _RaceLost("round already settled")
            await conn.execute(
                "UPDATE penalty_rounds SET shooter_pick = ? WHERE id = ?",
                (shot, round_id),
            )
        await _settle(round_row, winner_id)

        shooter, guard = await _names(round_row)
        winner = shooter if shooter_won else guard
        pot = int(round_row["stake"]) * 2
        verdict = (
            "🥅 توپ رفت تور! گلر جهت رو خوند — گلر برنده شد!"
            if not shooter_won
            else "⚽ ضربه گل شد! گلر یه طرف پرید — مهاجم برنده شد!"
        )
        await call.answer("پایان پنالتی! 🥅")
        message = editable_message(call)
        if message is not None:
            await render_panel(
                message,
                text=(
                    f"🥅 <b>پنالتی #{round_id} تمام شد</b>\n\n"
                    f"🧤 گلر: <b>{_SIDE_EMOJI[save]} {save}</b>\n"
                    f"🎯 مهاجم: <b>{_SIDE_EMOJI[shot]} {shot}</b>\n\n"
                    f"{verdict}\n"
                    f"🏆 <b>{esc(winner.display_tag)}</b> کل پات <b>{pot:,}</b> سکه رو برد!"
                ),
            )
    except _RaceLost:
        await call.answer("نتیجه همین حالا ثبت شد.", show_alert=True)
    except Exception as exc:  # noqa: BLE001
        await answer_error(exc, callback=call)
