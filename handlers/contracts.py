"""قرارداد — daily mission board with automatic reward payout.

Coins *and* EXP are paid inside :func:`services.contracts.sync` (one
transaction, exactly once per window) — the handler must never grant them
again, or every completed contract would double-pay its EXP.
"""

from __future__ import annotations

import logging

from aiogram import Router
from aiogram.filters import CommandObject
from aiogram.types import Message

from handlers.common import CommandOrText, answer_error, hydrate
from services import contracts

logger = logging.getLogger(__name__)
router = Router(name="contracts")

CONTRACT_WORDS = {"قرارداد"}


@router.message(CommandOrText(["contract", "contracts"], words=CONTRACT_WORDS))
async def cmd_contract(message: Message, command: CommandObject | None = None) -> None:
    user = message.from_user
    if user is None:
        return
    try:
        player = await hydrate(user.id, user.full_name, user.username)
        res = await contracts.sync(player.user_id)
        await message.reply(res["message"])
    except Exception as exc:  # noqa: BLE001
        await answer_error(exc, message=message)
