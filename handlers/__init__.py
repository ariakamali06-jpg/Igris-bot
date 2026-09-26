"""Handler package: one router per feature, aggregated here.

Registration order matters for *message* handlers: aiogram dispatches to the
first matching router, so the group-activity middleware (raid counter) is
attached at the dispatcher level in ``main.py`` rather than as a router —
it must see every message, including ones consumed by /me etc.
"""

from __future__ import annotations

from aiogram import Dispatcher

from handlers import duels, economy, profile, raids, shop


def register_routers(dp: Dispatcher) -> None:
    """Attach every feature router to the dispatcher (stable order)."""
    dp.include_router(profile.router)
    dp.include_router(economy.router)
    dp.include_router(shop.router)
    dp.include_router(duels.router)
    dp.include_router(raids.router)


__all__ = ["register_routers", "profile", "economy", "shop", "duels", "raids"]
