"""A shared, observable holder for the persisted :class:`~auto_essay_typer.app_settings.AppSettings`.

Every part of the GUI that reads or changes an application preference (the
Type page's "Typing into" picker, the Settings page, the Advanced page's
shortcut overrides, onboarding, the global-hotkey service) goes through one
:class:`SettingsStore` instead of each calling ``load_settings`` /
``save_settings`` itself. That gives the app a single in-memory truth, a
``changed`` signal so a page showing a setting stays in sync when a
different page changes it, and one place that enforces the rule
"never write a settings file that would not load back".

That rule matters because :func:`~auto_essay_typer.app_settings.load_settings`
validates the hotkeys against the *selected* target's effective keymap and
falls back to defaults plus a warning if they collide. So :meth:`update`
runs the same check on the candidate before saving; a change that would
produce an unloadable file is refused with a ``ValueError`` carrying a
reason that is fit to show next to the control that caused it.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, Signal

from auto_essay_typer import app_settings, platforms
from auto_essay_typer.app_settings import AppSettings

#: Fields a "Reset to defaults" keeps: onboarding progress is not a
#: preference, and keyboard-shortcut overrides have their own per-editor
#: "Restore defaults" on the Advanced page.
_PRESERVED_ON_RESET = ("onboarding_complete", "keymap_overrides")


def validate_settings(candidate: AppSettings) -> None:
    """Raise ``ValueError`` if ``candidate`` could not be saved and loaded back unchanged.

    Mirrors the checks :func:`~auto_essay_typer.app_settings.load_settings`
    applies, so a settings file this app writes is always one it can read.
    """
    try:
        keymap = app_settings.effective_keymap(candidate, platforms.current_os())
        app_settings.validate_hotkeys(candidate.hotkeys, keymap)
    except ValueError:
        raise
    except KeyError as exc:  # an unknown target id
        raise ValueError(str(exc.args[0]) if exc.args else str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - e.g. pynput unavailable on a headless box
        raise ValueError(f"could not check the shortcuts: {exc}") from exc


class SettingsStore(QObject):
    """The app's one live copy of its :class:`AppSettings`.

    ``path`` is where settings are persisted; ``None`` (the default) means
    :func:`~auto_essay_typer.app_settings.settings_path`, which honours the
    ``AUTO_TEXT_TYPER_SETTINGS`` environment override -- the hook the test
    suite uses to keep tests away from the real settings file.

    Signals
    -------
    changed(object)
        The new :class:`AppSettings`, emitted after every change that
        actually altered something (and was saved).
    """

    changed = Signal(object)

    def __init__(self, path: Path | None = None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._path = path
        settings, warning = app_settings.load_settings(path)
        self._settings: AppSettings = settings
        self._load_warning: str | None = warning

    # -- read access -----------------------------------------------------------

    @property
    def settings(self) -> AppSettings:
        return self._settings

    @property
    def path(self) -> Path:
        """The file settings are saved to (resolved now, so env overrides apply)."""
        return self._path if self._path is not None else app_settings.settings_path()

    @property
    def load_warning(self) -> str | None:
        """Why the settings file could not be loaded at startup, or ``None``."""
        return self._load_warning

    def take_load_warning(self) -> str | None:
        """Return the startup load warning once, then forget it."""
        warning, self._load_warning = self._load_warning, None
        return warning

    # -- changing settings -------------------------------------------------------

    def update(self, **changes: Any) -> AppSettings:
        """Apply ``changes`` to the current settings, validate, save, and announce.

        Raises ``ValueError`` (with a user-presentable reason) when the result
        would be invalid or could not be loaded back -- nothing is saved and
        nothing changes. Raises ``OSError`` if the file cannot be written; the
        in-memory settings are likewise left as they were.
        """
        return self.replace(self._candidate(changes))

    def replace(self, settings: AppSettings) -> AppSettings:
        """Make ``settings`` the current settings (validated, saved, announced)."""
        candidate = self._normalized(settings)
        validate_settings(candidate)
        if candidate == self._settings:
            return self._settings
        app_settings.save_settings(candidate, self._path)
        self._settings = candidate
        self.changed.emit(candidate)
        return candidate

    def reset_to_defaults(self) -> AppSettings:
        """Restore every preference to its default (see ``_PRESERVED_ON_RESET``)."""
        keep = {name: getattr(self._settings, name) for name in _PRESERVED_ON_RESET}
        return self.replace(AppSettings(**keep))

    # -- internals ---------------------------------------------------------------

    def _candidate(self, changes: Mapping[str, Any]) -> AppSettings:
        valid = {f.name for f in dataclasses.fields(AppSettings)}
        unknown = sorted(set(changes) - valid)
        if unknown:
            raise ValueError(f"unknown setting(s): {', '.join(unknown)}")
        # dataclasses.replace re-runs AppSettings.__post_init__, so range and
        # theme checks raise their own ValueError here.
        return dataclasses.replace(self._settings, **changes)

    @staticmethod
    def _normalized(settings: AppSettings) -> AppSettings:
        """Copy the mapping fields into plain dicts so callers can't alias our state."""
        return dataclasses.replace(
            settings,
            hotkeys=dict(settings.hotkeys),
            keymap_overrides={
                target: dict(overrides) for target, overrides in settings.keymap_overrides.items()
            },
        )
