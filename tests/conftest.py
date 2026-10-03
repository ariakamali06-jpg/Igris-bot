"""Pytest bootstrap: pin the database BEFORE any test module imports config.

``pytest`` imports conftest first, then the test modules alphabetically.
``tests/test_admin.py`` imports ``database.connection`` (→ ``config``) at
module scope, and ``get_settings()`` is an ``lru_cache`` — so whichever value
``DB_PATH`` holds at that first import decides where the whole suite reads
and writes.  ``setdefault`` calls inside individual test modules arrive too
late and used to be silently ignored, which made every test run against the
real ``data/game.db`` and left state behind for the next run.

Setting it here keeps the suite on a throwaway file: reruns stay green and
the game database is never touched by tests.
"""

import os
import tempfile
from pathlib import Path

os.environ.setdefault(
    "DB_PATH", str(Path(tempfile.mkdtemp(prefix="tgbot-pytest-")) / "test.db")
)
