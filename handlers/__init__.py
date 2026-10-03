"""Handler package: one router per feature, aggregated here.

Registration order matters for *message* handlers: aiogram dispatches to the
first matching router, so the group-activity middleware (raid counter) is
attached at the dispatcher level in ``main.py`` rather than as a router —
it must see every message, including ones consumed by /me etc.
"""

from __future__ import annotations

from aiogram import Dispatcher

from handlers import (
    admin,
    bazaar,
    blackmarket,
    casino,
    contracts,
    cup,
    duels,
    economy,
    lottery,
    market,
    onboarding,
    penalty,
    pets,
    profile,
    properties,
    raids,
    risk,
    security,
    shop,
    social,
    underworld,
    xo,
)
from handlers.diagnostics import CallbackDiagnosticsMiddleware
from handlers.gatekeeper import OnboardingGateMiddleware


def register_routers(dp: Dispatcher) -> None:
    """Attach every feature router to the dispatcher (stable order)."""
    dp.include_router(admin.router)
    dp.include_router(onboarding.router)
    dp.include_router(profile.router)
    dp.include_router(economy.router)
    dp.include_router(shop.router)
    dp.include_router(social.router)
    dp.include_router(duels.router)
    dp.include_router(raids.router)
    # Ocean port phase 1 — Persian trigger words never overlap the routers above.
    dp.include_router(properties.router)
    dp.include_router(contracts.router)
    dp.include_router(market.router)
    dp.include_router(security.router)
    dp.include_router(lottery.router)
    # Ocean port phase 2 — arcade money games; words never overlap routers above.
    dp.include_router(casino.router)
    dp.include_router(risk.router)
    dp.include_router(xo.router)
    dp.include_router(penalty.router)
    # Ocean port phase 3 — underworld trades & attacks; own words only.
    dp.include_router(blackmarket.router)
    dp.include_router(bazaar.router)
    dp.include_router(underworld.shadow_router)
    dp.include_router(underworld.extort_router)
    dp.include_router(pets.router)
    dp.include_router(cup.router)

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
    "properties",
    "contracts",
    "market",
    "security",
    "lottery",
    "casino",
    "risk",
    "xo",
    "penalty",
    "blackmarket",
    "bazaar",
    "underworld",
    "pets",
    "cup",
    "CallbackDiagnosticsMiddleware",
]
