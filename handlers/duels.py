"""/duel — challenge, escrow stake, and an interactive 2-round combat log.

Flow:
1. ``/duel @user [stake]`` posts a challenge with Accept/Decline buttons.
2. Accept escrows **both** stakes atomically (challenger's stake is escrowed
   at creation so they cannot spend it away mid-challenge).
3. The battle resolves deterministically from a stored seed, but each round
   is *revealed* one button-press at a time, so the log feels live without
   any server-side animation timers.
4. Decline/cancel/expiry refunds the escrow through the ledger.

Escrow lives in the wallet as a normal debit with kind ``DUEL_STAKE``; the
pot is rebuilt from the duels row, never from memory, so a restart mid-fight
still settles correctly.
"""

from __future__ import annotations

import json
import logging
import secrets

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from config import settings
from database.connection import db
from handlers.common import answer_error, editable_message, esc, hydrate
from handlers.panel import render_panel
from models import ActivityKind, Player
from services import battle, economy
from services.game import GameError, InsufficientFunds, now

logger = logging.getLogger(__name__)
router = Router(name="duels")

# How long a challenge sits before it auto-expires (checked lazily on view).
_DUEL_TTL_SECONDS = 600


# ---------------------------------------------------------------------------
# Creation
# ---------------------------------------------------------------------------


def _parse_stake(args: str) -> tuple[int | None, str]:
    """Split ``/duel`` args into (stake, target token)."""
    parts = args.split()
    stake: int | None = None
    rest: list[str] = []
    for part in parts:
        if stake is None and part.isdigit():
            stake = int(part)
        else:
            rest.append(part)
    return stake, " ".join(rest)


async def _resolve_target(message: Message, token: str) -> Player | None:
    """Find the challenged player via reply or numeric id.

    Telegram's API cannot resolve a bare ``@username`` to an id without a
    member lookup that requires the id in the first place, so the supported
    paths are: reply to the target, or pass their numeric user id.
    """
    if message.reply_to_message and message.reply_to_message.from_user:
        target_user = message.reply_to_message.from_user
        return await hydrate(target_user.id, target_user.full_name, target_user.username)

    cleaned = token.lstrip("@")
    if cleaned.isdigit():
        user_id = int(cleaned)
        return await hydrate(user_id, f"Player {user_id}", None)
    return None


@router.message(Command("duel"))
async def cmd_duel(message: Message, command: CommandObject) -> None:
    user = message.from_user
    if user is None:
        return
    try:
        stake_arg, token = _parse_stake(command.args or "")
        stake = stake_arg if stake_arg is not None else settings.duel_min_bet
        if not settings.duel_min_bet <= stake <= settings.duel_max_bet:
            raise GameError(
                f"stake must be {settings.duel_min_bet:,}-{settings.duel_max_bet:,}"
            )

        challenger = await hydrate(user.id, user.full_name, user.username)
        opponent = await _resolve_target(message, token)
        if opponent is None:
            await message.reply(
                "Usage: reply to someone with <code>/duel [stake]</code>, "
                "or <code>/duel @username [stake]</code>."
            )
            return
        if opponent.user_id == challenger.user_id:
            raise GameError("you cannot duel yourself")

        economy.require_ready(user.id, "duel", settings.cooldown_duel)

        seed = secrets.randbits(63)
        async with db.write() as conn:
            # Escrow the challenger's stake immediately: no spending it away.
            await economy.mutate(
                conn,
                challenger.user_id,
                credits=-stake,
                kind=ActivityKind.DUEL_STAKE,
                ref="duel:escrow",
            )
            from services.game import spend_energy_conn

            await spend_energy_conn(conn, challenger.user_id, settings.duel_energy_cost)
            cursor = await conn.execute(
                """
                INSERT INTO duels (chat_id, challenger_id, opponent_id, stake,
                                   status, seed, created_at)
                VALUES (?, ?, ?, ?, 'pending', ?, ?)
                """,
                (message.chat.id, challenger.user_id, opponent.user_id, stake, seed, now()),
            )
            duel_id = cursor.lastrowid
            await cursor.close()

        markup = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=f"⚔️ Accept ({stake:,}cr)",
                        callback_data=f"duel:accept:{duel_id}",
                    ),
                    InlineKeyboardButton(
                        text="🚫 Decline",
                        callback_data=f"duel:decline:{duel_id}",
                    ),
                ]
            ]
        )
        await message.reply(
            f"⚔️ <b>{esc(challenger.display_tag)}</b> challenges "
            f"<b>{esc(opponent.display_tag)}</b>!\n"
            f"Stake: <b>{stake:,}</b> credits each · pot {stake * 2:,}\n"
            f"{esc(opponent.display_tag)}, accept within {_DUEL_TTL_SECONDS // 60} min.",
            reply_markup=markup,
        )
    except Exception as exc:  # noqa: BLE001
        await answer_error(exc, message=message)


