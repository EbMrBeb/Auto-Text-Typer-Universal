"""Per-editor keyboard shortcut overrides.

Every formatting command the app can press (bold, a heading style, a list
toggle, ...) has a default shortcut for each editor, defined in
``targets.py``. This editor lets someone whose editor binds a command
differently -- or who is typing into an editor with its own custom shortcuts
-- override any of them. Overrides are saved in
``AppSettings.keymap_overrides`` (``{target id: {command value: chord text}}``)
through the shared :class:`~auto_essay_typer.gui.settings_store.SettingsStore`,
so ``app_settings.effective_keymap`` picks them up with no further wiring.

Rules this editor enforces, mirrored from the store so nothing it writes can
produce a settings file that would not load back:

* what the user types is normalised (``"Ctrl + Shift + 7"`` becomes
  ``"ctrl+shift+7"``) and then validated with ``keymaps.parse_chord``;
* an override equal to the default is dropped rather than stored;
* an override that would collide with one of the global hotkeys is refused
  with the reason, for *any* editor, not just the currently selected one.
"""

from __future__ import annotations

import dataclasses
import re
from typing import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget

from auto_essay_typer import platforms, targets
from auto_essay_typer.actions import FormatOp
from auto_essay_typer.gui.advanced_common import NoWheelComboBox, ThemedMixin, default_confirm
from auto_essay_typer.gui.settings_store import SettingsStore, validate_settings
from auto_essay_typer.gui.theme import ThemeManager
from auto_essay_typer.keymaps import NO_OP, Chord, format_chord, parse_chord

_ALIASES = {"command": "cmd", "option": "alt", "control": "ctrl", "return": "enter"}
_EXAMPLE = "ctrl+shift+7"
_DEFAULT_COLUMN_WIDTH = 140
_EDIT_COLUMN_WIDTH = 200


# --- text helpers -------------------------------------------------------------------------


def op_label(op: FormatOp) -> str:
    """``FormatOp.TOGGLE_BOLD`` -> ``"Toggle bold"`` (the enum value, made readable)."""
    words = op.value.replace("_", " ")
    return words[:1].upper() + words[1:]


def normalize_chord_text(text: str) -> str:
    """Tidy what a person typed so ``parse_chord`` can judge it.

    Strips spaces (including around ``+``), lower-cases, and accepts the
    long names ``command``/``option``/``control``/``return``. An empty or
    all-blank string normalises to ``""`` (meaning "no override").
    """
    parts = [part.strip().lower() for part in text.strip().split("+")]
    if parts == [""]:
        return ""
    return "+".join(_ALIASES.get(part, part) for part in parts)


def describe_chord_error(error: ValueError) -> str:
    """A plain-language reason for a ``parse_chord`` failure, with an example."""
    text = str(error)
    modifier = re.search(r"unknown modifier '([^']*)'", text)
    if modifier:
        return (
            f"'{modifier.group(1)}' isn't a modifier key. Use cmd, ctrl, alt or shift, "
            f"for example {_EXAMPLE}."
        )
    final = re.search(r"invalid final key '([^']*)'", text)
    if final:
        return (
            f"'{final.group(1)}' isn't a single key. End with one character, or tab, enter "
            f"or space, for example {_EXAMPLE}."
        )
    if "empty part" in text:
        return f"A key is missing next to a '+'. Try something like {_EXAMPLE}."
    return f"That isn't a valid shortcut. Try something like {_EXAMPLE}."


_COLLISION = re.compile(r"hotkey for '([^']*)' \('([^']*)'\) collides with")


def describe_store_error(error: ValueError) -> str:
    """A short, plain-language version of why the settings store refused a shortcut.

    The store's own message (which names internal identifiers) is kept in full
    as the row's tooltip; this is what fits under the box.
    """
    collision = _COLLISION.search(str(error))
    if collision:
        return f"Clashes with your “{collision.group(1)}” hotkey. Pick another shortcut."
    return f"Can't use this shortcut: {error}"


def _set_property(widget: QWidget, name: str, value: object) -> None:
    widget.setProperty(name, value)
    widget.style().unpolish(widget)
    widget.style().polish(widget)


# --- one row ---------------------------------------------------------------------------------


