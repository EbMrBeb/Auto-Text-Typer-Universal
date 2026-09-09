"""Thin macOS menu-bar shell, built on ``rumps``.

All state and decision-making lives in :class:`~auto_essay_typer.menubar_controller.MenubarController`;
this module only turns that state into a menu-bar title and menu-item labels, and
turns menu clicks into calls on the controller. See ``menubar_controller.py``'s
module docstring for why the split exists.

``rumps`` (and the PyObjC frameworks it wraps) is an *optional* dependency --
see the ``menubar`` extra in ``pyproject.toml`` -- so the import is guarded
right here at module level. On a machine that never installed the extra,
``import auto_essay_typer.menubar`` fails with a clear, actionable message
instead of a bare ``ModuleNotFoundError`` traceback. Every other module in
this package stays importable, and the whole test suite runs, with no
``rumps`` installed at all.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

try:
    import rumps
except ImportError as _import_error:  # pragma: no cover - exercised only without the optional extra
    raise RuntimeError(
        "The macOS menu-bar mode needs the 'rumps' package, which is not installed.\n\n"
        "Install it with:\n"
        "    pip install 'auto-essay-typer[menubar]'\n\n"
        "or directly:\n"
        "    pip install rumps"
    ) from _import_error

from auto_essay_typer import config
from auto_essay_typer.driver import Driver
from auto_essay_typer.menubar_controller import MenubarController

#: How often the @rumps.timer polls the controller and redraws the title/menu labels.
_REFRESH_SECONDS = 0.5

#: States in which "Pause/Resume" and "Abort" are clickable rather than greyed out.
_PAUSEABLE_STATES = frozenset({"typing", "paused"})
_ABORTABLE_STATES = frozenset({"counting_down", "typing", "paused"})


def _read_clipboard() -> str:
    """Read the system clipboard via ``pbpaste`` -- the macOS-native way, no extra dependency."""
    result = subprocess.run(["pbpaste"], capture_output=True, text=True, check=True)
    return result.stdout


def _make_driver() -> Driver:
    """Build a real, keystroke-injecting Driver (PynputKeyboard, real countdown)."""
    return Driver()


def _choose_file() -> str | None:
    """Prompt for a file via a native NSOpenPanel. Returns its path, or None if cancelled."""
    from AppKit import NSModalResponseOK, NSOpenPanel

    panel = NSOpenPanel.openPanel()
    panel.setCanChooseFiles_(True)
    panel.setCanChooseDirectories_(False)
    panel.setAllowsMultipleSelection_(False)
    panel.setTitle_("Choose a text file to type")
    if panel.runModal() != NSModalResponseOK:
        return None
    urls = panel.URLs()
    if not urls:
        return None
    return str(urls[0].path())


class MenuBarApp:
    """Owns the rumps.App and its menu; owns no typing state of its own.

    Every action just forwards to ``self.controller``; every redraw just
    reads back from it. If this class starts accumulating its own "is it
    running" bookkeeping, that logic belongs in ``MenubarController`` instead.
    """

    def __init__(self) -> None:
        self.controller = MenubarController(
            read_clipboard=_read_clipboard,
            driver_factory=_make_driver,
        )

        self.app = rumps.App("Auto Essay Typer", title=self.controller.title(), quit_button=None)

        self.type_clipboard_item = rumps.MenuItem(
            "Type from Clipboard", callback=self._on_type_clipboard
        )
        self.type_file_item = rumps.MenuItem("Type from File…", callback=self._on_type_file)
        self.profile_menu = rumps.MenuItem("Profile")
        self.pause_item = rumps.MenuItem("Pause", callback=self._on_toggle_pause)
        self.abort_item = rumps.MenuItem("Abort", callback=self._on_abort)
        self.open_window_item = rumps.MenuItem("Open Full Window…", callback=self._on_open_window)
        self.quit_item = rumps.MenuItem("Quit", callback=self._on_quit)

        self.app.menu = [
            self.type_clipboard_item,
            self.type_file_item,
            self.profile_menu,
            None,  # separator
            self.pause_item,
            self.abort_item,
            None,  # separator
            self.open_window_item,
            None,  # separator
            self.quit_item,
        ]

        self._profile_items: dict[str, rumps.MenuItem] = {}
        self._build_profile_menu()
        self._refresh_labels(None)

        rumps.timer(_REFRESH_SECONDS)(self._refresh_labels)

    # -- menu actions: each one just forwards to the controller ---------------

    def _on_type_clipboard(self, _sender: object) -> None:
        self.controller.start_from_clipboard()

    def _on_type_file(self, _sender: object) -> None:
        path = _choose_file()
        if path is None:
            return
        try:
            text = Path(path).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as error:
            rumps.alert(title="Could not read file", message=str(error))
            return
        self.controller.start_from_text(text)

    def _on_profile_selected(self, sender: "rumps.MenuItem") -> None:
        self.controller.set_profile(sender.title)
        self._refresh_profile_checkmarks()

    def _on_toggle_pause(self, _sender: object) -> None:
        self.controller.toggle_pause()

    def _on_abort(self, _sender: object) -> None:
        self.controller.abort()

    def _on_open_window(self, _sender: object) -> None:
        """Launch the Tk GUI in a separate process.

        Not in-process: Tk and rumps both want the main thread and would
        deadlock if combined into one process.
        """
        subprocess.Popen([sys.executable, "-m", "auto_essay_typer.app"])

    def _on_quit(self, _sender: object) -> None:
        self.controller.abort()
        rumps.quit_application()

    # -- rendering: read the controller, write the menu ------------------------

    def _build_profile_menu(self) -> None:
        if list(self.profile_menu.keys()):
            # MenuItem.clear() dereferences its NSMenu unconditionally, which
            # is only ever created lazily on the first item added -- calling
            # it on a still-empty submenu (the very first build) raises.
            self.profile_menu.clear()
        self._profile_items = {}
        for name in sorted(config.builtin_and_user_profiles()):
            item = rumps.MenuItem(name, callback=self._on_profile_selected)
            self.profile_menu.add(item)
            self._profile_items[name] = item
        self._refresh_profile_checkmarks()

    def _refresh_profile_checkmarks(self) -> None:
        active = self.controller.profile_name
        for name, item in self._profile_items.items():
            item.state = 1 if name == active else 0

    def _refresh_labels(self, _timer: object) -> None:
        """The @rumps.timer callback: re-render everything from the controller's state."""
        self.app.title = self.controller.title()
        state = self.controller.state

        self.pause_item.title = "Resume" if state == "paused" else "Pause"
        self.pause_item.set_callback(self._on_toggle_pause if state in _PAUSEABLE_STATES else None)
        self.abort_item.set_callback(self._on_abort if state in _ABORTABLE_STATES else None)

        busy = state in ("counting_down", "typing", "paused")
        self.type_clipboard_item.set_callback(None if busy else self._on_type_clipboard)
        self.type_file_item.set_callback(None if busy else self._on_type_file)

        self._refresh_profile_checkmarks()

    def run(self) -> None:
        self.app.run()


def main() -> int:
    MenuBarApp().run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