# ---------------------------------------------------------------------------
# Accept / decline
# ---------------------------------------------------------------------------


async def _load_duel(duel_id: int) -> dict | None:
    row = await db.fetchone("SELECT * FROM duels WHERE id = ?", (duel_id,))
    return dict(row) if row else None


async def _settle(duel: dict, winner_id: int, log: dict) -> None:
    """Split the pot (minus rake) and award EXP — one transaction.

    Ledger shape: the winner is credited the full pot, then a ``DUEL_RAKE``
    row books the house skim as a real negative delta.  Net wallet effect is
    ``pot - rake`` and both rows reconcile against ``balance_after_*``.
    """
    stake = duel["stake"]
    pot = stake * 2
    rake = int(pot * settings.duel_house_rake)

    async with db.write() as conn:
        await economy.mutate(
            conn,
            winner_id,
            credits=pot,
            kind=ActivityKind.DUEL_PAYOUT,
            ref=f"duel:{duel['id']}:win",
        )
        if rake:
            await economy.mutate(
                conn,
                winner_id,
                credits=-rake,
                kind=ActivityKind.DUEL_RAKE,
                ref=f"duel:{duel['id']}:rake",
            )

        loser_id = (
            duel["opponent_id"]
            if duel["challenger_id"] == winner_id
            else duel["challenger_id"]
        )
        from services.game import grant_exp_conn

        await grant_exp_conn(conn, winner_id, settings.duel_exp_win)
        await grant_exp_conn(conn, loser_id, settings.duel_exp_loss)

        await conn.execute(
            "UPDATE duels SET status = 'resolved', winner_id = ?, log_json = ?, "
            "resolved_at = ? WHERE id = ?",
            (winner_id, json.dumps(log), now(), duel["id"]),
        )


async def _refund(duel: dict, kind: ActivityKind, status: str) -> None:
    async with db.write() as conn:
        await economy.mutate(
            conn,
            duel["challenger_id"],
            credits=duel["stake"],
            kind=kind,
            ref=f"duel:{duel['id']}:refund",
        )
        await conn.execute(
            "UPDATE duels SET status = ?, resolved_at = ? WHERE id = ?",
            (status, now(), duel["id"]),
        )


def _is_expired(duel: dict) -> bool:
    return (
        duel["status"] == "pending"
        and now() - duel["created_at"] > _DUEL_TTL_SECONDS
    )


class _RaceLost(GameError):
    """Another accept/decline won the race; nothing to do (rolled back)."""


async def _accept_escrow(
    duel: dict, opponent: Player
) -> None:
    """Escrow the opponent's stake inside one transaction.

    The status re-check happens *after* ``BEGIN IMMEDIATE``, so a concurrent
    accept/decline serialises behind us; a lost race raises :class:`_RaceLost`
    which rolls back the energy spend too (no early ``return`` — that would
    commit a half-applied transaction).
    """
    async with db.write() as conn:
        cursor = await conn.execute(
            "SELECT status FROM duels WHERE id = ?", (duel["id"],)
        )
        row = await cursor.fetchone()
        await cursor.close()
        if row is None or row["status"] != "pending":
            raise _RaceLost("already resolved")

        from services.game import spend_energy_conn

        await spend_energy_conn(conn, opponent.user_id, settings.duel_energy_cost)
        await economy.mutate(
            conn,
            opponent.user_id,
            credits=-duel["stake"],
            kind=ActivityKind.DUEL_STAKE,
            ref=f"duel:{duel['id']}:escrow",
        )


@router.callback_query(F.data.startswith("duel:accept:"))
async def cb_accept(call: CallbackQuery) -> None:
    user = call.from_user
    if user is None or call.data is None:
        return
    duel_id = int(call.data.rsplit(":", 1)[1])
    try:
        duel = await _load_duel(duel_id)
        if duel is None:
            await call.answer("That duel is gone.", show_alert=True)
            return
        if duel["status"] != "pending":
            await call.answer(f"Already {duel['status']}.", show_alert=True)
            return
        if _is_expired(duel):
            await _refund(duel, ActivityKind.DUEL_REFUND, "expired")
            await call.answer("Challenge expired — stake refunded.", show_alert=True)
            return
        if duel["opponent_id"] != user.id:
            await call.answer("Only the challenged player can accept.", show_alert=True)
            return

        challenger = await hydrate(duel["challenger_id"], "Challenger", None)
        opponent = await hydrate(user.id, user.full_name, user.username)

        # Escrow the opponent first: if they can't pay, nothing resolves.
        await _accept_escrow(duel, opponent)

        result = battle.simulate(
            battle.fighter_of(challenger), battle.fighter_of(opponent), duel["seed"]
        )
        names = {
            challenger.user_id: challenger.display_tag,
            opponent.user_id: opponent.display_tag,
        }
        log = {
            "rounds": result.rounds,
            "lines": result.log_lines(names),
            "revealed": 0,
            "winner": result.winner_id,
            "names": {str(k): v for k, v in names.items()},
            "stake": duel["stake"],
            "payout": int(duel["stake"] * 2 * (1 - settings.duel_house_rake)),
        }

        # simulate() always picks a winner (sudden death on HP tie); the
        # fallback keeps mypy honest about its ``int | None`` annotation.
        winner_id = result.winner_id
        if winner_id is None:  # pragma: no cover - defensive only
            raise GameError("battle produced no winner")
        await _settle(duel, winner_id, log)
        await _render_round(call, duel_id, log, edit=True)
    except _RaceLost:
        await call.answer("Too late — already resolved.", show_alert=True)
    except InsufficientFunds as exc:
        await answer_error(exc, callback=call)
        # Opponent can't cover the stake: void the challenge cleanly.
        duel = await _load_duel(duel_id)
        if duel and duel["status"] == "pending" and duel["opponent_id"] == user.id:
            await _refund(duel, ActivityKind.DUEL_REFUND, "declined")
    except Exception as exc:  # noqa: BLE001
        await answer_error(exc, callback=call)


