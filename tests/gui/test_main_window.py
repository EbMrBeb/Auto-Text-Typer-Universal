from PySide6.QtCore import QSettings

from auto_essay_typer.gui.main_window import MainWindow, NavPage
from auto_essay_typer.gui.pages.advanced_page import AdvancedPage
from auto_essay_typer.gui.pages.help_page import HelpPage
from auto_essay_typer.gui.pages.settings_page import SettingsPage
from auto_essay_typer.gui.pages.type_page import TypePage
from auto_essay_typer.gui.theme import ThemeManager
from auto_essay_typer.platforms import PermissionStatus


def _granted_status() -> PermissionStatus:
    return PermissionStatus(granted=True, summary="ok", fix_steps=(), can_open_settings=False)


def _not_granted_status() -> PermissionStatus:
    return PermissionStatus(
        granted=False,
        summary="not granted",
        fix_steps=("Step one.", "Step two."),
        can_open_settings=True,
    )


def _memory_settings(tmp_path) -> QSettings:
    return QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)


def _make_window(qapp, tmp_path, *, driver_factory=None, permission_checker=None) -> MainWindow:
    theme_manager = ThemeManager(qapp)
    kwargs = {"settings": _memory_settings(tmp_path)}
    if driver_factory is not None:
        kwargs["driver_factory"] = driver_factory
    if permission_checker is not None:
        kwargs["permission_checker"] = permission_checker
    return MainWindow(theme_manager, **kwargs)


def test_main_window_constructs_with_default_size_and_minimum(qtbot, qapp, tmp_path):
    window = _make_window(qapp, tmp_path, permission_checker=_granted_status)
    qtbot.addWidget(window)
    assert window.size().width() == 1000
    assert window.size().height() == 700
    assert window.minimumWidth() == 820
    assert window.minimumHeight() == 600


def test_main_window_registers_type_settings_advanced_help_in_that_order(qtbot, qapp, tmp_path):
    window = _make_window(qapp, tmp_path, permission_checker=_granted_status)
    qtbot.addWidget(window)
    assert [p.page_id for p in window._pages] == ["type", "settings", "advanced", "help"]
    assert isinstance(window.page_stack.widget(0), TypePage)
    assert isinstance(window.page_stack.widget(1), SettingsPage)
    assert isinstance(window.page_stack.widget(3), HelpPage)


def test_the_sidebar_lists_advanced_after_settings_and_before_help(qtbot, qapp, tmp_path):
    from PySide6.QtWidgets import QPushButton

    window = _make_window(qapp, tmp_path, permission_checker=_granted_status)
    qtbot.addWidget(window)
    layout = window._nav_layout
    buttons = [layout.itemAt(i).widget() for i in range(layout.count())]
    labels = [b.text() for b in buttons if isinstance(b, QPushButton) and b.objectName() == "NavButton"]
    assert labels == ["Type", "Settings", "Advanced", "Help"]


def test_navigation_switches_the_visible_page(qtbot, qapp, tmp_path):
    window = _make_window(qapp, tmp_path, permission_checker=_granted_status)
    qtbot.addWidget(window)

    assert window.page_stack.currentIndex() == 0
    window._nav_buttons["settings"].click()
    assert window.page_stack.currentIndex() == 1
    assert window.page_stack.currentWidget() is window.page_stack.widget(1)

    window._nav_buttons["advanced"].click()
    assert window.page_stack.currentIndex() == 2

    window._nav_buttons["help"].click()
    assert window.page_stack.currentIndex() == 3

    window._nav_buttons["type"].click()
    assert window.page_stack.currentIndex() == 0


def test_the_advanced_page_is_reachable_through_navigation(qtbot, qapp, tmp_path):
    window = _make_window(qapp, tmp_path, permission_checker=_granted_status)
    qtbot.addWidget(window)

    window._nav_buttons["advanced"].click()

    assert isinstance(window.page_stack.currentWidget(), AdvancedPage)
    assert window._nav_buttons["advanced"].isChecked()
    assert window.page_stack.currentWidget().profile_combo.count() >= 3  # the built-in profiles


