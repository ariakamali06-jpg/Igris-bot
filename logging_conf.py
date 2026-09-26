"""Shared logging setup for the bot process."""

from __future__ import annotations

import logging
import logging.config

from config import settings

_FORMAT = "%(asctime)s | %(levelname)-7s | %(name)-26s | %(message)s"


def setup_logging(level: str | None = None) -> None:
    """Configure root logging once; safe to call repeatedly."""
    resolved = (level or settings.log_level).upper()
    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {"default": {"format": _FORMAT, "datefmt": "%H:%M:%S"}},
            "handlers": {
                "console": {
                    "class": "logging.StreamHandler",
                    "formatter": "default",
                    "stream": "ext://sys.stdout",
                }
            },
            "root": {"handlers": ["console"], "level": resolved},
            "loggers": {
                "aiogram": {"level": "INFO", "propagate": True},
                "PIL": {"level": "WARNING", "propagate": True},
            },
        }
    )
