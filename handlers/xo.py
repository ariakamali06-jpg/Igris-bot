"""دوز — two-player tic-tac-toe over the arcades of Tiramix.

Flow mirrors ``handlers/duels.py``: a reply-challenge escrows the challenger's
stake at creation (so it cannot be spent away mid-challenge), the accept
button escrows the opponent's stake inside one ``BEGIN IMMEDIATE`` transaction
with a status re-check, and every board move re-validates turn + cell inside
its own transaction. The pot is rebuilt from the ``xo_games`` row, never from
memory, so a restart mid-board still settles correctly.

Ledger shape matches the duel pit: the winner is credited the full pot, then
an ``ARCADE_RAKE`` row books the house skim as a real negative delta; a draw
refunds both escrows through ``ARCADE_REFUND`` rows.
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
router = Router(name="xo")

XO_WORDS = {"دوز"}
ACTION_XO = "xo"

# The eight lines that end a board — structural, not tunable odds.
_WIN_LINES: tuple[tuple[int, int, int], ...] = (
    (0, 1, 2),
    (3, 4, 5),
    (6, 7, 8),
    (0, 3, 6),
    (1, 4, 7),
    (2, 5, 8),
    (0, 4, 8),
    (2, 4, 6),
)
_MARK = {"X": "❌", "O": "⭕"}

_USAGE = (
    "❌⭕ <b>دوزِ زیرزمینی تیرامیکس</b>\n"
    "روی پیام حریف ریپلای بزن و بنویس:\n"
    "<code>دوز [مبلغ شرط]</code>\n"
    f"مبلغ: {settings.xo_min_bet:,} تا {settings.xo_max_bet:,} سکه · "
    f"کارمزد خانه روی پات: {settings.xo_house_rake:.0%} · مساوی = برگشت شرط."
)


# ---------------------------------------------------------------------------
# Creation
# ---------------------------------------------------------------------------


async def _resolve_target(message: Message, token: str) -> Player | None:
    """Opponent via reply (preferred) or a numeric id — same as the duel pit."""
    if message.reply_to_message and message.reply_to_message.from_user:
        target = message.reply_to_message.from_user
        return await hydrate(target.id, target.full_name, target.username)
    cleaned = token.lstrip("@")
    if cleaned.isdigit():
        return await hydrate(int(cleaned), f"Player {cleaned}", None)
    return None


@router.message(CommandOrText(["xo"], words=XO_WORDS))
async def cmd_xo(message: Message, command: CommandObject | None = None) -> None:
    user = message.from_user
    if user is None:
        return
    try:
        args = extract_args(message, command)
        if not args or not args[0].isdigit():
            raise GameError(_USAGE)
        stake = int(args[0])
        if not settings.xo_min_bet <= stake <= settings.xo_max_bet:
            raise GameError(
                f"شرط باید بین {settings.xo_min_bet:,} تا {settings.xo_max_bet:,} سکه باشد."
            )

        challenger = await hydrate(user.id, user.full_name, user.username)
        opponent = await _resolve_target(message, " ".join(args[1:]))
        if opponent is None:
            await message.reply(
                "❌⭕ برای دعوت، روی پیام حریف ریپلای بزن و بنویس:\n"
                "<code>دوز [مبلغ]</code>"
            )
            return
        if opponent.user_id == challenger.user_id:
            raise GameError("با خودت دوز که نمی‌زنی، آینه‌باز! 😏")

        cash, _ = await economy.balances(challenger.user_id)
        if cash < stake:
            raise GameError(f"جیبت خالیه! (موجودی: {cash:,} سکه برای شرط {stake:,})")
        rival_cash, _ = await economy.balances(opponent.user_id)
        if rival_cash < stake:
            raise GameError(
                f"موجودی {esc(opponent.display_tag)} برای این شرط کافی نیست "
                f"(نیاز: {stake:,} سکه)."
            )

        economy.require_ready(user.id, ACTION_XO, settings.cooldown_xo)

        async with db.write() as conn:
            await economy.mutate(
                conn,
                challenger.user_id,
                credits=-stake,
                kind=ActivityKind.ARCADE_STAKE,
                ref="xo:escrow",
            )
            cursor = await conn.execute(
                """
                INSERT INTO xo_games (chat_id, challenger_id, opponent_id, stake,
                                      status, created_at, updated_at)
                VALUES (?, ?, ?, ?, 'pending', ?, ?)
                """,
                (message.chat.id, challenger.user_id, opponent.user_id, stake, now(), now()),
            )
            game_id = cursor.lastrowid
            await cursor.close()

        markup = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=f"❌⭕ قبول دوز ({stake:,} سکه)",
                        callback_data=f"xo:accept:{game_id}",
                    ),
                    InlineKeyboardButton(
                        text="🚫 رد چالش",
                        callback_data=f"xo:decline:{game_id}",
                    ),
                ]
            ]
        )
        await message.reply(
            f"❌⭕ <b>{esc(challenger.display_tag)}</b> میز دوز رو پهن کرد و "
            f"<b>{esc(opponent.display_tag)}</b> رو صدا زد!\n\n"
            f"💰 شرط هر نفر: <b>{stake:,}</b> سکه | پات: <b>{stake * 2:,}</b> سکه\n"
            f"⏳ {esc(opponent.display_tag)}، تا "
            f"{settings.xo_challenge_seconds // 60} دقیقه دیگه قبول کن یا رد!",
            reply_markup=markup,
        )
    except Exception as exc:  # noqa: BLE001
        await answer_error(exc, message=message)


# ---------------------------------------------------------------------------
# Load / settle / void helpers
# ---------------------------------------------------------------------------


async def _load(game_id: int) -> dict | None:
    row = await db.fetchone("SELECT * FROM xo_games WHERE id = ?", (game_id,))
    return dict(row) if row else None


def _is_expired(game: dict) -> bool:
    if game["status"] == "pending":
        return now() - int(game["created_at"]) > settings.xo_challenge_seconds
    if game["status"] == "active":
        return now() - int(game["updated_at"]) > settings.xo_turn_seconds
    return False


async def _void(game: dict, status: str) -> None:
    """Decline / cancel / expiry: hand every escrow back through the ledger."""
    refundees = [game["challenger_id"]]
    if game["status"] == "active":
        refundees.append(game["opponent_id"])
    async with db.write() as conn:
        for payer in refundees:
            await economy.mutate(
                conn,
                int(payer),
                credits=int(game["stake"]),
                kind=ActivityKind.ARCADE_REFUND,
                ref=f"xo:{game['id']}:refund",
            )
        await conn.execute(
            "UPDATE xo_games SET status = ?, resolved_at = ?, updated_at = ? WHERE id = ?",
            (status, now(), now(), game["id"]),
        )


async def _settle(game: dict, winner_id: int | None) -> None:
    """Split the pot (minus rake) or refund a draw — one transaction."""
    stake = int(game["stake"])
    pot = stake * 2
    async with db.write() as conn:
        if winner_id is None:
            for payer in (game["challenger_id"], game["opponent_id"]):
                await economy.mutate(
                    conn,
                    int(payer),
                    credits=stake,
                    kind=ActivityKind.ARCADE_REFUND,
                    ref=f"xo:{game['id']}:draw",
                )
        else:
            await economy.mutate(
                conn,
                winner_id,
                credits=pot,
                kind=ActivityKind.ARCADE_PAYOUT,
                ref=f"xo:{game['id']}:win",
            )
            rake = int(pot * settings.xo_house_rake)
            if rake:
                await economy.mutate(
                    conn,
                    winner_id,
                    credits=-rake,
                    kind=ActivityKind.ARCADE_RAKE,
                    ref=f"xo:{game['id']}:rake",
                )
        await conn.execute(
            "UPDATE xo_games SET status = 'resolved', winner_id = ?, resolved_at = ?, "
            "updated_at = ? WHERE id = ?",
            (winner_id, now(), now(), game["id"]),
        )


def _winning_mark(board: str) -> str | None:
    for a, b, c in _WIN_LINES:
        if board[a] != "." and board[a] == board[b] == board[c]:
            return board[a]
    return None


# ---------------------------------------------------------------------------
# Board rendering
# ---------------------------------------------------------------------------


def _board_markup(game_id: int, board: str) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for row in range(3):
        buttons: list[InlineKeyboardButton] = []
        for col in range(3):
            idx = row * 3 + col
            buttons.append(
                InlineKeyboardButton(
                    text=_MARK.get(board[idx], "▫️"),
                    callback_data=f"xo:move:{game_id}:{idx}",
                )
            )
        rows.append(buttons)
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _render(call: CallbackQuery, game: dict, text: str) -> None:
    message = editable_message(call)
    if message is None:
        return
    await render_panel(
        message,
        text=text,
        reply_markup=_board_markup(int(game["id"]), game["board"]),
    )


async def _names(game: dict) -> tuple[Player, Player]:
    challenger = await hydrate(int(game["challenger_id"]), "Challenger", None)
    opponent = await hydrate(int(game["opponent_id"]), "Opponent", None)
    return challenger, opponent


async def _board_text(game: dict) -> str:
    challenger, opponent = await _names(game)
    stake = int(game["stake"])
    lines = [
        f"❌⭕ <b>میز دوز #{game['id']}</b> — شرط <b>{stake:,}</b> سکه (پات {stake * 2:,})",
        "",
        f"❌ {esc(challenger.display_tag)}  ·  ⭕ {esc(opponent.display_tag)}",
        "",
    ]
    for row in range(3):
        cells = " ".join(_MARK.get(game["board"][row * 3 + c], "▫️") for c in range(3))
        lines.append(f"<code>{cells}</code>")
    if game["turn_id"] == game["challenger_id"]:
        turn_name, turn_mark = challenger.display_tag, "❌"
    else:
        turn_name, turn_mark = opponent.display_tag, "⭕"
    lines.append("")
    lines.append(f"نوبت: {turn_mark} <b>{esc(turn_name)}</b> — روی خانه خالی بزن.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Accept / decline
# ---------------------------------------------------------------------------


class _RaceLost(GameError):
    """Another accept/decline won the race; nothing to do (rolled back)."""


async def _accept_escrow(game: dict, opponent: Player) -> None:
    """Escrow the opponent's stake with a status re-check after BEGIN IMMEDIATE."""
    async with db.write() as conn:
        cursor = await conn.execute(
            "SELECT status FROM xo_games WHERE id = ?", (game["id"],)
        )
        row = await cursor.fetchone()
        await cursor.close()
        if row is None or row["status"] != "pending":
            raise _RaceLost("already resolved")
        await economy.mutate(
            conn,
            opponent.user_id,
            credits=-int(game["stake"]),
            kind=ActivityKind.ARCADE_STAKE,
            ref=f"xo:{game['id']}:escrow",
        )
        await conn.execute(
            "UPDATE xo_games SET status = 'active', turn_id = ?, updated_at = ? "
            "WHERE id = ?",
            (int(game["challenger_id"]), now(), game["id"]),
        )


