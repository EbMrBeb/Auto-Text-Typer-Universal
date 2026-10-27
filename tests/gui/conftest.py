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
    """Redirect the profiles config and the app settings to isolated temp paths for every test.

    Both the typing-profile file (``AUTO_TEXT_TYPER_CONFIG``) and the app
    preferences file (``AUTO_TEXT_TYPER_SETTINGS``) are pointed inside
    ``tmp_path`` so no GUI test can read or write the real files.
    """
    monkeypatch.setenv("AUTO_TEXT_TYPER_CONFIG", str(tmp_path / "profiles.toml"))
    monkeypatch.setenv("AUTO_TEXT_TYPER_SETTINGS", str(tmp_path / "settings.json"))


@pytest.fixture
def recording_driver_factory():
    """A ``driver_factory`` for :class:`~auto_essay_typer.gui.run_controller.RunController`
    that builds a real ``Driver`` around a ``RecordingKeyboard``, with no real
    sleeping and no countdown -- fast, deterministic, and it never touches
    the keyboard. Returns ``(factory, keyboards)`` where ``keyboards`` collects
    every ``RecordingKeyboard`` built, so a test can inspect what was "typed".

    The factory accepts the optional ``keymap`` keyword ``RunController`` passes
    when it types a formatted document, and records each keymap it was given in
    ``factory.keymaps`` -- so a test can check which editor's shortcuts a run
    was built for.
    """
    from auto_essay_typer.driver import Driver, RecordingKeyboard

    keyboards: list[RecordingKeyboard] = []

    def factory(keymap=None) -> Driver:
        keyboard = RecordingKeyboard()
        keyboards.append(keyboard)
        factory.keymaps.append(keymap)
        return Driver(
            keyboard=keyboard,
            sleep=lambda _seconds: None,
            countdown_seconds=0,
            keymap=keymap,
        )

    factory.keymaps = []
    return factory, keyboards
