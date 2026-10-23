"""Shared fixtures for the Qt GUI test suite.

``pytest-qt`` supplies the session-scoped ``qapp`` fixture (a real
``QApplication``, safe under ``QT_QPA_PLATFORM=offscreen``) and the
function-scoped ``qtbot`` fixture used throughout ``tests/gui/`` to wait on
signals instead of sleeping. This file only adds what's specific to this
project: an isolated, temp-dir profile config for every test (so the real
``~/Library/Application Support/Auto Text Typer`` is never read or written),
and a couple of small builders shared across test modules.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(autouse=True)
def _isolated_config(tmp_path, monkeypatch):
    """Redirect the profiles config file to an isolated temp path for every test."""
    monkeypatch.setenv("AUTO_TEXT_TYPER_CONFIG", str(tmp_path / "profiles.toml"))


@pytest.fixture
def recording_driver_factory():
    """A ``driver_factory`` for :class:`~auto_essay_typer.gui.run_controller.RunController`
    that builds a real ``Driver`` around a ``RecordingKeyboard``, with no real
    sleeping and no countdown -- fast, deterministic, and it never touches
    the keyboard. Returns ``(factory, keyboards)`` where ``keyboards`` collects
    every ``RecordingKeyboard`` built, so a test can inspect what was "typed".
    """
    from auto_essay_typer.driver import Driver, RecordingKeyboard

    keyboards: list[RecordingKeyboard] = []

    def factory() -> Driver:
        keyboard = RecordingKeyboard()
        keyboards.append(keyboard)
        return Driver(keyboard=keyboard, sleep=lambda _seconds: None, countdown_seconds=0)

    return factory, keyboards
