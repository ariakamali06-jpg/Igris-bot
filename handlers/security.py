"""سپر / دستبرد — anti-theft insurance and the bank robbery command."""

from __future__ import annotations

import logging

from aiogram import Router
from aiogram.filters import CommandObject
from aiogram.types import Message

from config import settings
from handlers.common import CommandOrText, answer_error, extract_args, hydrate
from services import economy, security
from services.game import GameError

logger = logging.getLogger(__name__)
router = Router(name="security")

SHIELD_WORDS = {"سپر"}
ROBBERY_WORDS = {"دستبرد"}

_BUY_ACTIONS = {"خرید", "buy", "فعال"}

_USAGE = (
    "🪓 <b>دستبرد از بانک مرکزی</b>\n"
    "نحوه استفاده: <code>دستبرد [مبلغ]</code>\n"
    "مبلغ مجاز: {min_amount:,} تا {max_amount:,} سکه\n"
    "نیاز: لول {min_level}+ و {energy}⚡ انرژی\n"
    "برد: {win_mult:g}× مبلغ · باخت: جریمه {fine_mult:g}× یا زندان"
)


@router.message(CommandOrText(["shield"], words=SHIELD_WORDS))
async def cmd_shield(message: Message, command: CommandObject | None = None) -> None:
    user = message.from_user
    if user is None:
        return
    try:
        player = await hydrate(user.id, user.full_name, user.username)
        args = extract_args(message, command)
        if args and args[0].casefold() in _BUY_ACTIONS:
            economy.require_ready(
                user.id, security.ACTION_SHIELD, settings.cooldown_shield
            )
            res = await security.buy_shield(player)
        else:
            res = await security.shield_status(player)
        await message.reply(res["message"])
    except Exception as exc:  # noqa: BLE001
        await answer_error(exc, message=message)


@router.message(CommandOrText(["bankraid", "bankrob"], words=ROBBERY_WORDS))
async def cmd_bank_heist(message: Message, command: CommandObject | None = None) -> None:
    user = message.from_user
    if user is None:
        return
    try:
        args = extract_args(message, command)
        if not args or not args[0].isdigit():
            raise GameError(
                _USAGE.format(
                    min_amount=settings.bank_heist_stake_min,
                    max_amount=settings.bank_heist_stake_max,
                    min_level=settings.bank_heist_min_level,
                    energy=settings.bank_heist_energy_cost,
                    win_mult=settings.bank_heist_payout_multiplier,
                    fine_mult=settings.bank_heist_fine_multiplier,
                )
            )
        economy.require_ready(
            user.id, security.ACTION_BANK_HEIST, settings.cooldown_bank_heist
        )
        player = await hydrate(user.id, user.full_name, user.username)
        res = await security.bank_heist(player, int(args[0]))
        await message.reply(res["message"])
    except Exception as exc:  # noqa: BLE001
        await answer_error(exc, message=message)
