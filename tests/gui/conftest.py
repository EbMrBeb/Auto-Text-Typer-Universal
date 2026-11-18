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

import dataclasses
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


@pytest.fixture(autouse=True)
def _no_real_permission_prompt(monkeypatch):
    """Keep a test from making macOS show its own "allow Accessibility access" prompt.

    On macOS, opening a ``PermissionDialog`` also asks the OS to prompt for that access
    (``platforms.request_input_permission_prompt``). A test that opens one without injecting its
    own ``request_prompt`` would put a real system dialog on the developer's screen -- from a
    process that is not even Auto Text Typer. This makes the default a no-op for every GUI test;
    the tests that care about the call inject their own and check it.
    """
    from auto_essay_typer import platforms

    monkeypatch.setattr(platforms, "request_input_permission_prompt", lambda: False)


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


# -- a Type page wired to fakes ---------------------------------------------------------------------------


@dataclasses.dataclass
class PageRig:
    """A :class:`TypePage` with every side effect replaced by something a test can inspect.

    ``keyboards`` and ``factory`` come from ``recording_driver_factory`` (what was
    "typed", and which keymap each run's driver was built with); ``errors`` collects
    the ``(title, message)`` of every import-failure message box that would have been
    shown; ``chosen`` records the start folder of every Browse dialog and ``store`` is
    the settings store the page reads and saves its editor choice in.
    """

    page: object
    controller: object
    keyboards: list
    factory: object
    errors: list
    chosen: list
    store: object


@pytest.fixture
def type_page_rig(qtbot, qapp, recording_driver_factory, tmp_path):
    """``build(...)`` -> :class:`PageRig`: a Type page that never touches the network, a
    file dialog, a modal message box, the real keyboard or the real settings file."""
    from auto_essay_typer.gui.pages.type_page import TypePage
    from auto_essay_typer.gui.run_controller import RunController
    from auto_essay_typer.gui.settings_store import SettingsStore
    from auto_essay_typer.gui.theme import ThemeManager
    from auto_essay_typer.importers import load_document
    from auto_essay_typer.platforms import PermissionStatus

    granted = PermissionStatus(granted=True, summary="ok", fix_steps=(), can_open_settings=False)

    def build(
        *,
        loader=load_document,
        file_to_choose=None,
        driver_factory=None,
        permission_checker=lambda: granted,
        store=None,
    ) -> PageRig:
        factory, keyboards = recording_driver_factory
        controller = RunController(driver_factory=driver_factory or factory)
        errors: list[tuple[str, str]] = []
        chosen: list[str] = []

        def choose_file(parent, start_dir):
            chosen.append(start_dir)
            return file_to_choose

        settings_store = store if store is not None else SettingsStore(tmp_path / "settings.json")
        page = TypePage(
            controller,
            ThemeManager(qapp),
            permission_checker=permission_checker,
            settings_store=settings_store,
            document_loader=loader,
            file_chooser=choose_file,
            error_presenter=lambda parent, title, message: errors.append((title, message)),
        )
        qtbot.addWidget(page)
        return PageRig(page, controller, keyboards, factory, errors, chosen, settings_store)

    return build
