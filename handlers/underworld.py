"""هندلرهای دنیای زیرزمینی، قراردادهای سایه و اخاذی خیابانی شهر تیرامیکس.

شامل:
- سایه هکر (دزدی ناشناس از کیف پول قربانی)
- سایه قاتل (ارسال قربانی به بازداشتگاه و حبس)
- اخاذی خیابانی (زورگیری درصدی از موجودی با ریسک دستگیری)
"""

from __future__ import annotations

import logging

from aiogram import Router
from aiogram.filters import CommandObject
from aiogram.types import Message

from handlers.common import (
    CommandOrText,
    answer_error,
    extract_args,
    hydrate,
    resolve_target_player,
)
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
    "🕳 <b>سایه — بازار سیاه جرایم سفارشی تیرامیکس</b>\n\n"
    "• <code>سایه هکر</code> — سرقت ناشناس درصدی از موجودی قربانی (ریپلای یا آیدی)\n"
    "• <code>سایه قاتل</code> — به حبس انداختن موقت قربانی در بازداشتگاه شهر\n\n"
    "🛡 <i>نکته: آیتم نگهبان شخصی و سپر ضدسرقت هر دو این حملات را خنثی می‌کنند!</i>"
)

_EXTORT_USAGE = (
    "😠 <b>اخاذی و زورگیری خیابانی</b>\n\n"
    "روی پیام شخص ریپلای بزنید یا آیدی او را بنویسید: <code>اخاذی</code>\n"
    "💰 در صورت موفقیت: سرقت درصدی از پول نقد قربانی\n"
    "⚖️ در صورت شکست: پرداخت غرامت نقدی و حبس فوری در بازداشتگاه پلیس"
)


async def _resolve_victim(message: Message, token: str = "") -> Player:
    """شناسایی قربانی عملیات زیرزمینی بر اساس ریپلای پیام، آیدی عددی یا یوزرنیم."""
    target = await resolve_target_player(message, token)
    if target is None:
        raise GameError("روی پیام هدف ریپلای کنید یا آیدی/یوزرنیم او را مشخص فرمایید.")
    return target


@shadow_router.message(CommandOrText(["shadow"], words=SHADOW_WORDS))
async def cmd_shadow(message: Message, command: CommandObject | None = None) -> None:
    """اجرای عملیات محرمانه سایه (هکر حساب یا قاتل اجاره‌ای)."""
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
        target_token = args[1] if len(args) > 1 else ""
        victim = await _resolve_victim(message, target_token)
        player = await hydrate(user.id, user.full_name, user.username)

        # اجرای عملیات بسته به نوع خدمت انتخاب‌شده
        if kind in _HACK_KINDS:
            res = await underworld.shadow_hack(player, victim)
        else:
            res = await underworld.shadow_kill(player, victim)
        await message.reply(res["message"])
    except Exception as exc:  # noqa: BLE001
        await answer_error(exc, message=message)


@extort_router.message(CommandOrText(["extort"], words=EXTORT_WORDS))
async def cmd_extort(message: Message, command: CommandObject | None = None) -> None:
    """اقدام به اخاذی خیابانی از شهروندان با محاسبه شانس مبتنی بر لول طرفین."""
    user = message.from_user
    if user is None:
        return
    try:
        args = extract_args(message, command)
        target_token = ""
        if args:
            if args[0].casefold() in {"extort", "اخاذی"}:
                target_token = args[1] if len(args) > 1 else ""
            else:
                target_token = args[0]
        victim = await _resolve_victim(message, target_token)
        player = await hydrate(user.id, user.full_name, user.username)
        res = await underworld.extort(player, victim)
        await message.reply(res["message"])
    except Exception as exc:  # noqa: BLE001
        await answer_error(exc, message=message)
