"""Handler package: one router per feature, aggregated here.

Registration order matters for *message* handlers: aiogram dispatches to the
first matching router, so the group-activity middleware (raid counter) is
attached at the dispatcher level in ``main.py`` rather than as a router —
it must see every message, including ones consumed by /me etc.
"""

from __future__ import annotations

from aiogram import Dispatcher

from handlers import duels, economy, onboarding, profile, raids, shop, social
from handlers.diagnostics import CallbackDiagnosticsMiddleware
from handlers.gatekeeper import OnboardingGateMiddleware


def register_routers(dp: Dispatcher) -> None:
    """Attach every feature router to the dispatcher (stable order)."""
    dp.include_router(onboarding.router)
    dp.include_router(profile.router)
    dp.include_router(economy.router)
    dp.include_router(shop.router)
    dp.include_router(social.router)
    dp.include_router(duels.router)
    dp.include_router(raids.router)

    # Gatekeeper: intercept group commands when user has not yet created a character
    dp.message.middleware(OnboardingGateMiddleware())
    dp.callback_query.middleware(OnboardingGateMiddleware())

    # Innermost on callback_query: sees the payload and any handler failure,
    # so a dead button is traceable in the logs instead of vanishing.
    dp.callback_query.middleware(CallbackDiagnosticsMiddleware())


__all__ = [
    "register_routers",
    "OnboardingGateMiddleware",
    "profile",
    "economy",
    "shop",
    "duels",
    "raids",
    "CallbackDiagnosticsMiddleware",
]