def test_the_advanced_page_is_built_the_first_time_it_is_opened_and_only_once(qtbot, qapp, tmp_path, monkeypatch):
    from auto_essay_typer.gui import main_window as main_window_module

    built: list[AdvancedPage] = []

    class CountingAdvancedPage(AdvancedPage):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            built.append(self)

    monkeypatch.setattr(main_window_module, "AdvancedPage", CountingAdvancedPage)
    window = _make_window(qapp, tmp_path, permission_checker=_granted_status)
    qtbot.addWidget(window)

    # Registered, with its place in the stack held by a placeholder -- but not built.
    assert built == []
    assert window.is_page_built("advanced") is False
    assert window.page_stack.count() == 4
    assert not isinstance(window.page_stack.widget(2), AdvancedPage)

    window._nav_buttons["advanced"].click()
    assert len(built) == 1
    assert window.is_page_built("advanced") is True
    assert window.page_stack.currentWidget() is built[0]
    assert window.page_stack.count() == 4  # the placeholder was replaced, not stacked beside it

    window._nav_buttons["help"].click()
    window._nav_buttons["advanced"].click()
    assert len(built) == 1
    assert window.page_stack.currentWidget() is built[0]


def test_ctrl_number_shortcuts_follow_the_sidebar_order(qtbot, qapp, tmp_path):
    from PySide6.QtGui import QShortcut

    window = _make_window(qapp, tmp_path, permission_checker=_granted_status)
    qtbot.addWidget(window)
    shortcuts = {s.key().toString(): s for s in window.findChildren(QShortcut)}
    for sequence, expected in (("Ctrl+3", AdvancedPage), ("Ctrl+4", HelpPage), ("Ctrl+2", SettingsPage), ("Ctrl+1", TypePage)):
        shortcuts[sequence].activated.emit()
        assert isinstance(window.page_stack.currentWidget(), expected), sequence
    assert "Advanced" in window._nav_buttons["advanced"].toolTip()


def test_a_lazy_page_registered_later_is_also_built_on_first_use(qtbot, qapp, tmp_path):
    from PySide6.QtWidgets import QLabel

    window = _make_window(qapp, tmp_path, permission_checker=_granted_status)
    qtbot.addWidget(window)
    built = []

    def factory():
        built.append(True)
        return QLabel("later")

    window.register_page(NavPage("later", "Later", "help", factory, lazy=True), index=1)
    assert built == []
    assert [p.page_id for p in window._pages] == ["type", "later", "settings", "advanced", "help"]
    window.show_page("later")
    assert built == [True]
    assert window.page_stack.currentIndex() == 1
    assert window.page("later") is window.page_stack.currentWidget()  # asking again builds nothing


def test_navigation_registry_is_extensible(qtbot, qapp, tmp_path):
    window = _make_window(qapp, tmp_path, permission_checker=_granted_status)
    qtbot.addWidget(window)

    from PySide6.QtWidgets import QLabel

    window.register_page(NavPage("extra", "Extra", "help", lambda: QLabel("extra page")))
    assert "extra" in window._nav_buttons
    window._nav_buttons["extra"].click()
    assert window.page_stack.currentIndex() == 4


def test_the_type_page_and_the_window_share_one_settings_store(qtbot, qapp, tmp_path):
    window = _make_window(qapp, tmp_path, permission_checker=_granted_status)
    qtbot.addWidget(window)
    page = window.type_page

    assert page._settings_store is window.settings_store

    # A change through either side is the other's straight away. The editor the
    # picker saves reaches the window, which builds every run's driver from it;
    # and an editor chosen anywhere else moves the picker, without this page
    # having to be shown again to catch up with the file.
    saved = window.settings_store.settings.target_app
    other = next(
        page.target_combo.itemData(i)
        for i in range(page.target_combo.count())
        if page.target_combo.itemData(i) != saved
    )
    page.target_combo.setCurrentIndex(page.target_combo.findData(other))
    assert window.settings_store.settings.target_app == other

    window.settings_store.update(target_app=saved)
    assert page.selected_target_id() == saved