class ShortcutRow(QWidget):
    """One command: its name, its default shortcut, and an editable override."""

    committed = Signal(object)  # FormatOp -- editing finished (Enter or focus left)

    def __init__(self, op: FormatOp, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.op = op
        #: The normalised override currently saved for this command ("" = none).
        self.saved = ""
        self._populating = False

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 6, 0, 6)
        layout.setSpacing(12)

        self.action_label = QLabel(op_label(op))
        self.action_label.setObjectName("ShortcutAction")
        layout.addWidget(self.action_label, 1, Qt.AlignmentFlag.AlignTop)

        self.default_label = QLabel()
        self.default_label.setObjectName("ShortcutDefault")
        self.default_label.setFixedWidth(_DEFAULT_COLUMN_WIDTH)
        layout.addWidget(self.default_label, 0, Qt.AlignmentFlag.AlignTop)

        right = QWidget()
        right.setFixedWidth(_EDIT_COLUMN_WIDTH)
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(2)
        self.edit = QLineEdit()
        self.edit.setAccessibleName(f"{op_label(op)} shortcut")
        self.edit.setToolTip(
            f"Type a shortcut such as {_EXAMPLE}. Leave it empty to use the default."
        )
        self.edit.textChanged.connect(self._on_text_changed)
        self.edit.editingFinished.connect(lambda: self.committed.emit(self.op))
        right_layout.addWidget(self.edit)
        self.error_label = QLabel()
        self.error_label.setObjectName("FieldError")
        self.error_label.setWordWrap(True)
        self.error_label.setVisible(False)
        right_layout.addWidget(self.error_label)
        layout.addWidget(right, 0, Qt.AlignmentFlag.AlignTop)

    # -- configuration ---------------------------------------------------------------------------------

    def configure(self, base: Chord | None) -> None:
        """Show the default ``base`` shortcut: ``None`` = the editor has none, ``()`` = deliberately none."""
        if base is None:
            self.default_label.setText("Not available")
            self.edit.setPlaceholderText("not set")
        elif base == NO_OP:
            self.default_label.setText("No shortcut")
            self.edit.setPlaceholderText("none")
        else:
            text = format_chord(base)
            self.default_label.setText(text)
            self.edit.setPlaceholderText(text)

    def set_saved(self, text: str) -> None:
        """Make ``text`` (a normalised override, or ``""``) the saved value and show it."""
        self.saved = text
        self._populating = True
        try:
            self.edit.setText(text)
        finally:
            self._populating = False
        self.set_error(None)
        _set_property(self.edit, "overridden", bool(text))

    def set_error(self, message: str | None, details: str = "") -> None:
        """Show ``message`` under the box; ``details`` (the full reason) goes in its tooltip."""
        self.error_label.setText(message or "")
        self.error_label.setToolTip(details if message else "")
        self.error_label.setVisible(bool(message))
        _set_property(self.edit, "invalid", bool(message))

    def has_error(self) -> bool:
        return bool(self.error_label.text())

    def pending_text(self) -> str:
        return normalize_chord_text(self.edit.text())

    def _on_text_changed(self, _text: str) -> None:
        # Typing a shortcut passes through half-finished states ("ctrl+"), so
        # errors are only *raised* when editing finishes; but one that is
        # already showing clears the moment the text parses again.
        if self._populating or not self.has_error():
            return
        pending = self.pending_text()
        if not pending:
            self.set_error(None)
            return
        try:
            parse_chord(pending)
        except ValueError:
            return
        self.set_error(None)


# --- the editor ----------------------------------------------------------------------------------


