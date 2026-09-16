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
