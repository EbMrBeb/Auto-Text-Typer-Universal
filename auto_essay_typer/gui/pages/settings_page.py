"""The Settings page: every app preference, saved the moment it changes.

Each control reads from and writes to the shared
:class:`~auto_essay_typer.gui.settings_store.SettingsStore`, so nothing here has an
"Apply" button, the file on disk is always what the page shows, and a change made
anywhere else (a reset, onboarding) shows up here too. Four blocks:

* **Typing** -- the editor to type into by default (whose keyboard shortcuts the
  Advanced page can override), the countdown, whether to ask before typing starts
  from the tray or a hotkey, and whether to minimise this window when typing starts.
* **Appearance** -- the theme, and the small always-on-top progress window.
* **Global hotkeys** -- an on/off switch and the three hotkeys, which are checked as
  they are committed (Enter, or leaving the field): an invalid set is explained next to
  the fields and never saved, so the file on disk can always be loaded back. Below
  them, whether the hotkeys are actually working, with a retry when they are not.
* **Reset to defaults** -- after a confirmation.

Everything with a side effect is injectable (the confirmation, the hotkey service), so
the page can be driven from tests without blocking on a dialog or starting a listener.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from auto_essay_typer import app_settings, platforms, targets
from auto_essay_typer.app_settings import AppSettings
from auto_essay_typer.gui.advanced_common import (
    NoWheelComboBox,
    NoWheelSpinBox,
    ThemedMixin,
    default_confirm,
    spinbox_stylesheet,
)
from auto_essay_typer.gui.controls import ToggleSwitch, focus_ring_bleed, switch_row
from auto_essay_typer.gui.hotkeys import (
    ACTIONS as HOTKEY_ACTIONS,
    STATE_ACTIVE,
    STATE_OFF,
    STATE_STARTING,
    STATE_UNAVAILABLE,
    HotkeyService,
    validated_hotkeys,
)
from auto_essay_typer.gui.settings_store import SettingsStore, validate_settings
from auto_essay_typer.gui.theme import ThemeManager, ThemeMode
from auto_essay_typer.gui.widgets import LinkButton, make_card

_THEME_LABELS: tuple[tuple[ThemeMode, str], ...] = (
    (ThemeMode.SYSTEM, "System"),
    (ThemeMode.LIGHT, "Light"),
    (ThemeMode.DARK, "Dark"),
)

#: ``(AppSettings.hotkeys key, label)`` for the three hotkey fields, in display order.
_HOTKEY_FIELDS: tuple[tuple[str, str], ...] = (
    ("start", "Start"),
    ("pause", "Pause"),
    ("stop", "Stop"),
)
assert tuple(action for action, _label in _HOTKEY_FIELDS) == HOTKEY_ACTIONS

#: A card is capped so a wide window does not stretch two rows of controls edge to edge.
CARD_MAX_WIDTH = 560
#: Width of the label column, shared by every row so the controls start at one x.
_LABEL_WIDTH = 96
_COUNTDOWN_MAX = 30

HOTKEY_SYNTAX_HINT = (
    "Write each hotkey as keys joined with +, for example <ctrl>+<alt>+<shift>+t. "
    "Modifier and special keys go in angle brackets: <ctrl>, <alt>, <shift>, <cmd>, <esc>, "
    "<f9>. Letters and digits are written as they are."
)


def describe_hotkey_problem(text: str) -> str | None:
    """Why ``text`` is not a usable hotkey, in words for the person typing it; ``None`` if it parses.

    pynput reports a bad hotkey by raising ``ValueError`` with just the offending
    text, which is not something to put in front of a user.
    """
    text = text.strip()
    if not text:
        return "Enter a hotkey."
    try:
        from pynput.keyboard import HotKey

        HotKey.parse(text)
    except ValueError as exc:
        bad = str(exc)
        if bad and bad != text:
            return (
                f"'{bad}' is not a key I know. Use <ctrl>, <alt>, <shift>, <cmd>, <esc>, "
                "<f1> to <f20>, or a single letter or digit."
            )
        return (
            f"'{text}' is not a valid hotkey. Join keys with +, give each key once, and put "
            "modifier and special keys in angle brackets, like <ctrl>+<alt>+t."
        )
    except Exception as exc:  # noqa: BLE001 - e.g. the keyboard library cannot load on a headless box
        return f"Could not check this hotkey: {exc}"
    return None


class SettingsPage(QWidget, ThemedMixin):
    """The Settings page. See the module docstring for the layout.

    Public surface (all widgets, so a test or a caller can reach them): ``target_combo``,
    ``countdown_spin``, ``confirm_switch``, ``minimize_switch``, ``theme_combo``,
    ``mini_window_checkbox``, ``hotkeys_switch``, ``hotkey_edits`` (``{"start": QLineEdit,
    ...}``), ``hotkey_error_label``, ``hotkey_status_label``, ``hotkey_retry_button``,
    ``restore_hotkeys_button``, ``reset_button``, ``save_error_label``.
    """

    def __init__(
        self,
        theme_manager: ThemeManager,
        settings_store: SettingsStore,
        *,
        hotkey_service: HotkeyService | None = None,
        confirm: Callable[[QWidget | None, str, str], bool] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._theme_manager = theme_manager
        self._store = settings_store
        self._hotkey_service = hotkey_service
        self._confirm = confirm if confirm is not None else default_confirm
        #: True while the controls are being filled from the store, so that doing so is not
        #: mistaken for the user changing them.
        self._refreshing = False
        #: The hotkeys last copied from the store into the fields. A refresh that finds the
        #: store unchanged leaves the fields alone, so a half-typed edit survives it.
        self._shown_hotkeys: dict[str, str] = {}

        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 20, 24, 20)
        outer.setSpacing(12)

        title = QLabel("Settings")
        title.setObjectName("PageTitle")
        outer.addWidget(title)

        self.save_error_label = QLabel()
        self.save_error_label.setObjectName("InlineMessage")
        self.save_error_label.setProperty("tone", "danger")
        self.save_error_label.setWordWrap(True)
        self.save_error_label.setVisible(False)
        outer.addWidget(self.save_error_label)

        scroll = QScrollArea()
        scroll.setObjectName("PageScroll")  # draws the keyboard focus ring (see theme.py)
        scroll.setAccessibleName("Settings sections")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        content = QWidget()
        sections = QVBoxLayout(content)
        sections.setContentsMargins(0, 0, 0, 0)
        sections.setSpacing(12)
        sections.addWidget(self._build_typing_card())
        sections.addWidget(self._build_appearance_card())
        sections.addWidget(self._build_hotkeys_card())
        sections.addWidget(self._build_reset_block())
        sections.addStretch(1)
        scroll.setWidget(content)
        # The scroll area draws its keyboard focus ring in a 2px border, which would inset the
        # cards inside it; let that border sit outside the page margin so they still line up
        # with the title above.
        outer.addLayout(focus_ring_bleed(scroll), 1)

        self._connect_controls()
        self._refresh_from(self._store.settings, force=True)
        self._store.changed.connect(self._on_store_changed)
        if self._hotkey_service is not None:
            self._hotkey_service.statusChanged.connect(self._on_hotkey_status)
        self._render_hotkey_status()
        self._bind_theme(theme_manager)

    # -- construction -----------------------------------------------------------------

    @staticmethod
    def _card(title: str) -> tuple[QWidget, QVBoxLayout]:
        card = make_card()
        card.setMaximumWidth(CARD_MAX_WIDTH)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(12)
        heading = QLabel(title)
        heading.setObjectName("SectionTitle")
        layout.addWidget(heading)
        return card, layout

    @staticmethod
    def _row(label_text: str, control: QWidget, *, buddy: bool = True) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(12)
        label = QLabel(label_text)
        label.setFixedWidth(_LABEL_WIDTH)
        if buddy:
            label.setBuddy(control)
        row.addWidget(label)
        row.addWidget(control, 1)
        return row

    @staticmethod
    def _switch(text: str, tooltip: str) -> ToggleSwitch:
        switch = ToggleSwitch(text)
        switch.setAccessibleName(text)
        switch.setToolTip(tooltip)
        return switch

    def _build_typing_card(self) -> QWidget:
        card, layout = self._card("Typing")

        self.target_combo = NoWheelComboBox()
        self.target_combo.setAccessibleName("Typing into")
        self.target_combo.setToolTip(
            "The editor you usually type into. Its keyboard shortcuts are the ones pressed for "
            "headings, bold and other formatting."
        )
        for target in targets.targets_for(platforms.current_os()):
            self.target_combo.addItem(target.name, target.id)
        layout.addLayout(self._row("Typing into", self.target_combo))

        self.target_error_label = QLabel()
        self.target_error_label.setObjectName("InlineMessage")
        self.target_error_label.setProperty("tone", "danger")
        self.target_error_label.setWordWrap(True)
        self.target_error_label.setVisible(False)
        layout.addWidget(self.target_error_label)

        self.countdown_spin = NoWheelSpinBox()
        self.countdown_spin.setRange(0, _COUNTDOWN_MAX)
        self.countdown_spin.setSuffix(" seconds")
        self.countdown_spin.setSpecialValueText("No countdown")
        self.countdown_spin.setAccessibleName("Countdown before typing starts")
        self.countdown_spin.setToolTip(
            "How long to wait after you start, so you can click into the document the text "
            "should go into."
        )
        self.countdown_spin.setMinimumWidth(150)
        countdown_row = self._row("Countdown", self.countdown_spin)
        countdown_row.setStretch(1, 0)
        countdown_row.addStretch(1)
        layout.addLayout(countdown_row)

        self.confirm_switch = self._switch(
            "Confirm before typing from the tray or a hotkey",
            "Ask first when typing is started from the tray menu or a global hotkey, where the "
            "text is not in front of you.",
        )
        layout.addLayout(switch_row(self.confirm_switch))

        self.minimize_switch = self._switch(
            "Minimise this window when typing starts",
            "Get this window out of the way so the document being typed into is visible. Pause "
            "and stop from the tray icon or the mini progress window.",
        )
        layout.addLayout(switch_row(self.minimize_switch))
        return card

    def _build_appearance_card(self) -> QWidget:
        card, layout = self._card("Appearance")

        self.theme_combo = NoWheelComboBox()
        self.theme_combo.setAccessibleName("Theme")
        self.theme_combo.setToolTip("Match the system, or always use the light or dark theme.")
        for mode, label in _THEME_LABELS:
            self.theme_combo.addItem(label, mode)
        layout.addLayout(self._row("Theme", self.theme_combo))

        self.mini_window_checkbox = self._switch(
            "Show mini progress window while typing",
            "Keep a small always-on-top progress window in the corner while text is typed.",
        )
        layout.addLayout(switch_row(self.mini_window_checkbox))
        return card

    def _build_hotkeys_card(self) -> QWidget:
        card, layout = self._card("Global hotkeys")

        self.hotkeys_switch = self._switch(
            "Use global hotkeys",
            "Start, pause and stop typing from any app, even while this window is hidden.",
        )
        layout.addLayout(switch_row(self.hotkeys_switch))

        self.hotkey_edits: dict[str, QLineEdit] = {}
        for action, label in _HOTKEY_FIELDS:
            edit = QLineEdit()
            edit.setObjectName("HotkeyEdit")
            edit.setAccessibleName(f"{label} hotkey")
            edit.setToolTip(HOTKEY_SYNTAX_HINT)
            edit.setProperty("invalid", False)  # the stylesheet marks a field whose text was rejected
            self.hotkey_edits[action] = edit
            layout.addLayout(self._row(label, edit))

        self.hotkey_error_label = QLabel()
        self.hotkey_error_label.setObjectName("InlineMessage")
        self.hotkey_error_label.setProperty("tone", "danger")
        self.hotkey_error_label.setWordWrap(True)
        self.hotkey_error_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.hotkey_error_label.setVisible(False)
        layout.addWidget(self.hotkey_error_label)

        hint = QLabel(HOTKEY_SYNTAX_HINT)
        hint.setObjectName("MutedLabel")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        status_row = QHBoxLayout()
        status_row.setSpacing(10)
        self.hotkey_status_label = QLabel()
        self.hotkey_status_label.setObjectName("HotkeyStatus")
        self.hotkey_status_label.setWordWrap(True)
        status_row.addWidget(self.hotkey_status_label, 1)
        self.hotkey_retry_button = LinkButton("Try again", theme_manager=self._theme_manager)
        self.hotkey_retry_button.setAccessibleName("Try starting the global hotkeys again")
        self.hotkey_retry_button.setToolTip("Attempt to start the global hotkeys again.")
        self.hotkey_retry_button.setVisible(False)
        status_row.addWidget(self.hotkey_retry_button, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(status_row)

        restore_row = QHBoxLayout()
        self.restore_hotkeys_button = LinkButton("Restore default hotkeys", theme_manager=self._theme_manager)
        self.restore_hotkeys_button.setAccessibleName("Restore the default hotkeys")
        self.restore_hotkeys_button.setToolTip("Put the three hotkeys back to this system's defaults.")
        restore_row.addWidget(self.restore_hotkeys_button)
        restore_row.addStretch(1)
        layout.addLayout(restore_row)
        return card

    def _build_reset_block(self) -> QWidget:
        block = QWidget()
        block.setMaximumWidth(CARD_MAX_WIDTH)
        layout = QHBoxLayout(block)
        layout.setContentsMargins(0, 4, 0, 0)
        layout.setSpacing(12)
        self.reset_button = QPushButton("Reset to defaults")
        self.reset_button.setObjectName("SecondaryButton")
        self.reset_button.setAccessibleName("Reset settings to defaults")
        self.reset_button.setToolTip("Put every setting on this page back to its default.")
        layout.addWidget(self.reset_button)
        note = QLabel("Your typing profiles, keyboard shortcuts and setup progress are kept.")
        note.setObjectName("MutedLabel")
        note.setWordWrap(True)
        layout.addWidget(note, 1)
        return block

    def _connect_controls(self) -> None:
        self.target_combo.currentIndexChanged.connect(self._on_target_changed)
        self.countdown_spin.valueChanged.connect(self._on_countdown_changed)
        self.confirm_switch.toggled.connect(lambda checked: self._save(confirm_before_typing=checked))
        self.minimize_switch.toggled.connect(lambda checked: self._save(minimize_on_start=checked))
        self.theme_combo.currentIndexChanged.connect(self._on_theme_changed)
        self.mini_window_checkbox.toggled.connect(lambda checked: self._save(show_mini_window=checked))
        self.hotkeys_switch.toggled.connect(lambda checked: self._save(global_hotkeys_enabled=checked))
        for edit in self.hotkey_edits.values():
            edit.editingFinished.connect(self._commit_hotkeys)
        self.restore_hotkeys_button.clicked.connect(self._restore_default_hotkeys)
        self.hotkey_retry_button.clicked.connect(self._retry_hotkeys)
        self.reset_button.clicked.connect(self._on_reset)

    # -- store -> controls ------------------------------------------------------------------

    def _on_store_changed(self, settings: object) -> None:
        if isinstance(settings, AppSettings):
            self._refresh_from(settings)

    def _refresh_from(self, settings: AppSettings, *, force: bool = False) -> None:
        """Show ``settings`` in the controls without that counting as the user changing them."""
        self._refreshing = True
        try:
            self._set_combo(self.target_combo, settings.target_app)
            self._set_spin(self.countdown_spin, settings.countdown_seconds)
            for switch, value in (
                (self.confirm_switch, settings.confirm_before_typing),
                (self.minimize_switch, settings.minimize_on_start),
                (self.mini_window_checkbox, settings.show_mini_window),
                (self.hotkeys_switch, settings.global_hotkeys_enabled),
            ):
                self._set_switch(switch, value)
            self._set_combo(self.theme_combo, ThemeMode(settings.theme))
            stored = {action: str(settings.hotkeys.get(action, "")) for action, _label in _HOTKEY_FIELDS}
            if force or stored != self._shown_hotkeys:
                self._shown_hotkeys = stored
                for action, edit in self.hotkey_edits.items():
                    edit.setText(stored[action])
                self._show_hotkey_problems({})
            self.target_error_label.setVisible(False)
        finally:
            self._refreshing = False

    @staticmethod
    def _set_combo(combo: QComboBox, data: object) -> None:
        index = combo.findData(data)
        if index >= 0 and index != combo.currentIndex():
            combo.blockSignals(True)
            combo.setCurrentIndex(index)
            combo.blockSignals(False)

    @staticmethod
    def _set_spin(spin: QSpinBox, value: int) -> None:
        if spin.value() != value:
            spin.blockSignals(True)
            spin.setValue(value)
            spin.blockSignals(False)

    @staticmethod
    def _set_switch(switch: ToggleSwitch, value: bool) -> None:
        if switch.isChecked() != value:
            switch.blockSignals(True)
            switch.setChecked(value)
            switch.blockSignals(False)
            switch.update()

    # -- controls -> store ----------------------------------------------------------------------

    def _save(self, **changes: object) -> bool:
        """Write ``changes`` to the store; on failure put the controls back and say why."""
        if self._refreshing:
            return True
        try:
            self._store.update(**changes)
        except ValueError as exc:
            self._show_save_error(f"That setting could not be used: {exc}")
        except OSError as exc:
            self._show_save_error(f"Your settings could not be saved: {exc}")
        else:
            self.save_error_label.setVisible(False)
            return True
        self._refresh_from(self._store.settings)  # show what is really stored
        return False

    def _show_save_error(self, text: str) -> None:
        self.save_error_label.setText(text)
        self.save_error_label.setVisible(True)

    def _on_target_changed(self, _index: int) -> None:
        if self._refreshing:
            return
        self.target_error_label.setVisible(False)
        target_id = self.target_combo.currentData()
        try:
            self._store.update(target_app=target_id)
        except ValueError as exc:
            # e.g. one of your hotkeys is a formatting shortcut in that editor
            self.target_error_label.setText(f"Can't type into that editor: {exc}")
            self.target_error_label.setVisible(True)
            self._set_combo(self.target_combo, self._store.settings.target_app)
        except OSError as exc:
            self._show_save_error(f"Your settings could not be saved: {exc}")
            self._set_combo(self.target_combo, self._store.settings.target_app)
        else:
            self.save_error_label.setVisible(False)

    def _on_countdown_changed(self, value: int) -> None:
        self._save(countdown_seconds=value)

    def _on_theme_changed(self, _index: int) -> None:
        if self._refreshing:
            return
        # ThemeMode is a str Enum; QComboBox.currentData() round-trips it through Qt's
        # QVariant, which can hand back a plain str that is == the enum member but not
        # `is` it -- ThemeMode(...) restores the real member.
        mode = ThemeMode(self.theme_combo.currentData())
        if self._save(theme=mode.value):
            self._theme_manager.set_mode(mode)

    # -- hotkeys --------------------------------------------------------------------------------------

    def _hotkey_candidate(self) -> dict[str, str]:
        return {action: edit.text().strip() for action, edit in self.hotkey_edits.items()}

    def hotkey_problems(self, candidate: Mapping[str, str]) -> dict[str, str]:
        """``{field: reason}`` for everything wrong with ``candidate``; empty when it can be saved.

        Checks, field by field, that each hotkey parses, then that no two are the same
        key, then that none of them is a formatting shortcut of the default editor
        (the app presses those itself while typing). A problem that belongs to the whole
        set rather than one field is reported under the key ``""``.
        """
        problems: dict[str, str] = {}
        for action, text in candidate.items():
            reason = describe_hotkey_problem(text)
            if reason is not None:
                problems[action] = reason
        if problems:
            return problems

        try:
            validated_hotkeys(candidate)
        except ValueError as exc:  # two of them are the same key
            return {"": str(exc)}

        try:
            keymap = app_settings.effective_keymap(self._store.settings, platforms.current_os())
        except (ValueError, KeyError) as exc:
            return {"": f"Could not check the hotkeys against the editor's shortcuts: {exc}"}
        for action, text in candidate.items():
            try:
                app_settings.validate_hotkeys({action: text}, keymap)
            except ValueError as exc:
                problems[action] = str(exc)
        return problems

    def _commit_hotkeys(self) -> None:
        if self._refreshing:
            return
        candidate = self._hotkey_candidate()
        if candidate == self._shown_hotkeys:
            self._show_hotkey_problems({})  # back to what is saved: nothing to complain about
            return
        problems = self.hotkey_problems(candidate)
        if problems:
            self._show_hotkey_problems(problems)  # explained here, and not saved
            return
        try:
            self._store.update(hotkeys={**dict(self._store.settings.hotkeys), **candidate})
        except ValueError as exc:
            self._show_hotkey_problems({"": str(exc)})
        except OSError as exc:
            self._show_hotkey_problems({"": f"Your hotkeys could not be saved: {exc}"})
        else:
            self._shown_hotkeys = dict(candidate)
            self._show_hotkey_problems({})

    def _show_hotkey_problems(self, problems: Mapping[str, str]) -> None:
        for action, edit in self.hotkey_edits.items():
            invalid = action in problems
            if bool(edit.property("invalid")) != invalid:
                edit.setProperty("invalid", invalid)
                edit.style().unpolish(edit)
                edit.style().polish(edit)
        labels = dict(_HOTKEY_FIELDS)
        lines = [
            f"{labels[action]}: {reason}" if action in labels else reason
            for action, reason in problems.items()
        ]
        self.hotkey_error_label.setText("\n".join(lines))
        self.hotkey_error_label.setVisible(bool(lines))

    def _restore_default_hotkeys(self) -> None:
        defaults = app_settings.default_hotkeys(platforms.current_os())
        for action, edit in self.hotkey_edits.items():
            edit.setText(defaults[action])
        self._commit_hotkeys()

    def _retry_hotkeys(self) -> None:
        if self._hotkey_service is not None:
            self._hotkey_service.retry()

    def _on_hotkey_status(self, _state: str, _detail: str) -> None:
        self._render_hotkey_status()

    def _render_hotkey_status(self) -> None:
        service = self._hotkey_service
        if service is None:
            self.hotkey_status_label.setVisible(False)
            self.hotkey_retry_button.setVisible(False)
            return
        state, detail = service.state, service.detail
        if state == STATE_UNAVAILABLE:
            text = f"Not working: {detail}" if detail else "Not working: they could not start."
            tone = "danger"
        elif state == STATE_ACTIVE:
            text = "Global hotkeys are on." + (f" {detail}." if detail else "")
            tone = "muted"
        elif state == STATE_STARTING:
            text = "Starting the global hotkeys…"
            tone = "muted"
        else:
            assert state == STATE_OFF
            text = "Global hotkeys are off."
            tone = "muted"
        self.hotkey_status_label.setText(text)
        self.hotkey_status_label.setProperty("tone", tone)
        self.hotkey_status_label.style().unpolish(self.hotkey_status_label)
        self.hotkey_status_label.style().polish(self.hotkey_status_label)
        self.hotkey_status_label.setVisible(True)
        self.hotkey_retry_button.setVisible(state == STATE_UNAVAILABLE)

    # -- reset ---------------------------------------------------------------------------------------------

    def _on_reset(self) -> None:
        if not self._confirm(
            self,
            "Reset settings",
            "Put every setting on this page back to its default? Your typing profiles, keyboard "
            "shortcuts and setup progress are kept.",
        ):
            return
        try:
            self._store.reset_to_defaults()
        except (ValueError, OSError) as exc:
            self._show_save_error(f"Your settings could not be reset: {exc}")
            return
        self.save_error_label.setVisible(False)
        # The store announces a change only when something changed; either way the page should
        # end up clean, with no half-typed hotkey or old message left behind.
        self._refresh_from(self._store.settings, force=True)
        self._theme_manager.set_mode(ThemeMode(self._store.settings.theme))

    # -- theming -------------------------------------------------------------------------------------------------

    def _restyle(self) -> None:
        t = self.tokens
        self.setStyleSheet(
            spinbox_stylesheet(t)
            + f"""
            QLineEdit#HotkeyEdit {{
                font-family: Menlo, "SF Mono", Consolas, "DejaVu Sans Mono", monospace;
                font-size: 12px;
            }}
            QLineEdit#HotkeyEdit[invalid="true"],
            QLineEdit#HotkeyEdit[invalid="true"]:focus {{ border-color: {t.danger}; }}
            QLabel#HotkeyStatus {{ font-size: 12px; color: {t.muted_text}; }}
            QLabel#HotkeyStatus[tone="danger"] {{ color: {t.danger_text}; font-weight: 600; }}
            """
        )
