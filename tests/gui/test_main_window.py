from PySide6.QtCore import QSettings

from auto_essay_typer.gui.main_window import MainWindow, NavPage
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


def test_main_window_registers_type_settings_help_by_default(qtbot, qapp, tmp_path):
    window = _make_window(qapp, tmp_path, permission_checker=_granted_status)
    qtbot.addWidget(window)
    assert [p.page_id for p in window._pages] == ["type", "settings", "help"]
    assert isinstance(window.page_stack.widget(0), TypePage)
    assert isinstance(window.page_stack.widget(1), SettingsPage)
    assert isinstance(window.page_stack.widget(2), HelpPage)


def test_navigation_switches_the_visible_page(qtbot, qapp, tmp_path):
    window = _make_window(qapp, tmp_path, permission_checker=_granted_status)
    qtbot.addWidget(window)

    assert window.page_stack.currentIndex() == 0
    window._nav_buttons["settings"].click()
    assert window.page_stack.currentIndex() == 1
    assert window.page_stack.currentWidget() is window.page_stack.widget(1)

    window._nav_buttons["help"].click()
    assert window.page_stack.currentIndex() == 2

    window._nav_buttons["type"].click()
    assert window.page_stack.currentIndex() == 0


def test_navigation_registry_is_extensible(qtbot, qapp, tmp_path):
    window = _make_window(qapp, tmp_path, permission_checker=_granted_status)
    qtbot.addWidget(window)

    from PySide6.QtWidgets import QLabel

    window.register_page(NavPage("extra", "Extra", "help", lambda: QLabel("extra page")))
    assert "extra" in window._nav_buttons
    window._nav_buttons["extra"].click()
    assert window.page_stack.currentIndex() == 3


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