class KeymapEditor(QWidget, ThemedMixin):
    """A card with an editor picker and one override row per formatting command.

    Attributes tests (and the Advanced page) use: ``target_combo`` (item data =
    target id), ``rows`` (``FormatOp`` -> :class:`ShortcutRow`),
    ``restore_button``, ``duplicates_label``, ``message_label``.

    ``overridesChanged`` fires after an override was saved (or restored).
    """

    overridesChanged = Signal()

    def __init__(
        self,
        theme_manager: ThemeManager | None,
        settings_store: SettingsStore,
        *,
        confirm: Callable[[QWidget | None, str, str], bool] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._store = settings_store
        self._confirm = confirm if confirm is not None else default_confirm
        self._os = platforms.current_os()
        self._targets = targets.targets_for(self._os)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 14)
        outer.setSpacing(10)

        title = QLabel("Keyboard shortcuts")
        title.setObjectName("SectionTitle")
        outer.addWidget(title)
        intro = QLabel(
            "These are the shortcuts pressed to format a document you import. Change one if "
            "your editor uses a different shortcut for that command. A change is saved as soon "
            "as you press Enter or click away."
        )
        intro.setObjectName("MutedLabel")
        intro.setWordWrap(True)
        outer.addWidget(intro)

        picker = QHBoxLayout()
        picker.setSpacing(10)
        picker.addWidget(QLabel("Editor"))
        self.target_combo = NoWheelComboBox()
        self.target_combo.setAccessibleName("Editor")
        for target in self._targets:
            self.target_combo.addItem(target.name, target.id)
        picker.addWidget(self.target_combo, 1)
        self.restore_button = QPushButton("Restore defaults")
        self.restore_button.setObjectName("SecondaryButton")
        self.restore_button.setAccessibleName("Restore default shortcuts")
        self.restore_button.setToolTip("Remove your custom shortcuts for this editor")
        self.restore_button.clicked.connect(self._on_restore_clicked)
        picker.addWidget(self.restore_button)
        outer.addLayout(picker)

        self.caution_label = QLabel()
        self.caution_label.setObjectName("MutedLabel")
        self.caution_label.setWordWrap(True)
        self.caution_label.setVisible(False)
        outer.addWidget(self.caution_label)

        self.message_label = QLabel()
        self.message_label.setObjectName("FieldError")
        self.message_label.setWordWrap(True)
        self.message_label.setVisible(False)
        outer.addWidget(self.message_label)

        self.duplicates_label = QLabel()
        self.duplicates_label.setObjectName("MutedLabel")
        self.duplicates_label.setWordWrap(True)
        self.duplicates_label.setVisible(False)
        outer.addWidget(self.duplicates_label)

        outer.addWidget(self._build_header())
        self.rows: dict[FormatOp, ShortcutRow] = {}
        for position, op in enumerate(FormatOp):
            if position:
                outer.addWidget(self._divider())
            row = ShortcutRow(op)
            row.committed.connect(self._on_row_committed)
            self.rows[op] = row
            outer.addWidget(row)

        default_id = settings_store.settings.target_app
        if self._index_of(default_id) >= 0:
            self.target_combo.setCurrentIndex(self._index_of(default_id))
        self.target_combo.currentIndexChanged.connect(self._on_target_changed)
        settings_store.changed.connect(self._on_settings_changed)

        self._populate()
        self._bind_theme(theme_manager)

    # -- construction helpers -----------------------------------------------------------------------

    @staticmethod
    def _divider() -> QFrame:
        line = QFrame()
        line.setObjectName("FieldDivider")
        line.setFixedHeight(1)
        return line

    @staticmethod
    def _build_header() -> QWidget:
        header = QWidget()
        layout = QHBoxLayout(header)
        layout.setContentsMargins(0, 4, 0, 0)
        layout.setSpacing(12)
        action = QLabel("Command")
        action.setObjectName("ColumnHeader")
        layout.addWidget(action, 1)
        default = QLabel("Default")
        default.setObjectName("ColumnHeader")
        default.setFixedWidth(_DEFAULT_COLUMN_WIDTH)
        layout.addWidget(default)
        custom = QLabel("Your shortcut")
        custom.setObjectName("ColumnHeader")
        custom.setFixedWidth(_EDIT_COLUMN_WIDTH)
        layout.addWidget(custom)
        return header

    def _index_of(self, target_id: str) -> int:
        return self.target_combo.findData(target_id)

    # -- selection -------------------------------------------------------------------------------------

    def current_target_id(self) -> str:
        return str(self.target_combo.currentData())

    def set_target(self, target_id: str) -> None:
        index = self._index_of(target_id)
        if index < 0:
            raise ValueError(f"{target_id!r} is not available on this system")
        self.target_combo.setCurrentIndex(index)

    def _on_target_changed(self, _index: int) -> None:
        self._populate()

    # -- reading the saved state -------------------------------------------------------------------------

    def _saved_overrides(self, target_id: str) -> dict[str, str]:
        return dict(self._store.settings.keymap_overrides.get(target_id, {}))

    def _base_keymap(self, target_id: str) -> dict[FormatOp, Chord]:
        return dict(targets.keymap_for(target_id, self._os))

    # -- populating -----------------------------------------------------------------------------------------

    def _populate(self) -> None:
        """Rebuild every row from the selected editor's defaults and the saved overrides."""
        target_id = self.current_target_id()
        target = targets.get_target(target_id)
        base = self._base_keymap(target_id)
        saved = self._saved_overrides(target_id)
        for op, row in self.rows.items():
            row.configure(base.get(op))
            row.set_saved(saved.get(op.value, ""))
        if target.verified:
            self.caution_label.setVisible(False)
        else:
            self.caution_label.setText(
                f"{target.name} shortcuts haven't been checked against a live install. "
                "Adjust any that don't work in your version."
            )
            self.caution_label.setVisible(True)
        self._show_message(None)
        self._refresh_summary()

    def _on_settings_changed(self, _settings: object) -> None:
        """Another part of the app changed the settings: refresh any row that is now out of date."""
        target_id = self.current_target_id()
        saved = self._saved_overrides(target_id)
        for op, row in self.rows.items():
            wanted = saved.get(op.value, "")
            if row.saved != wanted:
                row.set_saved(wanted)
        self._refresh_summary()

    def _refresh_summary(self) -> None:
        target_id = self.current_target_id()
        self.restore_button.setEnabled(bool(self._saved_overrides(target_id)))
        self._refresh_duplicates(target_id)

    def _refresh_duplicates(self, target_id: str) -> None:
        keymap = self._base_keymap(target_id)
        for op_text, chord_text in self._saved_overrides(target_id).items():
            try:
                keymap[FormatOp(op_text)] = parse_chord(chord_text) if chord_text else NO_OP
            except ValueError:
                continue
        users: dict[Chord, list[str]] = {}
        for op, chord in keymap.items():
            if chord != NO_OP:
                users.setdefault(chord, []).append(op_label(op))
        shared = [
            f"{format_chord(chord)} is used by {' and '.join(names) if len(names) == 2 else ', '.join(names)}"
            for chord, names in users.items()
            if len(names) > 1
        ]
        if shared:
            self.duplicates_label.setText(
                "Heads up: " + "; ".join(shared) + ". Typing may not format as expected."
            )
        self.duplicates_label.setVisible(bool(shared))

    def _show_message(self, text: str | None) -> None:
        self.message_label.setText(text or "")
        self.message_label.setVisible(bool(text))

    # -- committing an edit ------------------------------------------------------------------------------------

    def _on_row_committed(self, op: FormatOp) -> None:
        row = self.rows[op]
        target_id = self.current_target_id()
        text = row.pending_text()

        normalized: str | None = None  # None = no override for this command
        if text:
            try:
                chord = parse_chord(text)
            except ValueError as exc:
                row.set_error(describe_chord_error(exc))
                return
            base = self._base_keymap(target_id).get(op)
            if base is None or chord != base:
                normalized = format_chord(chord)

        current = self._saved_overrides(target_id)
        wanted = dict(current)
        if normalized is None:
            wanted.pop(op.value, None)
        else:
            wanted[op.value] = normalized
        if wanted == current:
            row.set_saved(normalized or "")  # e.g. retyped the default: tidy the box
            return

        everything = {
            target: dict(overrides) for target, overrides in self._store.settings.keymap_overrides.items()
        }
        if wanted:
            everything[target_id] = wanted
        else:
            everything.pop(target_id, None)

        try:
            # The store validates against the *current* editor; check this one too,
            # so a shortcut can't be saved that would collide the moment the user
            # switches to the editor being edited.
            validate_settings(
                dataclasses.replace(
                    self._store.settings, target_app=target_id, keymap_overrides=everything
                )
            )
            self._store.update(keymap_overrides=everything)
        except ValueError as exc:
            row.set_error(describe_store_error(exc), str(exc))
            return
        except OSError as exc:
            row.set_error(f"Could not save: {exc}")
            return

        row.set_saved(normalized or "")
        self._refresh_summary()
        self.overridesChanged.emit()

    # -- restoring ---------------------------------------------------------------------------------------------------

    def _on_restore_clicked(self) -> None:
        target_id = self.current_target_id()
        if not self._saved_overrides(target_id):
            return
        name = targets.get_target(target_id).name
        if not self._confirm(
            self,
            "Restore default shortcuts",
            f"Remove your custom shortcuts for {name} and go back to its defaults?",
        ):
            return
        everything = {
            target: dict(overrides)
            for target, overrides in self._store.settings.keymap_overrides.items()
            if target != target_id
        }
        try:
            self._store.update(keymap_overrides=everything)
        except (ValueError, OSError) as exc:
            self._show_message(f"Could not restore the defaults: {exc}")
            return
        self._populate()  # also clears any half-typed rows
        self.overridesChanged.emit()

    # -- theming -----------------------------------------------------------------------------------------------------------

    def _restyle(self) -> None:
        t = self.tokens
        self.setStyleSheet(
            f"""
            QLabel#FieldError {{ color: {t.danger}; font-size: 12px; font-weight: 600; }}
            QLabel#ColumnHeader {{ color: {t.muted_text}; font-size: 12px; font-weight: 600; }}
            QLabel#ShortcutAction {{ font-size: 13px; padding-top: 6px; }}
            QLabel#ShortcutDefault {{
                color: {t.muted_text}; font-size: 12px; padding-top: 7px;
                font-family: Menlo, "SF Mono", Consolas, "DejaVu Sans Mono", monospace;
            }}
            QLineEdit[overridden="true"] {{ border-color: {t.accent}; }}
            QLineEdit[invalid="true"] {{ border-color: {t.danger}; }}
            QFrame#FieldDivider {{ background: {t.border}; border: none; }}
            """
        )
