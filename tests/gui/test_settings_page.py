from auto_essay_typer.gui.pages.settings_page import SettingsPage
from auto_essay_typer.gui.theme import ThemeManager, ThemeMode


def _make_page(qtbot, qapp, initial_mini_window=True):
    theme_manager = ThemeManager(qapp)
    state = {"show_mini_window": initial_mini_window}
    page = SettingsPage(
        theme_manager,
        lambda: state["show_mini_window"],
        lambda value: state.__setitem__("show_mini_window", value),
    )
    qtbot.addWidget(page)
    return page, theme_manager, state


def test_theme_combo_starts_on_the_manager_current_mode(qtbot, qapp):
    theme_manager = ThemeManager(qapp, mode=ThemeMode.DARK)
    state = {"show_mini_window": True}
    page = SettingsPage(theme_manager, lambda: state["show_mini_window"], lambda v: None)
    qtbot.addWidget(page)
    assert page.theme_combo.currentData() == ThemeMode.DARK


def test_changing_theme_combo_updates_the_theme_manager(qtbot, qapp):
    page, theme_manager, _state = _make_page(qtbot, qapp)
    dark_index = page.theme_combo.findData(ThemeMode.DARK)
    page.theme_combo.setCurrentIndex(dark_index)
    assert theme_manager.mode is ThemeMode.DARK

    light_index = page.theme_combo.findData(ThemeMode.LIGHT)
    page.theme_combo.setCurrentIndex(light_index)
    assert theme_manager.mode is ThemeMode.LIGHT


def test_mini_window_checkbox_reflects_initial_value(qtbot, qapp):
    page, _tm, _state = _make_page(qtbot, qapp, initial_mini_window=False)
    assert page.mini_window_checkbox.isChecked() is False


def test_toggling_mini_window_checkbox_calls_the_setter(qtbot, qapp):
    page, _tm, state = _make_page(qtbot, qapp, initial_mini_window=True)
    page.mini_window_checkbox.setChecked(False)
    assert state["show_mini_window"] is False
    page.mini_window_checkbox.setChecked(True)
    assert state["show_mini_window"] is True
