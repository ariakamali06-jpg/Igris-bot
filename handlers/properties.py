"""املاک / نیرو — the passive-economy commands (buy, upgrade, collect).

Command words are deliberately distinct from Ocean's ``ملک``/``کارگر`` and
never overlap the forbidden list in OCEAN_PORT.md: ``املاک`` owns the real
estate sub-actions and ``نیرو`` only ever lists/hires crew.
"""

from __future__ import annotations

import logging

from aiogram import Router
from aiogram.filters import CommandObject
from aiogram.types import Message

from config import settings
from handlers.common import CommandOrText, answer_error, extract_args, hydrate
from models import Player
from services import economy, properties
from services.game import GameError

logger = logging.getLogger(__name__)
router = Router(name="properties")

PROPERTIES_WORDS = {"املاک"}
CREW_WORDS = {"نیرو"}

_BUY_ACTIONS = {"خرید", "buy", "purchase"}
_UPGRADE_ACTIONS = {"ارتقا", "ارتقاء", "upgrade", "levelup"}
_COLLECT_ACTIONS = {"وصول", "collect", "برداشت"}

_USAGE = (
    "🏘 <b>دستورات املاک</b>\n"
    "• <code>املاک</code> — فهرست ملک‌های فروشی و درآمد انباشته\n"
    "• <code>املاک خرید [نام]</code> — خرید ملک جدید\n"
    "• <code>املاک ارتقا [نام]</code> — ارتقای لول (+۳۰٪ درآمد)\n"
    "• <code>املاک وصول</code> — جمع‌آوری درآمد انباشته"
)


async def _reply(message: Message, command: CommandObject | None) -> None:
    user = message.from_user
    if user is None:
        return
    try:
        player = await hydrate(user.id, user.full_name, user.username)
        args = extract_args(message, command)
        if not args:
            res = await properties.list_estate(player, properties.KIND_PROPERTY)
        else:
            res = await _run_action(user.id, player, args)
        await message.reply(res["message"])
    except Exception as exc:  # noqa: BLE001
        await answer_error(exc, message=message)


async def _run_action(user_id: int, player: Player, args: list[str]) -> dict:
    action = args[0].casefold()
    name = " ".join(args[1:]).strip()

    if action in _COLLECT_ACTIONS:
        economy.require_ready(
            user_id, properties.ACTION_COLLECT, settings.cooldown_property_collect
        )
        return await properties.collect(player)

    if action in _BUY_ACTIONS:
        economy.require_ready(
            user_id, properties.ACTION_BUY, settings.cooldown_property_buy
        )
        if not name:
            raise GameError(_USAGE)
        return await properties.buy_asset(player, properties.KIND_PROPERTY, name)

    if action in _UPGRADE_ACTIONS:
        economy.require_ready(
            user_id, properties.ACTION_UPGRADE, settings.cooldown_property_upgrade
        )
        if not name:
            raise GameError(_USAGE)
        return await properties.upgrade_property(player, name)

    raise GameError(_USAGE)


@router.message(CommandOrText(["property", "estate"], words=PROPERTIES_WORDS))
async def cmd_properties(message: Message, command: CommandObject | None = None) -> None:
    await _reply(message, command)


@router.message(CommandOrText(["crew", "hire"], words=CREW_WORDS))
async def cmd_crew(message: Message, command: CommandObject | None = None) -> None:
    user = message.from_user
    if user is None:
        return
    try:
        player = await hydrate(user.id, user.full_name, user.username)
        args = extract_args(message, command)
        if not args:
            res = await properties.list_estate(player, properties.KIND_WORKER)
        elif args[0].casefold() in _BUY_ACTIONS:
            economy.require_ready(
                user.id, properties.ACTION_BUY, settings.cooldown_property_buy
            )
            name = " ".join(args[1:]).strip()
            if not name:
                raise GameError(
                    "👥 نام نیروی مورد نظر را بنویسید.\n"
                    "مثال: <code>نیرو خرید نگهبان شب</code>"
                )
            res = await properties.buy_asset(player, properties.KIND_WORKER, name)
        else:
            raise GameError(
                "👥 <b>دستورات نیرو</b>\n"
                "• <code>نیرو</code> — فهرست نیروهای استخدامی و درآمد انباشته\n"
                "• <code>نیرو خرید [نام]</code> — استخدام نیروی جدید"
            )
        await message.reply(res["message"])
    except Exception as exc:  # noqa: BLE001
        await answer_error(exc, message=message)
