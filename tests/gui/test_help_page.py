from auto_essay_typer.gui.pages.help_page import HelpPage


def test_help_page_includes_how_to_use_permission_and_about(qtbot, qapp):
    page = HelpPage("1.2.3")
    qtbot.addWidget(page)

    from PySide6.QtWidgets import QLabel

    labels_text = " ".join(label.text() for label in page.findChildren(QLabel))
    assert "Paste or type" in labels_text
    assert "Start typing" in labels_text
    assert "Esc" in labels_text
    assert "Permission on" in labels_text
    assert "1.2.3" in labels_text
    assert "MIT License" in labels_text


def test_add_section_appends_a_new_card(qtbot, qapp):
    page = HelpPage("1.0.0")
    qtbot.addWidget(page)

    from PySide6.QtWidgets import QLabel

    before = len(page.findChildren(QLabel))
    page.add_section("Changelog", "Nothing yet.")
    after = len(page.findChildren(QLabel))
    assert after == before + 2  # one title label + one body label

    labels_text = " ".join(label.text() for label in page.findChildren(QLabel))
    assert "Changelog" in labels_text
    assert "Nothing yet." in labels_text


def test_show_setup_again_asks_the_owner_to_reopen_the_walkthrough(qtbot, qapp):
    page = HelpPage("1.0.0")
    qtbot.addWidget(page)

    assert page.show_setup_button.text() == "Show setup again"
    assert page.show_setup_button.accessibleName() == "Show setup again"
    with qtbot.waitSignal(page.showSetupRequested, timeout=1000):
        page.show_setup_button.click()


def test_the_setup_section_explains_what_the_button_does(qtbot, qapp):
    from PySide6.QtWidgets import QLabel

    page = HelpPage("1.0.0")
    qtbot.addWidget(page)
    labels_text = " ".join(label.text() for label in page.findChildren(QLabel))
    assert "Setup" in labels_text
    assert "welcome steps" in labels_text


def test_a_section_can_carry_a_button_under_its_text(qtbot, qapp):
    from PySide6.QtWidgets import QPushButton

    page = HelpPage("1.0.0")
    qtbot.addWidget(page)
    button = QPushButton("Do it")
    page.add_section("Extra", "Some text.", button=button)
    assert button.parent() is not None
    assert page.isAncestorOf(button)


def test_the_keyboard_help_names_all_four_pages(qtbot, qapp):
    from PySide6.QtWidgets import QLabel

    page = HelpPage("1.0.0")
    qtbot.addWidget(page)
    labels_text = " ".join(label.text() for label in page.findChildren(QLabel))
    assert "Type, Settings, Advanced and Help" in labels_text


def test_the_help_page_explains_the_tray_and_the_hotkeys(qtbot, qapp):
    from PySide6.QtWidgets import QLabel

    page = HelpPage("1.0.0")
    qtbot.addWidget(page)
    labels_text = " ".join(label.text() for label in page.findChildren(QLabel))
    assert "From the tray or a hotkey" in labels_text
    assert "Start typing from clipboard" in labels_text
    assert "global hotkeys" in labels_text
    assert "Confirm before typing" in labels_text


def test_permission_section_matches_the_current_os(qtbot, qapp, monkeypatch):
    from auto_essay_typer.gui.pages import help_page as help_page_module
    from auto_essay_typer.platforms import OperatingSystem

    monkeypatch.setattr(help_page_module, "current_os", lambda: OperatingSystem.LINUX)
    page = HelpPage("1.0.0")
    qtbot.addWidget(page)

    from PySide6.QtWidgets import QLabel

    labels_text = " ".join(label.text() for label in page.findChildren(QLabel))
    assert "Permission on Linux" in labels_text
    assert "Wayland" in labels_text
