"""هندلرهای نگهداری، ارتقا و نبردهای خیابانی حیوانات خانگی (Pets) در تیرامیکس.

شامل:
- خرید و به سرپرستی گرفتن اولین حیوان
- ارتقای سطح و لول آپ حیوان خانگی برای افزایش قدرت مبارزه
- چالش نبرد و شرط‌بندی خیابانی با حیوان دیگر بازیکنان
- پذیرش، رد یا لغو چالش نبرد
"""

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
    resolve_target_player,
)
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
    "🐺 <b>سیستم حیوانات خیابانی شهر تیرامیکس</b>\n\n"
    "• <code>حیوان</code> — مشاهده وضعیت، لول و قدرت حیوان فعلی\n"
    "• <code>حیوان خرید</code> — خرید و نگهداری اولین حیوان\n"
    "• <code>حیوان ارتقا</code> — ارتقای سطح حیوان برای افزایش قدرت مبارزه\n"
    "• <code>حیوان نبرد [مبلغ]</code> — دعوت به چالش نبرد (در ریپلای به حریف)\n"
    "• <code>حیوان نبرد</code> — پذیرش چالش ارسال‌شده (در ریپلای به پیام چالش)\n"
    "• <code>حیوان نبرد رد</code> / <code>حیوان نبرد لغو</code> — رد یا پس‌گرفتن چالش"
)


async def _reply_target(message: Message, token: str = "") -> Player:
    """شناسایی حریف مبارزه حیوانات از طریق ریپلای، آیدی یا یوزرنیم."""
    target = await resolve_target_player(message, token)
    if target is None:
        raise GameError("روی پیام حریف ریپلای کنید یا آیدی/یوزرنیم او را بنویسید.")
    return target


@router.message(CommandOrText(["pet"], words=PET_WORDS))
async def cmd_pet(message: Message, command: CommandObject | None = None) -> None:
    """مدیریت فرامین و تعاملات مربوط به حیوان خانگی بازیکن."""
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
    """هدایت دستور حیوان به متدهای متناسب در سرویس بازی."""
    if not args:
        return await pets.status(player)

    action = args[0].casefold()
    if action in _BUY_ACTIONS:
        economy.require_ready(player.user_id, "pet_buy", settings.cooldown_pet)
        return await pets.buy(player)
    if action in _UPGRADE_ACTIONS:
        economy.require_ready(player.user_id, "pet_upgrade", settings.cooldown_pet)
        return await pets.upgrade(player)
    if action in _BATTLE_ACTIONS:
        return await _battle(message, player, args[1:])
    raise GameError(_USAGE)


async def _battle(message: Message, player: Player, rest: list[str]) -> dict:
    """پردازش مراحل چالش، رد، لغو یا پذیرش نبرد خیابانی حیوانات."""
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
