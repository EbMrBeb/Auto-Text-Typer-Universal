"""Tests for the Advanced page (``gui/pages/advanced_page.py``).

Profile storage, the name prompt, confirmations and "open folder" are all
injected, so nothing here blocks on a dialog or touches a real file; one test
at the end runs the page against the real ``config`` functions with
``AUTO_TEXT_TYPER_CONFIG`` pointing inside ``tmp_path`` (set by the autouse
fixture in ``conftest.py``) to prove the default wiring.
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QInputDialog, QMessageBox

from auto_essay_typer import app_settings, config, platforms, targets
from auto_essay_typer.actions import FormatOp
from auto_essay_typer.gui.advanced_common import (
    default_confirm,
    default_name_prompt,
    default_open_folder,
)
from auto_essay_typer.gui.pages.advanced_page import (
    MAX_NAME_LENGTH,
    AdvancedPage,
    validate_profile_name,
)
from auto_essay_typer.gui.settings_store import SettingsStore
from auto_essay_typer.gui.theme import DARK_TOKENS, LIGHT_TOKENS, ThemeManager, ThemeMode
from auto_essay_typer.platforms import OperatingSystem
from auto_essay_typer.profiles import PRESETS, Profile, field_names

BUILTIN_SAVE_TIP = "Built-in profiles can't be changed. Use Save as…"


@pytest.fixture(autouse=True)
def _delete_closed_widgets():
    """Actually delete the widgets pytest-qt closed at the end of each test.

    pytest-qt closes them with ``deleteLater()``, but nothing runs the deferred
    delete between tests, so full-size pages pile up -- and every
    ``ThemeManager`` (which re-polishes every live widget when it applies its
    stylesheet) gets slower with each one.
    """
    yield
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)


# --- fakes ---------------------------------------------------------------------------------------


class FakeProfiles:
    """An in-memory stand-in for the profile file: the four injectable callables."""

    def __init__(self, user: dict[str, Profile] | None = None) -> None:
        self.user: dict[str, Profile] = dict(user or {})
        self.saved: list[tuple[str, Profile]] = []
        self.deleted: list[str] = []
        self.save_error: Exception | None = None
        self.delete_error: Exception | None = None
        self.read_error: Exception | None = None

    def all(self) -> dict[str, Profile]:
        if self.read_error is not None:
            raise self.read_error
        merged = dict(PRESETS)
        merged.update(self.user)
        return merged

    def users(self) -> dict[str, Profile]:
        if self.read_error is not None:
            raise self.read_error
        return dict(self.user)

    def save(self, name: str, profile: Profile) -> None:
        if self.save_error is not None:
            raise self.save_error
        self.user[name] = profile
        self.saved.append((name, profile))

    def delete(self, name: str) -> bool:
        if self.delete_error is not None:
            raise self.delete_error
        self.deleted.append(name)
        return self.user.pop(name, None) is not None


class Prompt:
    """Stand-in for the "Save as..." name dialog; returns ``answer`` and records the call."""

    def __init__(self, answer: str | None = None) -> None:
        self.answer = answer
        self.calls: list[tuple[str, str, str]] = []

    def __call__(self, parent, title: str, label: str, initial: str) -> str | None:
        self.calls.append((title, label, initial))
        return self.answer


class Confirm:
    def __init__(self, answer: bool = True) -> None:
        self.answer = answer
        self.calls: list[tuple[str, str]] = []

    def __call__(self, parent, title: str, text: str) -> bool:
        self.calls.append((title, text))
        return self.answer


class Opener:
    def __init__(self) -> None:
        self.paths: list = []

    def __call__(self, path) -> None:
        self.paths.append(path)


def _make_page(
    qtbot,
    qapp,
    tmp_path,
    *,
    backend: FakeProfiles | None = None,
    prompt: Prompt | None = None,
    confirm: Confirm | None = None,
    opener: Opener | None = None,
    store: SettingsStore | None = None,
    theme_manager: ThemeManager | None = None,
):
    backend = backend if backend is not None else FakeProfiles()
    page = AdvancedPage(
        theme_manager if theme_manager is not None else ThemeManager(qapp),
        store if store is not None else SettingsStore(tmp_path / "settings.json"),
        profiles_provider=backend.all,
        user_profiles_provider=backend.users,
        profile_saver=backend.save,
        profile_deleter=backend.delete,
        name_prompt=prompt if prompt is not None else Prompt(None),
        confirm=confirm if confirm is not None else Confirm(True),
        open_folder=opener if opener is not None else Opener(),
    )
    qtbot.addWidget(page)
    return page, backend


def _wpm(page: AdvancedPage):
    return page.editor.rows["wpm"].spin_boxes()[0]


def _select(page: AdvancedPage, name: str) -> None:
    page.profile_combo.setCurrentIndex(page.profile_combo.findData(name))


def _combo_names(page: AdvancedPage) -> list[str]:
    return [page.profile_combo.itemData(i) for i in range(page.profile_combo.count())]


# --- composition --------------------------------------------------------------------------------------


def test_the_page_builds_a_control_for_every_profile_field(qtbot, qapp, tmp_path):
    page, _backend = _make_page(qtbot, qapp, tmp_path)
    assert set(page.field_widgets) == set(field_names())


def test_the_page_has_a_title_and_the_three_blocks(qtbot, qapp, tmp_path):
    page, _backend = _make_page(qtbot, qapp, tmp_path)
    from PySide6.QtWidgets import QLabel

    texts = [label.text() for label in page.findChildren(QLabel)]
    assert "Advanced" in texts
    assert "Typing profile" in texts
    assert "Keyboard shortcuts" in texts
    assert page.locations_button.text() == "Show file locations"


def test_expand_all_and_collapse_all_links_fold_every_group(qtbot, qapp, tmp_path):
    page, _ = _make_page(qtbot, qapp, tmp_path)
    sections = list(page.editor.group_sections.values())
    page.expand_all_button.click()
    assert all(section.is_expanded() for section in sections)
    page.collapse_all_button.click()
    assert not any(section.is_expanded() for section in sections)


def test_the_page_copes_with_no_profiles_at_all(qtbot, qapp, tmp_path):
    page = AdvancedPage(
        ThemeManager(qapp),
        SettingsStore(tmp_path / "settings.json"),
        profiles_provider=lambda: {},
        user_profiles_provider=lambda: {},
        profile_saver=lambda name, profile: None,
        profile_deleter=lambda name: False,
        name_prompt=Prompt(None),
        confirm=Confirm(True),
        open_folder=Opener(),
    )
    qtbot.addWidget(page)
    assert page.profile_combo.count() == 0
    assert page.selected_profile_name() is None
    assert page.current_profile() == Profile()  # the editor still shows the standard defaults
    assert page.save_button.isEnabled() is False
    assert page.delete_button.isEnabled() is False
    assert page.save_as_button.isEnabled() is True


def test_the_content_scrolls_vertically_and_never_horizontally(qtbot, qapp, tmp_path):
    from PySide6.QtCore import Qt

    page, _backend = _make_page(qtbot, qapp, tmp_path)
    assert page.scroll_area.horizontalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    assert page.scroll_area.verticalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOn
    assert page.scroll_area.widgetResizable()


def test_the_content_fits_the_narrowest_window_without_horizontal_scrolling(qtbot, qapp, tmp_path):
    # The main window is at least 820 px wide with a 220 px sidebar: the page gets ~600 px.
    page, _backend = _make_page(qtbot, qapp, tmp_path)
    page.editor.expand_all()
    page.resize(600, 600)
    page.show()
    qtbot.waitExposed(page)
    content = page.scroll_area.widget()
    assert content.minimumSizeHint().width() <= page.scroll_area.viewport().width()
    assert page.minimumSizeHint().width() <= 600


# --- the profile combo ---------------------------------------------------------------------------------------


def test_the_combo_lists_profiles_sorted_with_built_ins_marked(qtbot, qapp, tmp_path):
    backend = FakeProfiles({"zeta": Profile(), "alpha": Profile()})
    page, _ = _make_page(qtbot, qapp, tmp_path, backend=backend)
    assert _combo_names(page) == sorted(["alpha", "zeta", *PRESETS])
    labels = {page.profile_combo.itemData(i): page.profile_combo.itemText(i) for i in range(page.profile_combo.count())}
    assert "(Built-in)" in labels["natural"]
    assert "(Built-in)" in labels["clean"]
    assert labels["alpha"] == "alpha"


def test_a_user_profile_that_shadows_a_built_in_is_not_marked_built_in(qtbot, qapp, tmp_path):
    backend = FakeProfiles({"natural": PRESETS["natural"].with_overrides(wpm=99.0)})
    page, _ = _make_page(qtbot, qapp, tmp_path, backend=backend)
    index = page.profile_combo.findData("natural")
    assert "(Built-in)" not in page.profile_combo.itemText(index)
    assert "(Built-in)" in page.profile_combo.itemText(page.profile_combo.findData("clean"))


def test_the_combo_starts_on_the_saved_profile_name(qtbot, qapp, tmp_path):
    store = SettingsStore(tmp_path / "settings.json")
    store.update(profile_name="clean")
    page, _ = _make_page(qtbot, qapp, tmp_path, store=store)
    assert page.profile_combo.currentData() == "clean"
    assert page.selected_profile_name() == "clean"
    assert page.current_profile() == PRESETS["clean"]


def test_the_combo_falls_back_to_natural_when_the_saved_name_is_gone(qtbot, qapp, tmp_path):
    store = SettingsStore(tmp_path / "settings.json")
    store.update(profile_name="deleted-long-ago")
    page, _ = _make_page(qtbot, qapp, tmp_path, store=store)
    assert page.profile_combo.currentData() == "natural"


def test_selecting_a_profile_loads_its_values_exactly(qtbot, qapp, tmp_path):
    page, _ = _make_page(qtbot, qapp, tmp_path)
    _select(page, "clean")
    assert page.current_profile() == PRESETS["clean"]
    _select(page, "sloppy")
    assert page.current_profile() == PRESETS["sloppy"]
    assert not page.editor.is_dirty()


def test_loading_a_profile_into_the_page_round_trips(qtbot, qapp, tmp_path):
    page, _ = _make_page(qtbot, qapp, tmp_path)
    awkward = Profile(revision_rate_per_word=1 / 800, fatigue_tau_ms=1_200_000.0, hesitation_probability=0.035)
    page.load_profile(awkward)
    assert page.current_profile() == awkward


# --- buttons for a built-in -------------------------------------------------------------------------------------


def test_a_built_in_profile_cannot_be_saved_or_deleted(qtbot, qapp, tmp_path):
    page, backend = _make_page(qtbot, qapp, tmp_path)
    _wpm(page).setValue(90)

    assert page.save_button.isEnabled() is False
    assert page.save_button.toolTip() == BUILTIN_SAVE_TIP
    assert page.delete_button.isEnabled() is False
    assert page.save_as_button.isEnabled() is True
    assert page.reset_button.isEnabled() is True

    page._on_save()  # even if triggered some other way
    page._on_delete()
    assert backend.saved == [] and backend.deleted == []


def test_the_status_line_describes_the_state(qtbot, qapp, tmp_path):
    page, _ = _make_page(qtbot, qapp, tmp_path)
    assert "Built-in profile" in page.status_label.text()
    _wpm(page).setValue(90)
    assert "Unsaved changes" in page.status_label.text()
    assert "Save as" in page.status_label.text()
    assert page.status_label.property("tone") == "accent"


def test_the_page_reports_invalid_settings_and_blocks_saving(qtbot, qapp, tmp_path):
    page, _ = _make_page(qtbot, qapp, tmp_path)
    page.editor.rows["burst_range"].spin_boxes()[0].setValue(1.6)  # above the high end, 1.25

    assert page.save_as_button.isEnabled() is False
    assert page.save_button.isEnabled() is False
    assert page.status_label.text() == "Fix 1 setting before saving"
    assert page.status_label.property("tone") == "danger"
    assert page.save_as_button.toolTip() == "Fix 1 setting before saving"


def test_fixing_the_problem_re_enables_saving(qtbot, qapp, tmp_path):
    page, _ = _make_page(qtbot, qapp, tmp_path)
    burst_low = page.editor.rows["burst_range"].spin_boxes()[0]
    burst_low.setValue(1.6)
    burst_low.setValue(0.9)
    assert page.save_as_button.isEnabled() is True
    assert "Unsaved changes" in page.status_label.text()


# --- Save as... --------------------------------------------------------------------------------------------------


def test_save_as_saves_selects_and_announces(qtbot, qapp, tmp_path):
    prompt = Prompt("fast voice")
    page, backend = _make_page(qtbot, qapp, tmp_path, prompt=prompt)
    _wpm(page).setValue(90)

    with qtbot.waitSignal(page.profilesChanged, timeout=1000):
        page.save_as_button.click()

    assert backend.saved == [("fast voice", PRESETS["natural"].with_overrides(wpm=90.0))]
    assert prompt.calls == [("Save profile as", "Name for this profile", "natural copy")]
    assert page.profile_combo.currentData() == "fast voice"
    assert page.selected_profile_name() == "fast voice"
    assert "(Built-in)" not in page.profile_combo.currentText()
    assert page.message_banner.isHidden()
    assert page.status_label.text() == "Saved as 'fast voice'."
    assert page.status_label.property("tone") == "success"
    # The new profile is now a saved user profile: clean, deletable, and Save waits for a change.
    assert not page.editor.is_dirty()
    assert page.delete_button.isEnabled() is True
    assert page.save_button.isEnabled() is False


def test_save_as_trims_the_name(qtbot, qapp, tmp_path):
    page, backend = _make_page(qtbot, qapp, tmp_path, prompt=Prompt("   spaced out  "))
    page.save_as_button.click()
    assert [name for name, _profile in backend.saved] == ["spaced out"]


def test_save_as_can_be_cancelled(qtbot, qapp, tmp_path):
    page, backend = _make_page(qtbot, qapp, tmp_path, prompt=Prompt(None))
    with qtbot.assertNotEmitted(page.profilesChanged):
        page.save_as_button.click()
    assert backend.saved == []
    assert page.profile_combo.currentData() == "natural"


@pytest.mark.parametrize("typed", ["natural", "Natural", "CLEAN", " sloppy "])
def test_save_as_refuses_a_built_in_name(qtbot, qapp, tmp_path, typed):
    page, backend = _make_page(qtbot, qapp, tmp_path, prompt=Prompt(typed))
    with qtbot.assertNotEmitted(page.profilesChanged):
        page.save_as_button.click()
    assert backend.saved == []
    assert not page.message_banner.isHidden()
    assert "is a built-in profile name; choose another" in page.message_banner.message()
    assert page.message_banner.tone() == "danger"


def test_save_as_refuses_a_name_that_already_exists(qtbot, qapp, tmp_path):
    backend = FakeProfiles({"mine": Profile()})
    page, _ = _make_page(qtbot, qapp, tmp_path, backend=backend, prompt=Prompt("Mine"))
    page.save_as_button.click()
    assert backend.saved == []
    assert "already exists" in page.message_banner.message()


@pytest.mark.parametrize(
    ("typed", "fragment"),
    [("", "Enter a name"), ("   ", "Enter a name"), ("x" * (MAX_NAME_LENGTH + 1), "characters or fewer"), ("bad\tname", "control characters")],
)
def test_save_as_refuses_unusable_names(qtbot, qapp, tmp_path, typed, fragment):
    page, backend = _make_page(qtbot, qapp, tmp_path, prompt=Prompt(typed))
    page.save_as_button.click()
    assert backend.saved == []
    assert fragment in page.message_banner.message()


def test_save_as_reports_a_storage_failure(qtbot, qapp, tmp_path):
    backend = FakeProfiles()
    backend.save_error = OSError("read-only file system")
    page, _ = _make_page(qtbot, qapp, tmp_path, backend=backend, prompt=Prompt("new one"))
    with qtbot.assertNotEmitted(page.profilesChanged):
        page.save_as_button.click()
    assert "Could not save 'new one': read-only file system" in page.message_banner.message()
    assert page.profile_combo.currentData() == "natural"  # nothing was selected away


def test_save_as_suggests_a_name_that_is_not_taken(qtbot, qapp, tmp_path):
    backend = FakeProfiles({"natural copy": Profile()})
    prompt = Prompt(None)
    page, _ = _make_page(qtbot, qapp, tmp_path, backend=backend, prompt=prompt)
    page.save_as_button.click()
    assert prompt.calls[0][2] == "natural copy 2"


def test_validate_profile_name_directly():
    assert validate_profile_name("good name", {}) is None
    assert validate_profile_name("x" * MAX_NAME_LENGTH, {}) is None
    assert validate_profile_name("natural", {}) == "'natural' is a built-in profile name; choose another."
    assert "already exists" in validate_profile_name("MINE", {"mine": Profile()})
    assert validate_profile_name("", {}) == "Enter a name for the profile."


# --- Save ---------------------------------------------------------------------------------------------------------


def test_save_overwrites_the_selected_user_profile(qtbot, qapp, tmp_path):
    mine = Profile(wpm=70.0)
    backend = FakeProfiles({"mine": mine})
    page, _ = _make_page(qtbot, qapp, tmp_path, backend=backend)
    _select(page, "mine")
    assert page.save_button.isEnabled() is False  # nothing changed yet
    assert page.save_button.toolTip() == "There are no changes to save."

    _wpm(page).setValue(95)
    assert page.save_button.isEnabled() is True
    with qtbot.waitSignal(page.profilesChanged, timeout=1000):
        page.save_button.click()

    assert backend.saved == [("mine", mine.with_overrides(wpm=95.0))]
    assert not page.editor.is_dirty()
    assert page.status_label.text() == "Saved 'mine'."
    assert page.save_button.isEnabled() is False


def test_a_later_edit_clears_the_saved_message(qtbot, qapp, tmp_path):
    backend = FakeProfiles({"mine": Profile()})
    page, _ = _make_page(qtbot, qapp, tmp_path, backend=backend)
    _select(page, "mine")
    _wpm(page).setValue(95)
    page.save_button.click()
    assert page.status_label.text() == "Saved 'mine'."
    _wpm(page).setValue(100)
    assert page.status_label.text() == "Unsaved changes."


def test_save_failure_is_shown_and_the_edit_is_kept(qtbot, qapp, tmp_path):
    backend = FakeProfiles({"mine": Profile()})
    backend.save_error = OSError("permission denied")
    page, _ = _make_page(qtbot, qapp, tmp_path, backend=backend)
    _select(page, "mine")
    _wpm(page).setValue(95)
    with qtbot.assertNotEmitted(page.profilesChanged):
        page.save_button.click()
    assert "Could not save 'mine': permission denied" in page.message_banner.message()
    assert page.editor.is_dirty()
    assert backend.saved == []


def test_a_malformed_profile_file_error_is_shown_not_raised(qtbot, qapp, tmp_path):
    backend = FakeProfiles({"mine": Profile()})
    backend.save_error = ValueError("malformed TOML in /somewhere/profiles.toml: oops")
    page, _ = _make_page(qtbot, qapp, tmp_path, backend=backend)
    _select(page, "mine")
    _wpm(page).setValue(95)
    page.save_button.click()
    assert "malformed TOML" in page.message_banner.message()


def test_ctrl_s_saves_when_there_is_something_to_save(qtbot, qapp, tmp_path):
    backend = FakeProfiles({"mine": Profile()})
    page, _ = _make_page(qtbot, qapp, tmp_path, backend=backend)
    _select(page, "mine")
    save_shortcut = next(
        s for s in page.findChildren(QShortcut) if s.key() == QKeySequence(QKeySequence.StandardKey.Save)
    )

    save_shortcut.activated.emit()
    assert backend.saved == []  # clean: nothing to save
    _wpm(page).setValue(95)
    save_shortcut.activated.emit()
    assert [name for name, _p in backend.saved] == ["mine"]


# --- Delete ----------------------------------------------------------------------------------------------------------


def test_deleting_a_user_profile_asks_first_then_falls_back(qtbot, qapp, tmp_path):
    confirm = Confirm(True)
    backend = FakeProfiles({"mine": Profile(wpm=70.0)})
    page, _ = _make_page(qtbot, qapp, tmp_path, backend=backend, confirm=confirm)
    _select(page, "mine")
    assert page.delete_button.isEnabled() is True

    with qtbot.waitSignal(page.profilesChanged, timeout=1000):
        page.delete_button.click()

    assert len(confirm.calls) == 1
    assert "mine" in confirm.calls[0][1] and "can't be undone" in confirm.calls[0][1]
    assert backend.deleted == ["mine"]
    assert "mine" not in _combo_names(page)
    assert page.profile_combo.currentData() == "natural"
    assert page.status_label.text() == "Deleted 'mine'."


def test_declining_the_delete_confirmation_keeps_the_profile(qtbot, qapp, tmp_path):
    confirm = Confirm(False)
    backend = FakeProfiles({"mine": Profile()})
    page, _ = _make_page(qtbot, qapp, tmp_path, backend=backend, confirm=confirm)
    _select(page, "mine")
    with qtbot.assertNotEmitted(page.profilesChanged):
        page.delete_button.click()
    assert backend.deleted == []
    assert "mine" in _combo_names(page)
    assert page.profile_combo.currentData() == "mine"


def test_deleting_a_customised_built_in_restores_the_built_in(qtbot, qapp, tmp_path):
    confirm = Confirm(True)
    backend = FakeProfiles({"natural": PRESETS["natural"].with_overrides(wpm=99.0)})
    page, _ = _make_page(qtbot, qapp, tmp_path, backend=backend, confirm=confirm)
    assert page.profile_combo.currentData() == "natural"
    assert page.current_profile().wpm == 99.0
    assert page.delete_button.isEnabled() is True
    assert "built-in" in page.delete_button.toolTip().lower()

    with qtbot.waitSignal(page.profilesChanged, timeout=1000):
        page.delete_button.click()

    assert "built-in" in confirm.calls[0][1].lower()
    assert page.profile_combo.currentData() == "natural"
    assert "(Built-in)" in page.profile_combo.currentText()
    assert page.current_profile() == PRESETS["natural"]
    assert page.status_label.text() == "Restored the built-in 'natural'."
    assert page.delete_button.isEnabled() is False


def test_delete_failure_is_shown(qtbot, qapp, tmp_path):
    backend = FakeProfiles({"mine": Profile()})
    backend.delete_error = OSError("busy")
    page, _ = _make_page(qtbot, qapp, tmp_path, backend=backend)
    _select(page, "mine")
    with qtbot.assertNotEmitted(page.profilesChanged):
        page.delete_button.click()
    assert "Could not delete 'mine': busy" in page.message_banner.message()
    assert page.profile_combo.currentData() == "mine"


# --- Reset to defaults -----------------------------------------------------------------------------------------------------


def test_reset_puts_a_built_in_back_to_its_own_values(qtbot, qapp, tmp_path):
    page, backend = _make_page(qtbot, qapp, tmp_path)
    _select(page, "clean")
    _wpm(page).setValue(120)
    assert page.editor.is_dirty()

    page.reset_button.click()
    assert page.current_profile() == PRESETS["clean"]
    assert not page.editor.is_dirty()
    assert backend.saved == []  # reset never writes anything


def test_reset_on_your_own_profile_goes_to_the_standard_defaults_and_stays_unsaved(qtbot, qapp, tmp_path):
    saved_version = Profile(wpm=90.0, typo_rate_per_100=2.0)
    backend = FakeProfiles({"mine": saved_version})
    page, _ = _make_page(qtbot, qapp, tmp_path, backend=backend)
    _select(page, "mine")
    assert page.current_profile() == saved_version

    page.reset_button.click()

    assert page.current_profile() == Profile()
    assert page.editor.is_dirty()  # differs from what is saved
    assert "Unsaved changes" in page.status_label.text()
    assert page.save_button.isEnabled() is True
    assert backend.saved == []
    # Selecting the profile again discards the reset (nothing was written).
    page._select("mine")
    assert page.current_profile() == saved_version


def test_reset_on_a_profile_already_at_the_defaults_is_not_dirty(qtbot, qapp, tmp_path):
    backend = FakeProfiles({"mine": Profile()})
    page, _ = _make_page(qtbot, qapp, tmp_path, backend=backend)
    _select(page, "mine")
    _wpm(page).setValue(120)
    page.reset_button.click()
    assert not page.editor.is_dirty()


def test_reset_on_a_customised_built_in_uses_the_built_in_values(qtbot, qapp, tmp_path):
    backend = FakeProfiles({"natural": PRESETS["natural"].with_overrides(wpm=99.0)})
    page, _ = _make_page(qtbot, qapp, tmp_path, backend=backend)
    page.reset_button.click()
    assert page.current_profile() == PRESETS["natural"]
    assert page.editor.is_dirty()  # the saved customised version is 99 wpm


# --- switching profiles with unsaved edits ---------------------------------------------------------------------------------


def test_switching_away_from_unsaved_edits_asks_and_can_be_refused(qtbot, qapp, tmp_path):
    confirm = Confirm(False)
    page, _ = _make_page(qtbot, qapp, tmp_path, confirm=confirm)
    _wpm(page).setValue(120)

    _select(page, "clean")

    assert len(confirm.calls) == 1
    assert "unsaved" in confirm.calls[0][1].lower()
    assert page.profile_combo.currentData() == "natural"  # reverted
    assert page.selected_profile_name() == "natural"
    assert page.editor.is_dirty()
    assert _wpm(page).value() == 120


def test_switching_away_from_unsaved_edits_discards_them_when_confirmed(qtbot, qapp, tmp_path):
    confirm = Confirm(True)
    page, _ = _make_page(qtbot, qapp, tmp_path, confirm=confirm)
    _wpm(page).setValue(120)

    _select(page, "clean")

    assert len(confirm.calls) == 1
    assert page.profile_combo.currentData() == "clean"
    assert page.current_profile() == PRESETS["clean"]
    assert not page.editor.is_dirty()


def test_switching_with_no_edits_does_not_ask(qtbot, qapp, tmp_path):
    confirm = Confirm(False)
    page, _ = _make_page(qtbot, qapp, tmp_path, confirm=confirm)
    _select(page, "clean")
    assert confirm.calls == []
    assert page.profile_combo.currentData() == "clean"


# --- reload_profiles ----------------------------------------------------------------------------------------------------------


def test_reload_picks_up_new_profiles_and_keeps_the_selection(qtbot, qapp, tmp_path):
    page, backend = _make_page(qtbot, qapp, tmp_path)
    _select(page, "clean")
    backend.user["extra"] = Profile(wpm=40.0)

    page.reload_profiles()

    assert "extra" in _combo_names(page)
    assert page.profile_combo.currentData() == "clean"
    assert page.current_profile() == PRESETS["clean"]


def test_reload_keeps_unsaved_edits_when_the_selection_still_exists(qtbot, qapp, tmp_path):
    page, backend = _make_page(qtbot, qapp, tmp_path)
    _wpm(page).setValue(120)
    backend.user["extra"] = Profile()

    page.reload_profiles()

    assert "extra" in _combo_names(page)
    assert page.editor.is_dirty()
    assert _wpm(page).value() == 120


def test_reload_picks_up_fresh_values_for_a_clean_selection(qtbot, qapp, tmp_path):
    backend = FakeProfiles({"mine": Profile(wpm=70.0)})
    page, _ = _make_page(qtbot, qapp, tmp_path, backend=backend)
    _select(page, "mine")
    backend.user["mine"] = Profile(wpm=88.0)  # edited on disk meanwhile

    page.reload_profiles()
    assert page.current_profile().wpm == 88.0


def test_reload_falls_back_when_the_selected_profile_disappeared(qtbot, qapp, tmp_path):
    backend = FakeProfiles({"mine": Profile()})
    page, _ = _make_page(qtbot, qapp, tmp_path, backend=backend)
    _select(page, "mine")
    del backend.user["mine"]

    page.reload_profiles()
    assert page.profile_combo.currentData() == "natural"
    assert "mine" not in _combo_names(page)


def test_an_unreadable_profiles_file_falls_back_to_the_built_ins_with_a_warning(qtbot, qapp, tmp_path):
    backend = FakeProfiles()
    backend.read_error = ValueError("malformed TOML in profiles.toml: oops")
    page, _ = _make_page(qtbot, qapp, tmp_path, backend=backend)

    assert _combo_names(page) == sorted(PRESETS)
    assert not page.message_banner.isHidden()
    assert page.message_banner.tone() == "warning"
    assert "could not be read" in page.message_banner.message()
    assert "malformed TOML" in page.message_banner.message()
    assert page.current_profile() == PRESETS["natural"]


# --- keyboard shortcuts block ------------------------------------------------------------------------------------------------------


def test_the_shortcut_block_is_wired_to_the_shared_settings(qtbot, qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(platforms, "current_os", lambda *a, **k: OperatingSystem.MACOS)
    store = SettingsStore(tmp_path / "settings.json")
    page, _ = _make_page(qtbot, qapp, tmp_path, store=store)

    ids = [page.keymap_target_combo.itemData(i) for i in range(page.keymap_target_combo.count())]
    assert ids == [t.id for t in targets.targets_for(OperatingSystem.MACOS)]
    assert set(page.shortcut_rows) == set(FormatOp)

    bold = page.shortcut_rows[FormatOp.TOGGLE_BOLD]
    bold.edit.setText("ctrl+shift+b")
    bold.edit.editingFinished.emit()
    assert store.settings.keymap_overrides == {"google-docs": {"toggle_bold": "ctrl+shift+b"}}
    assert page.restore_defaults_button.isEnabled()


def test_restore_defaults_uses_the_pages_confirm(qtbot, qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(platforms, "current_os", lambda *a, **k: OperatingSystem.MACOS)
    confirm = Confirm(True)
    store = SettingsStore(tmp_path / "settings.json")
    store.update(keymap_overrides={"google-docs": {"toggle_bold": "ctrl+shift+b"}})
    page, _ = _make_page(qtbot, qapp, tmp_path, store=store, confirm=confirm)

    page.restore_defaults_button.click()

    assert len(confirm.calls) == 1
    assert store.settings.keymap_overrides == {}


# --- file locations -------------------------------------------------------------------------------------------------------------


def test_the_file_locations_are_hidden_until_asked_for(qtbot, qapp, tmp_path):
    page, _ = _make_page(qtbot, qapp, tmp_path)
    assert page.locations_panel.isHidden()
    assert page.locations_button.text() == "Show file locations"


def test_the_link_reveals_both_paths_and_toggles_back(qtbot, qapp, tmp_path):
    page, _ = _make_page(qtbot, qapp, tmp_path)
    page.locations_button.click()

    assert not page.locations_panel.isHidden()
    assert page.profiles_path_edit.text() == str(config.config_path())
    assert page.settings_path_edit.text() == str(app_settings.settings_path())
    assert page.profiles_path_edit.isReadOnly() and page.settings_path_edit.isReadOnly()
    assert page.locations_button.text() == "Hide file locations"

    page.locations_button.click()
    assert page.locations_panel.isHidden()
    assert page.locations_button.text() == "Show file locations"


def test_open_folder_buttons_use_the_injected_opener(qtbot, qapp, tmp_path):
    opener = Opener()
    page, _ = _make_page(qtbot, qapp, tmp_path, opener=opener)
    page.locations_button.click()

    page.open_profiles_folder_button.click()
    page.open_settings_folder_button.click()

    assert opener.paths == [config.config_path().parent, app_settings.settings_path().parent]


# --- default dialogs --------------------------------------------------------------------------------------------------------------


def test_default_confirm_maps_the_buttons(monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
    assert default_confirm(None, "Title", "Really?") is True
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.No))
    assert default_confirm(None, "Title", "Really?") is False


def test_default_name_prompt_returns_text_or_none(monkeypatch):
    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("hello", True)))
    assert default_name_prompt(None, "T", "L", "init") == "hello"
    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("hello", False)))
    assert default_name_prompt(None, "T", "L", "init") is None


def test_default_open_folder_opens_the_directory(monkeypatch, tmp_path):
    from PySide6.QtGui import QDesktopServices

    opened = []
    monkeypatch.setattr(QDesktopServices, "openUrl", staticmethod(lambda url: opened.append(url) or True))
    default_open_folder(tmp_path)
    assert len(opened) == 1
    assert opened[0].toLocalFile() == str(tmp_path)


# --- theming ----------------------------------------------------------------------------------------------------------------------


def test_the_page_restyles_when_the_theme_changes(qtbot, qapp, tmp_path):
    theme_manager = ThemeManager(qapp, mode=ThemeMode.LIGHT)
    page, _ = _make_page(qtbot, qapp, tmp_path, theme_manager=theme_manager)
    assert LIGHT_TOKENS.muted_text in page.styleSheet()
    assert DARK_TOKENS.muted_text not in page.styleSheet()

    theme_manager.set_mode(ThemeMode.DARK)
    assert DARK_TOKENS.muted_text in page.styleSheet()
    assert LIGHT_TOKENS.muted_text not in page.styleSheet()
    # The parts that style themselves follow too.
    assert DARK_TOKENS.text in page.editor.styleSheet()
    assert DARK_TOKENS.muted_text in page.keymap_editor.styleSheet()
    theme_manager.set_mode(ThemeMode.SYSTEM)


# --- the real config functions (default wiring) --------------------------------------------------------------------------------------


def test_the_page_works_against_the_real_profile_file(qtbot, qapp, tmp_path):
    # No injected providers: the defaults must read and write AUTO_TEXT_TYPER_CONFIG
    # (redirected into tmp_path by the autouse fixture).
    store = SettingsStore(tmp_path / "settings.json")
    page = AdvancedPage(
        ThemeManager(qapp),
        store,
        name_prompt=Prompt("real one"),
        confirm=Confirm(True),
        open_folder=Opener(),
    )
    qtbot.addWidget(page)
    assert str(config.config_path()).startswith(str(tmp_path))

    _wpm(page).setValue(91)
    with qtbot.waitSignal(page.profilesChanged, timeout=1000):
        page.save_as_button.click()

    assert config.config_path().exists()
    assert config.load_profiles()["real one"] == PRESETS["natural"].with_overrides(wpm=91.0)
    assert page.profile_combo.currentData() == "real one"

    with qtbot.waitSignal(page.profilesChanged, timeout=1000):
        page.delete_button.click()
    assert "real one" not in config.load_profiles()
    assert page.profile_combo.currentData() == "natural"
