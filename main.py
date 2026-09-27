"""Entrypoint: boot the asset pipeline, database and aiogram dispatcher.

Startup order matters:
1. ``ensure_assets``  — generate placeholder art if missing (idempotent).
2. ``load_library``   — preload every layer PNG once so renders never hit disk.
3. ``db.connect``     — open SQLite, apply pragmas, install schema.
4. ``seed_catalog``   — mirror ``database/items.py`` into the items table.
5. Dispatcher wiring  — routers, then the group-activity middleware.

Shutdown flushes the compositor's background cache writer and closes both DB
connections, so a restart never leaves a half-written composite or WAL file.
"""

from __future__ import annotations

import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramConflictError

from config import settings
from database.connection import db
from database.seed import seed_catalog
from handlers import register_routers
from handlers.raids import GroupActivityMiddleware
from logging_conf import setup_logging
from services.assetgen import ensure_assets
from services.compositor import compositor, load_library
from services.economy import clear_cooldowns

import os

logger = logging.getLogger("main")


async def _start_health_server() -> asyncio.Server | None:
    """Tiny HTTP server so Railway / Cloud deployment health checks pass."""
    port_str = os.environ.get("PORT")
    if not port_str:
        return None
    try:
        port = int(port_str)
    except ValueError:
        return None

    async def _handle_http(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            await reader.read(1024)
            resp = b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Length: 2\r\nConnection: close\r\n\r\nOK"
            writer.write(resp)
            await writer.drain()
        except Exception:
            pass
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    try:
        server = await asyncio.start_server(_handle_http, "0.0.0.0", port)
        logger.info("Health check server listening on 0.0.0.0:%d", port)
        return server
    except Exception as exc:
        logger.warning("Could not start health check server on port %d: %s", port, exc)
        return None


async def on_startup(bot: Bot) -> None:
    """Everything that must be warm before the first update arrives."""
    written = await asyncio.to_thread(ensure_assets)
    if written:
        logger.info("generated %d missing asset(s)", len(written))
    await asyncio.to_thread(load_library)

    await db.connect()
    await seed_catalog()
    clear_cooldowns()

    if settings.dry_run:
        logger.info("DRY_RUN=1 — boot checks passed, skipping get_me()")
        return
    me = await bot.get_me()
    logger.info("bot online as @%s", me.username)


async def on_shutdown() -> None:
    await db.close()
    # Blocks until pending composite disk writes land (bounded, small queue).
    await asyncio.to_thread(compositor.close)
    logger.info("shutdown complete")


async def run() -> None:
    setup_logging()

    if not settings.bot_token or settings.bot_token.startswith("123456"):
        logger.critical(
            "BOT_TOKEN is missing or still the .env.example placeholder. "
            "Copy .env.example to .env and set a real token from @BotFather."
        )
        sys.exit(2)

    if settings.dry_run:
        # Boot everything except the network loop: useful for smoke tests.
        bot = Bot(
            token=settings.bot_token,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        )
        try:
            await on_startup(bot)
            logger.info("DRY_RUN=1 — boot checks passed, not polling Telegram")
        finally:
            await on_shutdown()
            await bot.session.close()
        return

    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher()

    register_routers(dp)
    # Outer middleware on the *message* observer: counts group messages for
    # raid spawns before any router claims the update, so /me and commands
    # still tick the counter. (Registered on `message` — not `update` — so
    # the event is a Message with .chat/.from_user already resolved.)
    dp.message.outer_middleware(GroupActivityMiddleware())

    health_server = await _start_health_server()
    try:
        await on_startup(bot)
        for attempt in range(1, 11):
            try:
                await dp.start_polling(
                    bot,
                    allowed_updates=dp.resolve_used_update_types(),
                    handle_signals=True,
                )
                break
            except TelegramConflictError:
                logger.warning(
                    "TelegramConflictError: another bot instance is still active/shutting down. "
                    "Waiting 3 seconds before retry (attempt %d/10)...",
                    attempt,
                )
                await asyncio.sleep(3)
    finally:
        if health_server is not None:
            health_server.close()
            await health_server.wait_closed()
        await on_shutdown()
        await bot.session.close()


def main() -> None:
    try:
        asyncio.run(run())
    except (KeyboardInterrupt, SystemExit):
        logger.info("interrupted")


if __name__ == "__main__":
    main()