def test_an_editor_chosen_on_the_settings_page_reaches_the_type_page_at_once(qtbot, qapp, tmp_path):
    window = _make_window(qapp, tmp_path, permission_checker=_granted_status)
    qtbot.addWidget(window)
    page = window.type_page
    settings_page = window.page("settings")
    chosen = next(
        settings_page.target_combo.itemData(i)
        for i in range(settings_page.target_combo.count())
        if settings_page.target_combo.itemData(i) != page.selected_target_id()
    )

    settings_page.target_combo.setCurrentIndex(settings_page.target_combo.findData(chosen))

    assert page.selected_target_id() == chosen


def test_permission_chip_shows_ready_when_granted(qtbot, qapp, tmp_path):
    window = _make_window(qapp, tmp_path, permission_checker=_granted_status)
    qtbot.addWidget(window)
    assert window.permission_chip.text() == "Ready to type"
    assert window.permission_chip.property("state") == "ready"


def test_permission_chip_shows_warning_when_not_granted(qtbot, qapp, tmp_path):
    window = _make_window(qapp, tmp_path, permission_checker=_not_granted_status)
    qtbot.addWidget(window)
    assert window.permission_chip.text() == "Permission needed - Fix"
    assert window.permission_chip.property("state") == "warning"


def test_clicking_permission_chip_opens_dialog_and_refreshes(qtbot, qapp, tmp_path, monkeypatch):
    window = _make_window(qapp, tmp_path, permission_checker=_not_granted_status)
    qtbot.addWidget(window)

    from auto_essay_typer.gui import main_window as main_window_module

    opened = []
    monkeypatch.setattr(
        main_window_module.PermissionDialog,
        "exec",
        lambda self: opened.append(True) or 0,
    )
    window._on_permission_chip_clicked()
    assert opened == [True]
    # refresh_permission_status ran again after the dialog closed.
    assert window.permission_chip.property("state") == "warning"


def test_geometry_is_restored_from_settings(qtbot, qapp, tmp_path):
    # Both windows are actually shown (not just constructed): saveGeometry()/
    # restoreGeometry() encode real window-manager frame geometry and are
    # only reliable once a window has been shown at least once.
    #
    # Only height is asserted below, not width: the offscreen QPA platform's
    # synthetic screen is 800px wide (verified directly), narrower than this
    # window's own 820px minimumWidth, so restoreGeometry's screen-fit
    # validation always falls back to the minimum width regardless of what
    # was saved -- an artifact of the tiny virtual screen this suite runs
    # under, not of the save/restore wiring itself, which height (650 < 800,
    # so it fits) demonstrates working correctly.
    settings_path = tmp_path / "settings.ini"
    theme_manager = ThemeManager(qapp)
    first = MainWindow(
        theme_manager,
        permission_checker=_granted_status,
        settings=QSettings(str(settings_path), QSettings.Format.IniFormat),
    )
    qtbot.addWidget(first)
    first.show()
    qtbot.waitExposed(first)
    first.resize(900, 650)
    qtbot.wait(50)
    assert first.saveGeometry() != b""
    first.close()  # closeEvent saves geometry

    stored = QSettings(str(settings_path), QSettings.Format.IniFormat).value("mainWindow/geometry")
    assert stored is not None

    second = MainWindow(
        theme_manager,
        permission_checker=_granted_status,
        settings=QSettings(str(settings_path), QSettings.Format.IniFormat),
    )
    qtbot.addWidget(second)
    second.show()
    qtbot.waitExposed(second)
    assert second.size().height() == 650