@router.callback_query(F.data.startswith("duel:decline:"))
async def cb_decline(call: CallbackQuery) -> None:
    user = call.from_user
    if user is None or call.data is None:
        return
    duel_id = int(call.data.rsplit(":", 1)[1])
    try:
        duel = await _load_duel(duel_id)
        if duel is None or duel["status"] != "pending":
            await call.answer("Nothing to decline.", show_alert=True)
            return
        if duel["opponent_id"] != user.id and duel["challenger_id"] != user.id:
            await call.answer("Not your duel.", show_alert=True)
            return
        status = "declined" if duel["opponent_id"] == user.id else "cancelled"
        await _refund(duel, ActivityKind.DUEL_REFUND, status)
        await call.answer("Challenge refused — stake returned.")
        await render_panel(
            editable_message(call),
            text=f"🚫 Duel #{duel_id} {status}. Stake refunded.",
        )
    except Exception as exc:  # noqa: BLE001
        await answer_error(exc, callback=call)


# ---------------------------------------------------------------------------
# Round-by-round reveal
# ---------------------------------------------------------------------------


def _render_log_body(log: dict, revealed: int) -> str:
    """Only the first ``revealed`` strike lines are visible so far."""
    lines = log["lines"][: max(0, revealed)]
    return "\n".join(lines) if lines else "Steel is drawn…"


async def _render_round(
    call: CallbackQuery, duel_id: int, log: dict, *, edit: bool
) -> None:
    revealed = log.get("revealed", 0)
    total = len(log["lines"])
    winner = log["winner"]
    names = log["names"]

    title = f"⚔️ <b>Duel #{duel_id}</b> — {log['stake']:,}cr stake"
    body = _render_log_body(log, revealed)

    if revealed >= total:
        winner_name = names.get(str(winner), "???")
        payout = int(log["payout"])
        body += (
            f"\n\n🏁 <b>{esc(winner_name)} wins!</b> "
            f"Pot {log['stake'] * 2:,}cr → {payout:,}cr "
            f"(rake {settings.duel_house_rake:.0%})"
        )
        markup = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🏠 Card", callback_data="act:me"
                    )
                ]
            ]
        )
    else:
        markup = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="⚡ Next round",
                        callback_data=f"duel:round:{duel_id}",
                    )
                ]
            ]
        )

    text = f"{title}\n{body}"
    message = editable_message(call)
    if message is None:
        return
    if edit:
        await render_panel(message, text=text, reply_markup=markup)
    else:
        await message.answer(text, reply_markup=markup)


@router.callback_query(F.data.startswith("duel:round:"))
async def cb_round(call: CallbackQuery) -> None:
    user = call.from_user
    if user is None or call.data is None:
        return
    duel_id = int(call.data.rsplit(":", 1)[1])
    duel = await _load_duel(duel_id)
    if duel is None:
        await call.answer("Gone.", show_alert=True)
        return
    if user.id not in (duel["challenger_id"], duel["opponent_id"]):
        await call.answer("Not your fight.", show_alert=True)
        return

    try:
        log = json.loads(duel["log_json"])
    except (TypeError, ValueError):
        await call.answer("Log corrupted.", show_alert=True)
        return

    if log.get("revealed", 0) >= len(log["lines"]):
        await call.answer("Fight's over.", show_alert=True)
        return

    log["revealed"] += 1
    # Persist so both players see the same reveal position after a reload.
    async with db.write() as conn:
        await conn.execute(
            "UPDATE duels SET log_json = ? WHERE id = ?",
            (json.dumps(log), duel_id),
        )
    await call.answer()
    await _render_round(call, duel_id, log, edit=True)
