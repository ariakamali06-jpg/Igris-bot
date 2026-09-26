"""Diagnostic middleware: log every callback the bot actually receives.

When an inline button "does nothing", the first question is always the same:
did the tap even reach the process?  A silent no-op has three possible causes
and they need different fixes:

1. the callback never arrived (client-side issue, wrong bot, stale message)
2. the callback arrived and the handler raised (fixed by visible errors)
3. the callback arrived, the edit was refused by Telegram, and the refusal was
   swallowed (the original bug)

Without this log, cases 1 and 3 look identical from the chat.  Every callback
is logged at INFO with its payload, plus a one-line outcome on the way out, so
one Railway log read settles it.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from time import monotonic
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, TelegramObject

logger = logging.getLogger("handlers.diagnostics")

# Callbacks that legitimately do nothing, so their silence is not a bug.
_EXPECTED_QUIET = {"inv:noop", "shop:noop"}


class CallbackDiagnosticsMiddleware(BaseMiddleware):
    """Log inbound callbacks and, on failure, what went wrong."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: CallbackQuery,
        data: dict[str, Any],
    ) -> Any:
        payload = event.data or "<none>"
        user = event.from_user.id if event.from_user else "?"
        has_message = event.message is not None
        message_type = type(event.message).__name__ if has_message else "None"

        logger.info(
            "callback in: data=%r user=%s message=%s (editable=%s)",
            payload, user, message_type, has_message,
        )

        started = monotonic()
        try:
            result = await handler(event, data)
        except Exception as exc:  # noqa: BLE001 - this is the diagnostic point
            logger.error(
                "callback FAILED: data=%r after %.0fms: %s: %s",
                payload, (monotonic() - started) * 1000,
                type(exc).__name__, exc,
                exc_info=True,
            )
            raise

        elapsed = (monotonic() - started) * 1000
        if payload in _EXPECTED_QUIET:
            logger.info("callback done (no-op): data=%r %.0fms", payload, elapsed)
        else:
            logger.info("callback done: data=%r %.0fms", payload, elapsed)
        return result