@router.callback_query(F.data.startswith("xo:accept:"))
async def cb_accept(call: CallbackQuery) -> None:
    user = call.from_user
    if user is None or call.data is None:
        return
    game_id = int(call.data.rsplit(":", 1)[1])
    try:
        game = await _load(game_id)
        if game is None:
            await call.answer("این میز پاک شده است.", show_alert=True)
            return
        if game["status"] != "pending":
            await call.answer("این دوز قبلاً خاتمه یافته است.", show_alert=True)
            return
        if _is_expired(game):
            await _void(game, "expired")
            await call.answer("مهلت چالش تمام شد — شرط برگشت.", show_alert=True)
            return
        if int(game["opponent_id"]) != user.id:
            await call.answer("فقط حریفِ دعوت‌شده می‌تونه قبول کنه!", show_alert=True)
            return

        opponent = await hydrate(user.id, user.full_name, user.username)
        await _accept_escrow(game, opponent)
        game = await _load(game_id)
        assert game is not None
        await call.answer("میز پهن شد — تویی ❌ شروع‌کننده!")
        await _render(call, game, await _board_text(game))
    except _RaceLost:
        await call.answer("دیر شد — دوز قبلاً پاسخ داده شده.", show_alert=True)
    except InsufficientFunds as exc:
        await answer_error(exc, callback=call)
        game = await _load(game_id)
        if game and game["status"] == "pending" and int(game["opponent_id"]) == user.id:
            await _void(game, "declined")
    except Exception as exc:  # noqa: BLE001
        await answer_error(exc, callback=call)


