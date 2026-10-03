"""حیوان — pet shop, upgrades and the reply-based street battle."""

from __future__ import annotations

import logging

from aiogram import Router
from aiogram.filters import CommandObject
from aiogram.types import Message

from config import settings
from handlers.common import CommandOrText, answer_error, extract_args, hydrate
from models import Player
from services import economy, pets
from services.game import GameError

logger = logging.getLogger(__name__)
router = Router(name="pets")

PET_WORDS = {"حیوان"}

_BUY_ACTIONS = {"خرید", "buy"}
_UPGRADE_ACTIONS = {"ارتقا", "ارتقاء", "upgrade"}
_BATTLE_ACTIONS = {"نبرد", "battle", "fight", "فایت"}
_DECLINE_ACTIONS = {"رد", "decline", "نه"}
_CANCEL_ACTIONS = {"لغو", "cancel"}

_USAGE = (
    "🐺 <b>حیوان خیابان</b>\n"
    "• <code>حیوان</code> — وضعیت و قوانین\n"
    "• <code>حیوان خرید</code> — خرید اولین حیوان\n"
    "• <code>حیوان ارتقا</code> — یک لول بالاتر\n"
    "• <code>حیوان نبرد [مبلغ]</code> — چالش (ریپلای حریف)\n"
    "• <code>حیوان نبرد</code> — قبول چالش (ریپلای چالش)\n"
    "• <code>حیوان نبرد رد</code> / <code>حیوان نبرد لغو</code>"
)


async def _reply_target(message: Message) -> Player:
    reply = message.reply_to_message
    target = reply.from_user if reply else None
    if target is None:
        raise GameError("روی پیام طرف ریپلای کن.")
    return await hydrate(target.id, target.full_name, target.username)


@router.message(CommandOrText(["pet"], words=PET_WORDS))
async def cmd_pet(message: Message, command: CommandObject | None = None) -> None:
    user = message.from_user
    if user is None:
        return
    try:
        player = await hydrate(user.id, user.full_name, user.username)
        args = extract_args(message, command)
        res = await _dispatch(message, player, args)
        await message.reply(res["message"])
    except Exception as exc:  # noqa: BLE001
        await answer_error(exc, message=message)


async def _dispatch(message: Message, player: Player, args: list[str]) -> dict:
    if not args:
        return await pets.status(player)

    action = args[0].casefold()
    if action in _BUY_ACTIONS:
        economy.require_ready(player.user_id, "pet_buy", settings.cooldown_pet)
        return await pets.buy(player)
    if action in _UPGRADE_ACTIONS:
        economy.require_ready(
            player.user_id, "pet_upgrade", settings.cooldown_pet
        )
        return await pets.upgrade(player)
    if action in _BATTLE_ACTIONS:
        return await _battle(message, player, args[1:])
    raise GameError(_USAGE)


async def _battle(message: Message, player: Player, rest: list[str]) -> dict:
    if rest:
        sub = rest[0].casefold()
        if sub in _DECLINE_ACTIONS:
            challenger = await _reply_target(message)
            return await pets.decline(player, challenger)
        if sub in _CANCEL_ACTIONS:
            target = await _reply_target(message)
            return await pets.cancel(player, target)
        if not rest[0].isdigit():
            raise GameError(_USAGE)
        economy.require_ready(
            player.user_id, "pet_challenge", settings.cooldown_pet_battle
        )
        target = await _reply_target(message)
        chat_id = message.chat.id if message.chat else 0
        return await pets.challenge(player, target, int(rest[0]), chat_id)

    challenger = await _reply_target(message)
    return await pets.accept(player, challenger)
