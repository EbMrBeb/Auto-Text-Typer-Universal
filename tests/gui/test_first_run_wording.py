"""What a first-time user is told at the moments that worry them: before a run, during one, and at the permission gate."""

from auto_essay_typer import platforms
from auto_essay_typer.gui.pages.type_page import TypePage
from auto_essay_typer.gui.permission_dialog import WHY_TEXT, PermissionDialog
from auto_essay_typer.gui.run_controller import RunController
from auto_essay_typer.gui.settings_store import SettingsStore
from auto_essay_typer.gui.theme import LIGHT_TOKENS, ThemeManager
from auto_essay_typer.platforms import OperatingSystem, PermissionStatus

_GRANTED = PermissionStatus(granted=True, summary="ok", fix_steps=(), can_open_settings=False)


def _page(qtbot, qapp, tmp_path, **settings):
    store = SettingsStore(tmp_path / "settings.json")
    if settings:
        store.update(**settings)
    page = TypePage(
        RunController(driver_factory=lambda **_kw: None),
        ThemeManager(qapp),
        permission_checker=lambda: _GRANTED,
        settings_store=store,
    )
    qtbot.addWidget(page)
    return page


def test_the_empty_text_box_points_to_importing_a_word_file(qtbot, qapp, tmp_path):
    page = _page(qtbot, qapp, tmp_path)
    hint = page.text_edit.placeholderText()
    assert "Word file" in hint and "Google Doc" in hint


def test_the_import_box_names_word_and_google_docs(qtbot, qapp, tmp_path):
    page = _page(qtbot, qapp, tmp_path)
    placeholder = page.import_bar.edit.placeholderText()
    assert "Word" in placeholder and "Google Doc" in placeholder


def test_a_run_tells_the_user_to_keep_hands_off_and_how_to_stop(qtbot, qapp, tmp_path):
    page = _page(qtbot, qapp, tmp_path)
    page._set_running_ui(True)
    text = page.run_hint_label.text()
    assert "leave the keyboard and mouse alone" in text
    assert "Stop, or press Esc," in text
    assert page.run_hint_label.isHidden() is False


def test_the_run_hint_does_not_promise_esc_when_the_hotkeys_are_off(qtbot, qapp, tmp_path):
    page = _page(qtbot, qapp, tmp_path, global_hotkeys_enabled=False)
    page._set_running_ui(True)
    assert "Esc" not in page.run_hint_label.text()
    assert "Stop ends it at once" in page.run_hint_label.text()


def test_the_permission_dialog_says_why_typing_needs_permission(qtbot, qapp):
    status = PermissionStatus(False, "Needs access.", ("Do it.",), True)
    dialog = PermissionDialog(
        status,
        LIGHT_TOKENS,
        permission_checker=lambda: status,
        current_os=lambda: OperatingSystem.LINUX,
    )
    qtbot.addWidget(dialog)
    assert dialog._why_label.text() == WHY_TEXT
    assert "pressing keys for you" in WHY_TEXT


def test_the_mac_steps_cover_a_grant_that_went_stale():
    status = platforms.check_input_permission(os_=OperatingSystem.MACOS, ax_trusted_probe=lambda: False)
    assert "off and on again" in " ".join(status.fix_steps)