@router.callback_query(F.data.startswith("xo:decline:"))
async def cb_decline(call: CallbackQuery) -> None:
    user = call.from_user
    if user is None or call.data is None:
        return
    game_id = int(call.data.rsplit(":", 1)[1])
    try:
        game = await _load(game_id)
        if game is None or game["status"] != "pending":
            await call.answer("چیزی برای رد کردن نیست.", show_alert=True)
            return
        if user.id not in (int(game["challenger_id"]), int(game["opponent_id"])):
            await call.answer("این دوز مال تو نیست.", show_alert=True)
            return
        status = "declined" if user.id == int(game["opponent_id"]) else "cancelled"
        await _void(game, status)
        who = "رد" if status == "declined" else "لغو"
        await call.answer(f"چالش {who} شد — شرط مسترد گردید.")
        message = editable_message(call)
        if message is not None:
            await render_panel(
                message,
                text=(
                    f"❌⭕ دوز #{game_id} توسط <b>{esc(user.full_name)}</b> {who} شد؛ "
                    "شرط به جیبش برگشت."
                ),
            )
    except Exception as exc:  # noqa: BLE001
        await answer_error(exc, callback=call)


# ---------------------------------------------------------------------------
# Moves
# ---------------------------------------------------------------------------


@router.callback_query(F.data.startswith("xo:move:"))
async def cb_move(call: CallbackQuery) -> None:
    user = call.from_user
    if user is None or call.data is None:
        return
    try:
        _, _, raw_id, raw_idx = call.data.split(":")
        game_id, idx = int(raw_id), int(raw_idx)
    except ValueError:
        await call.answer("خانه نامعتبر است.", show_alert=True)
        return

    try:
        game = await _load(game_id)
        if game is None:
            await call.answer("این میز دیگه وجود نداره.", show_alert=True)
            return
        if user.id not in (int(game["challenger_id"]), int(game["opponent_id"])):
            await call.answer("تو بازیکن این دوز نیستی!", show_alert=True)
            return
        if game["status"] == "pending":
            await call.answer("هنوز قبول نشده — صبر کن حریف دکمه رو بزنه.", show_alert=True)
            return
        if game["status"] != "active":
            await call.answer("این دوز تمام شده است.", show_alert=True)
            return
        if _is_expired(game):
            await _void(game, "expired")
            await call.answer("وقت دوز تموم شد — شرط‌ها برگشت.", show_alert=True)
            return
        if int(game["turn_id"]) != user.id:
            await call.answer("نوبت تو نیست! ⏳", show_alert=True)
            return
        if not 0 <= idx < 9 or game["board"][idx] != ".":
            await call.answer("این خانه پرته — یکی دیگه رو امتحان کن.", show_alert=True)
            return

        mark = "X" if user.id == int(game["challenger_id"]) else "O"
        board = game["board"][:idx] + mark + game["board"][idx + 1 :]
        winner_mark = _winning_mark(board)
        draw = winner_mark is None and "." not in board
        challenger, opponent = await _names(game)

        if winner_mark or draw:
            winner_id: int | None = None
            if winner_mark == "X":
                winner_id = int(game["challenger_id"])
            elif winner_mark == "O":
                winner_id = int(game["opponent_id"])
            # Re-validate the move inside the settle transaction (race guard).
            async with db.write() as conn:
                cursor = await conn.execute(
                    "SELECT board, turn_id, status FROM xo_games WHERE id = ?",
                    (game_id,),
                )
                fresh = await cursor.fetchone()
                await cursor.close()
                if (
                    fresh is None
                    or fresh["status"] != "active"
                    or int(fresh["turn_id"]) != user.id
                    or fresh["board"][idx] != "."
                ):
                    raise GameError("این خانه رو همین حالا یکی برداشته.")
                game["board"] = board
                await conn.execute(
                    "UPDATE xo_games SET board = ? WHERE id = ?", (board, game_id)
                )
            await _settle(game, winner_id)
            stake = int(game["stake"])
            pot = stake * 2
            rake = int(pot * settings.xo_house_rake)
            if winner_id is not None:
                winner = challenger if winner_id == int(game["challenger_id"]) else opponent
                body = (
                    f"🏆 <b>{esc(winner.display_tag)}</b> دوز رو برد!\n"
                    f"پات {pot:,} سکه — کارمزد خانه {settings.xo_house_rake:.0%} "
                    f"({rake:,}) — جایزه خالص <b>{pot - rake:,}</b> سکه."
                )
            else:
                body = (
                    "🤝 میز پُر شد؛ مساوی! شرط هر دو برگشت، خانه دست‌خالی موند."
                )
            await call.answer("پایان بازی! 🎉" if winner_id is not None else "مساوی! 🤝")
            await _render(call, game, f"{await _board_text(game)}\n\n{body}")
            return

        # Plain move: hand the turn over.
        other_id = (
            int(game["opponent_id"])
            if user.id == int(game["challenger_id"])
            else int(game["challenger_id"])
        )
        async with db.write() as conn:
            cursor = await conn.execute(
                "SELECT board, turn_id, status FROM xo_games WHERE id = ?", (game_id,)
            )
            fresh = await cursor.fetchone()
            await cursor.close()
            if (
                fresh is None
                or fresh["status"] != "active"
                or int(fresh["turn_id"]) != user.id
                or fresh["board"][idx] != "."
            ):
                raise GameError("این خانه رو همین حالا یکی برداشته.")
            await conn.execute(
                "UPDATE xo_games SET board = ?, turn_id = ?, updated_at = ? WHERE id = ?",
                (board, other_id, now(), game_id),
            )
        game["board"] = board
        game["turn_id"] = other_id
        await call.answer("حرکت ثبت شد ✔")
        await _render(call, game, await _board_text(game))
    except Exception as exc:  # noqa: BLE001
        await answer_error(exc, callback=call)
