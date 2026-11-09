"""The Advanced page: typing-profile editor, per-editor shortcut overrides, file locations.

Three blocks, for people who want to go beyond the Type page's quick settings:

1. **Typing profile** -- a combo of the built-in and saved profiles with
   Save / Save as... / Delete / Reset to defaults, pinned above the scrolling
   content so they stay reachable while editing a field far down the page. The
   fields themselves are generated from the profile metadata
   (:class:`~auto_essay_typer.gui.profile_editor.ProfileEditor`).
2. **Keyboard shortcuts** -- override the shortcut pressed for any formatting
   command, per editor
   (:class:`~auto_essay_typer.gui.keymap_editor.KeymapEditor`).
3. **File locations** -- a link-style button that reveals where profiles and
   settings are stored, for power users who prefer to edit them by hand.

Everything with a side effect is injectable (profile reading/saving/deleting,
the name prompt, confirmations, opening a folder), so the page can be driven
from tests without touching real files or blocking on a modal dialog.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from auto_essay_typer import app_settings, config
from auto_essay_typer.actions import FormatOp
from auto_essay_typer.gui.advanced_common import (
    SCROLLBAR_WIDTH,
    NoWheelComboBox,
    ThemedMixin,
    default_confirm,
    default_name_prompt,
    default_open_folder,
    scrollbar_stylesheet,
)
from auto_essay_typer.gui.keymap_editor import KeymapEditor, ShortcutRow
from auto_essay_typer.gui.profile_editor import ProfileEditor
from auto_essay_typer.gui.settings_store import SettingsStore
from auto_essay_typer.gui.theme import ThemeManager
from auto_essay_typer.gui.widgets import Banner, LinkButton, make_card
from auto_essay_typer.profiles import PRESETS, Profile

#: The content column is capped so a wide window does not stretch short rows edge to edge.
COLUMN_MAX_WIDTH = 760
MAX_NAME_LENGTH = 40

#: Errors a profile reader/saver/deleter can raise for ordinary reasons (a
#: read-only folder, a hand-edited file that no longer parses).
_STORAGE_ERRORS = (OSError, ValueError, KeyError)


def validate_profile_name(name: str, user_names: Mapping[str, object] | set[str]) -> str | None:
    """Why ``name`` can't be used for a new profile, or ``None`` if it can.

    Names are compared case-insensitively against the built-in presets and the
    user's saved profiles, so "Natural" can't sit next to "natural".
    """
    if not name:
        return "Enter a name for the profile."
    if len(name) > MAX_NAME_LENGTH:
        return f"Use {MAX_NAME_LENGTH} characters or fewer."
    if not name.isprintable():
        return "The name can't contain control characters."
    folded = name.casefold()
    if any(builtin.casefold() == folded for builtin in PRESETS):
        return f"'{name}' is a built-in profile name; choose another."
    for existing in user_names:
        if existing.casefold() == folded:
            return (
                f"A profile named '{existing}' already exists. Select it and use Save to "
                "update it, or choose another name."
            )
    return None


class AdvancedPage(QWidget, ThemedMixin):
    """The Advanced page. See the module docstring for the layout.

    Public surface: ``profilesChanged`` (after a save or delete), ``reload_profiles()``,
    ``current_profile()``, ``load_profile()``, and these widgets -- ``editor``
    (the generated field form), ``field_widgets`` (one per ``Profile`` field),
    ``profile_combo``, ``save_button``, ``save_as_button``, ``delete_button``,
    ``reset_button``, ``status_label``, ``message_banner``; for shortcuts
    ``keymap_editor``, ``keymap_target_combo``, ``shortcut_rows``,
    ``restore_defaults_button``; for the files block ``locations_button``,
    ``locations_panel``, ``profiles_path_edit``, ``settings_path_edit``,
    ``open_profiles_folder_button``, ``open_settings_folder_button``.
    """

    profilesChanged = Signal()

    def __init__(
        self,
        theme_manager: ThemeManager | None,
        settings_store: SettingsStore,
        *,
        profiles_provider: Callable[[], dict[str, Profile]] = config.builtin_and_user_profiles,
        user_profiles_provider: Callable[[], dict[str, Profile]] = config.load_profiles,
        profile_saver: Callable[[str, Profile], None] = config.save_profile,
        profile_deleter: Callable[[str], bool] = config.delete_profile,
        name_prompt: Callable[[QWidget | None, str, str, str], str | None] | None = None,
        confirm: Callable[[QWidget | None, str, str], bool] | None = None,
        open_folder: Callable[[Path], None] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._store = settings_store
        self._profiles_provider = profiles_provider
        self._user_profiles_provider = user_profiles_provider
        self._profile_saver = profile_saver
        self._profile_deleter = profile_deleter
        self._name_prompt = name_prompt if name_prompt is not None else default_name_prompt
        self._confirm = confirm if confirm is not None else default_confirm
        self._open_folder = open_folder if open_folder is not None else default_open_folder

        self._profiles: dict[str, Profile] = {}
        self._user_profiles: dict[str, Profile] = {}
        self._load_problem = ""
        self._selected_name: str | None = None
        self._combo_guard = False
        #: A one-off status ("Saved 'x'.") shown until the next edit or selection.
        self._transient: tuple[str, str] | None = None

        self.editor = ProfileEditor(theme_manager)
        self.keymap_editor = KeymapEditor(theme_manager, settings_store, confirm=self._confirm)

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 12)
        root.setSpacing(12)

        title = QLabel("Advanced")
        title.setObjectName("PageTitle")
        root.addWidget(title)
        subtitle = QLabel(
            "Fine-tune how the typing looks, and which shortcuts are pressed in each editor."
        )
        subtitle.setObjectName("MutedLabel")
        subtitle.setWordWrap(True)
        root.addWidget(subtitle)

        # The card sits above the scroll area; reserving the scroll bar's width
        # on its right keeps its edge in line with the cards that scroll.
        pinned = QWidget()
        pinned_layout = QVBoxLayout(pinned)
        pinned_layout.setContentsMargins(0, 0, SCROLLBAR_WIDTH, 0)
        pinned_layout.addWidget(self._build_profile_card(theme_manager))
        root.addWidget(pinned)
        root.addWidget(self._build_scroll_area(theme_manager), 1)

        self.editor.stateChanged.connect(self._on_editor_state_changed)
        save_shortcut = QShortcut(QKeySequence(QKeySequence.StandardKey.Save), self)
        save_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        save_shortcut.activated.connect(self._on_save_shortcut)

        self._read_providers()
        self._populate_combo(self._initial_name())
        self._select(self._initial_name())
        self._show_load_problem()
        self._bind_theme(theme_manager)

    # -- construction ----------------------------------------------------------------------------

    def _build_profile_card(self, theme_manager: ThemeManager | None) -> QWidget:
        card = make_card()
        card.setMaximumWidth(COLUMN_MAX_WIDTH)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)

        picker = QHBoxLayout()
        picker.setSpacing(10)
        picker_label = QLabel("Typing profile")
        picker_label.setObjectName("SectionTitle")
        picker.addWidget(picker_label)
        self.profile_combo = NoWheelComboBox()
        self.profile_combo.setAccessibleName("Typing profile")
        self.profile_combo.currentIndexChanged.connect(self._on_combo_changed)
        picker.addWidget(self.profile_combo, 1)
        layout.addLayout(picker)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self.save_button = QPushButton("Save")
        self.save_button.setObjectName("PrimaryButton")
        self.save_button.setAccessibleName("Save profile")
        self.save_button.clicked.connect(self._on_save)
        buttons.addWidget(self.save_button)
        self.save_as_button = QPushButton("Save as…")
        self.save_as_button.setObjectName("SecondaryButton")
        self.save_as_button.setAccessibleName("Save profile as a new profile")
        self.save_as_button.clicked.connect(self._on_save_as)
        buttons.addWidget(self.save_as_button)
        self.delete_button = QPushButton("Delete")
        self.delete_button.setObjectName("DestructiveButton")
        self.delete_button.setAccessibleName("Delete profile")
        self.delete_button.clicked.connect(self._on_delete)
        buttons.addWidget(self.delete_button)
        buttons.addStretch(1)
        self.reset_button = QPushButton("Reset to defaults")
        self.reset_button.setObjectName("SecondaryButton")
        self.reset_button.setAccessibleName("Reset profile fields to defaults")
        self.reset_button.setToolTip(
            "Put every field back to this profile's built-in values (or the standard "
            "defaults for a profile of your own). Nothing is saved until you press Save."
        )
        self.reset_button.clicked.connect(self._on_reset)
        buttons.addWidget(self.reset_button)
        layout.addLayout(buttons)

        self.status_label = QLabel()
        self.status_label.setObjectName("StatusLine")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        self.message_banner = Banner("danger", theme_manager=theme_manager)
        layout.addWidget(self.message_banner)
        return card

    def _build_scroll_area(self, theme_manager: ThemeManager | None) -> QScrollArea:
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        # Not a tab stop, like the Type page's: it holds dozens of controls that are, and Qt
        # scrolls a control into view when it takes focus. A tab stop of its own would draw no
        # focus ring (only the Help page's "PageScroll" has the stylesheet rule for one).
        self.scroll_area.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # Always on (and invisible until there is something to scroll) so the
        # column width never jumps when a group is folded or unfolded.
        self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 4, 0, 12)
        layout.setSpacing(16)

        layout.addWidget(self._build_groups_heading(theme_manager))
        self.editor.setMaximumWidth(COLUMN_MAX_WIDTH)
        layout.addWidget(self.editor)
        self.keymap_editor.setMaximumWidth(COLUMN_MAX_WIDTH)
        layout.addWidget(self.keymap_editor)
        layout.addWidget(self._build_locations_block(theme_manager))
        layout.addStretch(1)

        self.scroll_area.setWidget(content)
        return self.scroll_area

    def _build_groups_heading(self, theme_manager: ThemeManager | None) -> QWidget:
        heading = QWidget()
        heading.setMaximumWidth(COLUMN_MAX_WIDTH)
        layout = QHBoxLayout(heading)
        layout.setContentsMargins(4, 0, 4, 0)
        layout.setSpacing(12)
        label = QLabel("Typing behaviour")
        label.setObjectName("SectionTitle")
        layout.addWidget(label)
        layout.addStretch(1)
        self.expand_all_button = LinkButton("Expand all", theme_manager=theme_manager)
        self.expand_all_button.setAccessibleName("Expand all groups")
        self.expand_all_button.clicked.connect(self.editor.expand_all)
        layout.addWidget(self.expand_all_button)
        self.collapse_all_button = LinkButton("Collapse all", theme_manager=theme_manager)
        self.collapse_all_button.setAccessibleName("Collapse all groups")
        self.collapse_all_button.clicked.connect(self.editor.collapse_all)
        layout.addWidget(self.collapse_all_button)
        return heading

    def _build_locations_block(self, theme_manager: ThemeManager | None) -> QWidget:
        block = QWidget()
        block.setMaximumWidth(COLUMN_MAX_WIDTH)
        layout = QVBoxLayout(block)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        # The link is inset like the "Typing behaviour" heading; the panel below it
        # is a card and lines up with the other cards.
        link_row = QHBoxLayout()
        link_row.setContentsMargins(4, 0, 4, 0)
        self.locations_button = LinkButton("Show file locations", theme_manager=theme_manager)
        self.locations_button.setAccessibleName("Show file locations")
        self.locations_button.clicked.connect(self._toggle_locations)
        link_row.addWidget(self.locations_button, 0, Qt.AlignmentFlag.AlignLeft)
        link_row.addStretch(1)
        layout.addLayout(link_row)

        self.locations_panel = make_card()
        panel = QVBoxLayout(self.locations_panel)
        panel.setContentsMargins(16, 12, 16, 14)
        panel.setSpacing(8)
        note = QLabel(
            "These are the files the app reads. Both are plain text, so you can back them up "
            "or edit them by hand while the app is closed."
        )
        note.setObjectName("MutedLabel")
        note.setWordWrap(True)
        panel.addWidget(note)
        self.profiles_path_edit, self.open_profiles_folder_button = self._add_path_row(
            panel, "Typing profiles"
        )
        self.settings_path_edit, self.open_settings_folder_button = self._add_path_row(
            panel, "App settings"
        )
        self.open_profiles_folder_button.clicked.connect(
            lambda: self._open_folder(config.config_path().parent)
        )
        self.open_settings_folder_button.clicked.connect(
            lambda: self._open_folder(app_settings.settings_path().parent)
        )
        self.locations_panel.setVisible(False)
        layout.addWidget(self.locations_panel)
        return block

    @staticmethod
    def _add_path_row(layout: QVBoxLayout, title: str) -> tuple[QLineEdit, QPushButton]:
        caption = QLabel(title)
        caption.setObjectName("SectionTitle")
        layout.addWidget(caption)
        row = QHBoxLayout()
        row.setSpacing(8)
        edit = QLineEdit()
        edit.setReadOnly(True)
        edit.setAccessibleName(f"{title} file location")
        row.addWidget(edit, 1)
        button = QPushButton("Open folder")
        button.setObjectName("SecondaryButton")
        button.setAccessibleName(f"Open the {title.lower()} folder")
        row.addWidget(button)
        layout.addLayout(row)
        return edit, button

    # -- public API --------------------------------------------------------------------------------------

    @property
    def field_widgets(self) -> dict[str, QWidget]:
        """The control for every ``Profile`` field, keyed by field name."""
        return self.editor.field_widgets

    @property
    def keymap_target_combo(self) -> QWidget:
        return self.keymap_editor.target_combo

    @property
    def shortcut_rows(self) -> dict[FormatOp, ShortcutRow]:
        return self.keymap_editor.rows

    @property
    def restore_defaults_button(self) -> QPushButton:
        return self.keymap_editor.restore_button

    def current_profile(self) -> Profile:
        """The profile the editor describes now; ``ValueError`` if any field is invalid."""
        return self.editor.current_profile()

    def load_profile(self, profile: Profile) -> None:
        """Fill the editor with ``profile`` as if it were the saved state of the selected profile."""
        self.editor.load_profile(profile)

    def selected_profile_name(self) -> str | None:
        return self._selected_name

    def reload_profiles(self) -> None:
        """Re-read the profile lists, keeping the selection (and unsaved edits) if it still exists."""
        previous = self._selected_name
        dirty = self.editor.is_dirty()
        self._read_providers()
        if previous is not None and previous in self._profiles:
            self._populate_combo(previous)
            if dirty:
                self._refresh_actions()
            else:
                self._select(previous)  # pick up whatever changed on disk
        else:
            self._populate_combo(self._initial_name())
            self._select(self._initial_name())
        self._show_load_problem()

    # -- reading the profile lists ----------------------------------------------------------------------------------

    def _read_providers(self) -> None:
        self._load_problem = ""
        try:
            self._profiles = dict(self._profiles_provider())
        except Exception as exc:  # noqa: BLE001 - a hand-edited profiles file may be malformed
            self._profiles = dict(PRESETS)
            self._load_problem = (
                f"Your saved profiles could not be read ({exc}). Showing the built-in "
                "profiles only."
            )
        try:
            self._user_profiles = dict(self._user_profiles_provider())
        except Exception:  # noqa: BLE001 - reported once, above
            self._user_profiles = {}

    def _show_load_problem(self) -> None:
        if self._load_problem:
            self._show_banner(self._load_problem, "warning")

    def _is_pure_builtin(self, name: str | None) -> bool:
        return name in PRESETS and name not in self._user_profiles

    def _initial_name(self) -> str | None:
        preferred = self._store.settings.profile_name
        if preferred in self._profiles:
            return preferred
        if "natural" in self._profiles:
            return "natural"
        names = sorted(self._profiles)
        return names[0] if names else None

    # -- the profile combo ------------------------------------------------------------------------------------------------

    def _populate_combo(self, select: str | None) -> None:
        """Refill the combo from the loaded lists and show ``select`` (without firing handlers)."""
        self._combo_guard = True
        try:
            self.profile_combo.clear()
            for name in sorted(self._profiles):
                label = f"{name}  (Built-in)" if self._is_pure_builtin(name) else name
                self.profile_combo.addItem(label, name)
            index = self.profile_combo.findData(select) if select is not None else -1
            if index >= 0:
                self.profile_combo.setCurrentIndex(index)
        finally:
            self._combo_guard = False

    def _set_combo_to(self, name: str | None) -> None:
        self._combo_guard = True
        try:
            index = self.profile_combo.findData(name) if name is not None else -1
            if index >= 0:
                self.profile_combo.setCurrentIndex(index)
        finally:
            self._combo_guard = False

    def _on_combo_changed(self, _index: int) -> None:
        if self._combo_guard:
            return
        new_name = self.profile_combo.currentData()
        if new_name == self._selected_name:
            return
        if self.editor.is_dirty() and not self._confirm(
            self,
            "Discard changes?",
            f"'{self._selected_name}' has unsaved changes. Discard them and switch profile?",
        ):
            self._set_combo_to(self._selected_name)
            return
        self._select(new_name)

    def _select(self, name: str | None) -> None:
        """Make ``name`` the selected profile and load its saved values into the editor."""
        self._selected_name = name
        profile = self._profiles.get(name) if name is not None else None
        self._transient = None
        self._show_banner(None)
        self.editor.load_profile(profile if profile is not None else Profile())
        self._refresh_actions()

    # -- buttons: enabled state and the status line ------------------------------------------------------------------------------

    def _on_editor_state_changed(self) -> None:
        self._transient = None
        self._refresh_actions()

    def _refresh_actions(self) -> None:
        name = self._selected_name
        is_user = name in self._user_profiles
        pure_builtin = self._is_pure_builtin(name)
        valid = self.editor.is_valid()
        dirty = self.editor.is_dirty()
        summary = self.editor.error_summary()

        self.save_button.setEnabled(is_user and dirty and valid)
        if pure_builtin:
            self.save_button.setToolTip("Built-in profiles can't be changed. Use Save as…")
        elif not valid:
            self.save_button.setToolTip(summary)
        elif not dirty:
            self.save_button.setToolTip("There are no changes to save.")
        else:
            self.save_button.setToolTip("Save your changes (Ctrl+S)")

        self.save_as_button.setEnabled(valid)
        self.save_as_button.setToolTip(
            "Keep these settings as a profile of your own" if valid else summary
        )

        self.delete_button.setEnabled(is_user)
        if pure_builtin:
            self.delete_button.setToolTip("Built-in profiles can't be deleted.")
        elif name in PRESETS:
            self.delete_button.setToolTip(
                "Remove your customised version; the built-in profile comes back."
            )
        else:
            self.delete_button.setToolTip("Delete this profile")

        text, tone = self._status(pure_builtin, is_user, valid, dirty, summary, name)
        self.status_label.setText(text)
        self.status_label.setProperty("tone", tone)
        self.status_label.style().unpolish(self.status_label)
        self.status_label.style().polish(self.status_label)

    def _status(
        self,
        pure_builtin: bool,
        is_user: bool,
        valid: bool,
        dirty: bool,
        summary: str,
        name: str | None,
    ) -> tuple[str, str]:
        if self._transient is not None:
            return self._transient
        if not valid:
            return summary, "danger"
        if dirty:
            if pure_builtin:
                return (
                    "Unsaved changes. Built-in profiles can't be changed, so use Save as… "
                    "to keep them.",
                    "accent",
                )
            return "Unsaved changes.", "accent"
        if pure_builtin:
            return (
                "Built-in profile. Change anything, then use Save as… to make it your own.",
                "muted",
            )
        if is_user and name in PRESETS:
            return "Your customised version of a built-in profile.", "muted"
        return "Saved profile.", "muted"

    def _show_banner(self, text: str | None, tone: str = "danger") -> None:
        self.message_banner.set_message(text or "", tone)

    # -- Save -----------------------------------------------------------------------------------------------------------------------------

    def _on_save_shortcut(self) -> None:
        if self.save_button.isEnabled():
            self._on_save()

    def _on_save(self) -> None:
        name = self._selected_name
        if name is None or name not in self._user_profiles:
            return
        try:
            profile = self.editor.current_profile()
        except ValueError as exc:
            self._show_banner(str(exc))
            return
        try:
            self._profile_saver(name, profile)
        except _STORAGE_ERRORS as exc:
            self._show_banner(f"Could not save '{name}': {exc}")
            return
        self._show_banner(None)
        self._after_profile_change(name)
        self.editor.mark_saved()
        self._transient = (f"Saved '{name}'.", "success")
        self._refresh_actions()
        self.profilesChanged.emit()

    def _on_save_as(self) -> None:
        try:
            profile = self.editor.current_profile()
        except ValueError as exc:
            self._show_banner(str(exc))
            return
        entered = self._name_prompt(
            self, "Save profile as", "Name for this profile", self._suggest_name()
        )
        if entered is None:
            return
        name = entered.strip()
        problem = validate_profile_name(name, self._user_profiles)
        if problem is not None:
            self._show_banner(problem)
            return
        try:
            self._profile_saver(name, profile)
        except _STORAGE_ERRORS as exc:
            self._show_banner(f"Could not save '{name}': {exc}")
            return
        self._show_banner(None)
        self._after_profile_change(name)
        self.editor.mark_saved()
        self._transient = (f"Saved as '{name}'.", "success")
        self._refresh_actions()
        self.profilesChanged.emit()

    def _after_profile_change(self, select: str) -> None:
        """Re-read the lists after a save and keep ``select`` selected, leaving the editor as it is."""
        self._read_providers()
        self._populate_combo(select)
        self._selected_name = select

    def _suggest_name(self) -> str:
        base = f"{self._selected_name or 'my profile'} copy"
        taken = {name.casefold() for name in list(self._profiles) + list(self._user_profiles)}
        candidate, number = base, 2
        while candidate.casefold() in taken:
            candidate = f"{base} {number}"
            number += 1
        return candidate

    # -- Delete ---------------------------------------------------------------------------------------------------------------------------

    def _on_delete(self) -> None:
        name = self._selected_name
        if name is None or name not in self._user_profiles:
            return
        if name in PRESETS:
            question = (
                f"Remove your customised '{name}'? The built-in '{name}' profile will be used "
                "again."
            )
        else:
            question = f"Delete the profile '{name}'? This can't be undone."
        if not self._confirm(self, "Delete profile", question):
            return
        try:
            self._profile_deleter(name)
        except _STORAGE_ERRORS as exc:
            self._show_banner(f"Could not delete '{name}': {exc}")
            return
        self._read_providers()
        target = name if name in self._profiles else self._initial_name()
        self._populate_combo(target)
        self._select(target)
        restored = name in PRESETS
        self._transient = (
            (f"Restored the built-in '{name}'.", "success")
            if restored
            else (f"Deleted '{name}'.", "success")
        )
        self._refresh_actions()
        self.profilesChanged.emit()

    # -- Reset ----------------------------------------------------------------------------------------------------------------------------

    def _on_reset(self) -> None:
        name = self._selected_name
        defaults = PRESETS[name] if name in PRESETS else Profile()
        self.editor.load_profile(defaults, baseline=False)  # dirty only if it differs from what is saved
        self._transient = None
        self._refresh_actions()

    # -- file locations ---------------------------------------------------------------------------------------------------------------------------

    def _toggle_locations(self) -> None:
        reveal = not self.locations_panel.isVisibleTo(self)
        if reveal:
            self.profiles_path_edit.setText(str(config.config_path()))
            self.settings_path_edit.setText(str(app_settings.settings_path()))
        self.locations_panel.setVisible(reveal)
        label = "Hide file locations" if reveal else "Show file locations"
        self.locations_button.setText(label)
        self.locations_button.setAccessibleName(label)

    # -- theming ------------------------------------------------------------------------------------------------------------------------------------

    def _restyle(self) -> None:
        t = self.tokens
        self.setStyleSheet(
            f"""
            QLabel#StatusLine {{ font-size: 12px; color: {t.muted_text}; }}
            QLabel#StatusLine[tone="danger"] {{ color: {t.danger}; font-weight: 600; }}
            QLabel#StatusLine[tone="success"] {{ color: {t.success}; font-weight: 600; }}
            QLabel#StatusLine[tone="accent"] {{ color: {t.accent}; font-weight: 600; }}
            """
            + scrollbar_stylesheet(t)
        )
