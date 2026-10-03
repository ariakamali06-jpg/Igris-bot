"""Ocean port phase 2 — the arcade row: کازینو، قل‌سنگ، شانس، قفل.

One router, four house games. Each command parses its args *before* burning a
cooldown, funnels domain failures through :func:`answer_error`, and renders
the dict the service returned — services own all money movement and odds.
"""

from __future__ import annotations

import logging

from aiogram import Router
from aiogram.filters import CommandObject
from aiogram.types import Message

from config import settings
from handlers.common import CommandOrText, answer_error, extract_args, hydrate
from services import casino_games, economy
from services.game import GameError

logger = logging.getLogger(__name__)
router = Router(name="phase2_casino")

CASINO_WORDS = {"کازینو"}
RPS_WORDS = {"قل‌سنگ", "قلسنگ"}
GUESS_WORDS = {"شانس"}
SAFE_WORDS = {"قفل"}

_SLOT_USAGE = (
    "🎰 <b>کازینوی کوچه‌های تیرامیکس</b>\n"
    "سه چرخه می‌چرخن، سکه‌ها می‌شکنن:\n"
    "<code>کازینو [مبلغ]</code>\n"
    f"مبلغ شرط: {settings.casino_min_bet:,} تا {settings.casino_max_bet:,} سکه.\n"
    "<i>سه تا از یک جنس = جایزه سنگین؛ دوتا از یک جنس = شرط برمی‌گرده.</i>"
)
_RPS_USAGE = (
    "✊ قل‌سنگ با خانه:\n"
    "<code>قل‌سنگ [مبلغ] سنگ|کاغذ|قیچی</code>\n"
    f"مبلغ شرط: {settings.casino_min_bet:,} تا {settings.casino_max_bet:,} سکه · "
    "مساوی = برگشت شرط."
)
_GUESS_USAGE = (
    "🎲 شانس، عدد پنهان رو حدس بزن:\n"
    "<code>شانس [مبلغ] [عدد]</code>\n"
    f"عدد بین ۱ تا {settings.guess_number_max} · درست زدنی "
    f"{settings.guess_win_multiplier:g} برابر می‌شی.\n"
    f"مبلغ شرط: {settings.casino_min_bet:,} تا {settings.casino_max_bet:,} سکه."
)
_SAFE_USAGE = (
    "🔐 شکستن قفل سه‌رقمی:\n"
    "• <code>قفل</code> — وضعیت قفل فعلی\n"
    "• <code>قفل [مبلغ]</code> — سپرِ شرط و ساخت قفل جدید\n"
    "• <code>قفل [سه رقم]</code> — امتحان کد (وقتی قفلی بازه)\n"
    f"تلاش‌ها: {settings.safe_max_attempts} · باز شدن: "
    f"{settings.safe_payout_multiplier:g} برابر شرط · مبلغ: "
    f"{settings.casino_min_bet:,} تا {settings.casino_max_bet:,} سکه."
)


@router.message(CommandOrText(["casino", "slots"], words=CASINO_WORDS))
async def cmd_slots(message: Message, command: CommandObject | None = None) -> None:
    user = message.from_user
    if user is None:
        return
    try:
        args = extract_args(message, command)
        if len(args) != 1 or not args[0].isdigit():
            raise GameError(_SLOT_USAGE)
        bet = int(args[0])
        economy.require_ready(user.id, casino_games.ACTION_SLOT, settings.cooldown_slot)
        player = await hydrate(user.id, user.full_name, user.username)
        res = await casino_games.slots(player.user_id, bet)
        await message.reply(res["message"])
    except Exception as exc:  # noqa: BLE001
        await answer_error(exc, message=message)


@router.message(CommandOrText(["rps"], words=RPS_WORDS))
async def cmd_rps(message: Message, command: CommandObject | None = None) -> None:
    user = message.from_user
    if user is None:
        return
    try:
        args = extract_args(message, command)
        if len(args) < 2 or not args[0].isdigit():
            raise GameError(_RPS_USAGE)
        bet = int(args[0])
        move = casino_games.normalize_rps_move(" ".join(args[1:]))  # validates first
        economy.require_ready(user.id, casino_games.ACTION_RPS, settings.cooldown_rps)
        player = await hydrate(user.id, user.full_name, user.username)
        res = await casino_games.rps(player.user_id, bet, move)
        await message.reply(res["message"])
    except Exception as exc:  # noqa: BLE001
        await answer_error(exc, message=message)


@router.message(CommandOrText(["guess"], words=GUESS_WORDS))
async def cmd_guess(message: Message, command: CommandObject | None = None) -> None:
    user = message.from_user
    if user is None:
        return
    try:
        args = extract_args(message, command)
        if len(args) != 2 or not args[0].isdigit() or not args[1].isdigit():
            raise GameError(_GUESS_USAGE)
        bet = int(args[0])
        number = int(args[1])
        economy.require_ready(user.id, casino_games.ACTION_GUESS, settings.cooldown_guess)
        player = await hydrate(user.id, user.full_name, user.username)
        res = await casino_games.guess(player.user_id, bet, number)
        await message.reply(res["message"])
    except Exception as exc:  # noqa: BLE001
        await answer_error(exc, message=message)


@router.message(CommandOrText(["safe"], words=SAFE_WORDS))
async def cmd_safe(message: Message, command: CommandObject | None = None) -> None:
    user = message.from_user
    if user is None:
        return
    try:
        player = await hydrate(user.id, user.full_name, user.username)
        args = extract_args(message, command)

        if not args:
            res = await casino_games.safe_status(player.user_id)
        elif len(args) == 1 and args[0].isdigit():
            # A live lock turns the number into a code attempt; otherwise it
            # is the stake that opens a fresh lock (documented in _SAFE_USAGE).
            if await casino_games.active_lock(player.user_id) is not None:
                economy.require_ready(
                    user.id, casino_games.ACTION_SAFE_ATTEMPT, settings.cooldown_safe_attempt
                )
                res = await casino_games.safe_attempt(player.user_id, args[0])
            else:
                economy.require_ready(
                    user.id, casino_games.ACTION_SAFE_START, settings.cooldown_safe_start
                )
                res = await casino_games.safe_start(player.user_id, int(args[0]))
        else:
            raise GameError(_SAFE_USAGE)
        await message.reply(res["message"])
    except Exception as exc:  # noqa: BLE001
        await answer_error(exc, message=message)
