"""سایه / اخاذی — reply-based attacks against another player."""

from __future__ import annotations

import logging

from aiogram import Router
from aiogram.filters import CommandObject
from aiogram.types import Message

from handlers.common import CommandOrText, answer_error, extract_args, hydrate
from models import Player
from services import underworld
from services.game import GameError

logger = logging.getLogger(__name__)

shadow_router = Router(name="underworld")
extort_router = Router(name="extortion")

SHADOW_WORDS = {"سایه"}
EXTORT_WORDS = {"اخاذی"}

_HACK_KINDS = {"هکر", "hacker", "hack"}
_KILL_KINDS = {"قاتل", "killer", "kill"}

_SHADOW_USAGE = (
    "🕳 <b>سایه — خرید جرم سفارشی</b>\n"
    "• <code>سایه هکر</code> — دزدی از حساب طرف (ریپلای لازمه)\n"
    "• <code>سایه قاتل</code> — زندانی کردن طرف (ریپلای لازمه)\n"
    "نگهبان شخصی و سپر ضدسرقت هر دو رو خنثی می‌کنن."
)

_EXTORT_USAGE = (
    "😠 <b>اخاذی</b>\n"
    "روی پیام طرف ریپلای کن: <code>اخاذی</code>\n"
    "برد: درصدی از کیف طرف · باخت: غرامت + زندان کوتاه"
)


async def _reply_target(message: Message) -> Player:
    reply = message.reply_to_message
    target = reply.from_user if reply else None
    if target is None:
        raise GameError("روی پیام طرف ریپلای کن تا کار رو انجام بدم.")
    return await hydrate(target.id, target.full_name, target.username)


@shadow_router.message(CommandOrText(["shadow"], words=SHADOW_WORDS))
async def cmd_shadow(message: Message, command: CommandObject | None = None) -> None:
    user = message.from_user
    if user is None:
        return
    try:
        args = extract_args(message, command)
        if not args:
            raise GameError(_SHADOW_USAGE)
        kind = args[0].casefold()
        if kind not in _HACK_KINDS and kind not in _KILL_KINDS:
            raise GameError(_SHADOW_USAGE)
        victim = await _reply_target(message)
        player = await hydrate(user.id, user.full_name, user.username)
        if kind in _HACK_KINDS:
            res = await underworld.shadow_hack(player, victim)
        else:
            res = await underworld.shadow_kill(player, victim)
        await message.reply(res["message"])
    except Exception as exc:  # noqa: BLE001
        await answer_error(exc, message=message)


@extort_router.message(CommandOrText(["extort"], words=EXTORT_WORDS))
async def cmd_extort(message: Message, command: CommandObject | None = None) -> None:
    user = message.from_user
    if user is None:
        return
    try:
        args = extract_args(message, command)
        if args and args[0].casefold() not in {"extort", "اخاذی"}:
            raise GameError(_EXTORT_USAGE)
        victim = await _reply_target(message)
        player = await hydrate(user.id, user.full_name, user.username)
        res = await underworld.extort(player, victim)
        await message.reply(res["message"])
    except Exception as exc:  # noqa: BLE001
        await answer_error(exc, message=message)
