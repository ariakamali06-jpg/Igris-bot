"""قرعه / هدیه — lottery tickets and gift codes (admin-authored)."""

from __future__ import annotations

import logging

from aiogram import Router
from aiogram.filters import CommandObject
from aiogram.types import Message

from config import settings
from handlers.common import (
    CommandOrText,
    answer_error,
    extract_args,
    hydrate,
    is_admin_or_owner,
)
from services import economy, rewards
from services.game import GameError

logger = logging.getLogger(__name__)
router = Router(name="lottery")

LOTTERY_WORDS = {"قرعه"}
GIFT_WORDS = {"هدیه"}

_BUY_ACTIONS = {"خرید", "buy"}
_CREATE_ACTIONS = {"ساخت", "create", "make"}

_GIFT_USAGE = (
    "🎁 <b>کدهای هدیه شهر تیرامیکس</b>\n"
    "• <code>هدیه [کد]</code> — دریافت اعتبار هدیه "
    "(هر کد برای هر نفر یک‌بار)\n"
    "• <code>هدیه ساخت [کد] [مبلغ] [ظرفیت]</code> — ساخت کد جدید (فقط ادمین)"
)


@router.message(CommandOrText(["lottery", "raffle"], words=LOTTERY_WORDS))
async def cmd_lottery(message: Message, command: CommandObject | None = None) -> None:
    user = message.from_user
    if user is None:
        return
    try:
        player = await hydrate(user.id, user.full_name, user.username)
        args = extract_args(message, command)
        if args and args[0].casefold() in _BUY_ACTIONS:
            economy.require_ready(
                user.id, rewards.ACTION_LOTTERY, settings.cooldown_lottery
            )
            res = await rewards.buy_ticket(player)
        else:
            res = await rewards.status(player)
        await message.reply(res["message"])
    except Exception as exc:  # noqa: BLE001
        await answer_error(exc, message=message)


@router.message(CommandOrText(["gift"], words=GIFT_WORDS))
async def cmd_gift(message: Message, command: CommandObject | None = None) -> None:
    user = message.from_user
    if user is None:
        return
    try:
        args = extract_args(message, command)
        if not args:
            raise GameError(_GIFT_USAGE)

        if args[0].casefold() in _CREATE_ACTIONS:
            if not is_admin_or_owner(user.id):
                await message.reply(
                    "⛔ ساخت کد هدیه فقط برای ادمین‌های شهر مجاز است."
                )
                return
            if len(args) < 4 or not args[2].isdigit() or not args[3].isdigit():
                raise GameError(_GIFT_USAGE)
            res = await rewards.create_code(
                args[1], int(args[2]), int(args[3]), user.id
            )
        else:
            economy.require_ready(
                user.id, rewards.ACTION_GIFT, settings.cooldown_gift
            )
            player = await hydrate(user.id, user.full_name, user.username)
            res = await rewards.redeem(player, args[0])

        await message.reply(res["message"])
    except Exception as exc:  # noqa: BLE001
        await answer_error(exc, message=message)
