"""Settings parsing: the ADMIN_IDS env formats real deployments actually use."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from config import Settings


def test_csv_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ADMIN_IDS", "1,2,3")
    assert Settings(_env_file=None).admin_ids == [1, 2, 3]


def test_single_int_string(monkeypatch: pytest.MonkeyPatch) -> None:
    # .env.example historically shipped this form; it must never crash.
    monkeypatch.setenv("ADMIN_IDS", "123456789")
    assert Settings(_env_file=None).admin_ids == [123456789]


def test_json_array(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ADMIN_IDS", "[7, 8]")
    assert Settings(_env_file=None).admin_ids == [7, 8]


def test_semicolon_separated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ADMIN_IDS", "4;5")
    assert Settings(_env_file=None).admin_ids == [4, 5]


def test_empty_is_no_admins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ADMIN_IDS", "")
    assert Settings(_env_file=None).admin_ids == []


def test_unset_defaults_empty() -> None:
    assert Settings(_env_file=None).admin_ids == []


def test_garbage_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ADMIN_IDS", "not-a-number")
    with pytest.raises((ValidationError, ValueError)):
        Settings(_env_file=None)


def test_is_admin_helper(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ADMIN_IDS", "10,20")
    settings = Settings(_env_file=None)
    assert settings.is_admin(10)
    assert not settings.is_admin(11)
