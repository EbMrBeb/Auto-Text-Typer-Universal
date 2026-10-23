from PySide6.QtCore import Qt

from auto_essay_typer.gui.quick_settings import MISTAKE_STOPS
from auto_essay_typer.gui.run_controller import RunController
from auto_essay_typer.gui.theme import ThemeManager
from auto_essay_typer.gui.pages.type_page import TypePage
from auto_essay_typer.platforms import PermissionStatus
from auto_essay_typer.profiles import PRESETS, Profile


def _granted() -> PermissionStatus:
    return PermissionStatus(granted=True, summary="ok", fix_steps=(), can_open_settings=False)


def _not_granted() -> PermissionStatus:
    return PermissionStatus(
        granted=False, summary="nope", fix_steps=("Do a thing.",), can_open_settings=True
    )


def _make_page(qtbot, qapp, recording_driver_factory, permission_checker=_granted):
    factory, keyboards = recording_driver_factory
    controller = RunController(driver_factory=factory)
    theme_manager = ThemeManager(qapp)
    page = TypePage(controller, theme_manager, permission_checker=permission_checker)
    qtbot.addWidget(page)
    return page, controller, keyboards


# --- quick-settings mapping and profile reset ---------------------------------------


def test_speed_slider_label_reflects_the_word_mapping(qtbot, qapp, recording_driver_factory):
    page, _controller, _kb = _make_page(qtbot, qapp, recording_driver_factory)
    page.speed_slider.setValue(30)
    assert "Slow" in page.speed_value_label.text()
    page.speed_slider.setValue(65)
    assert "Natural" in page.speed_value_label.text()
    page.speed_slider.setValue(120)
    assert "Very fast" in page.speed_value_label.text()


def test_mistake_slider_label_reflects_the_stop_mapping(qtbot, qapp, recording_driver_factory):
    page, _controller, _kb = _make_page(qtbot, qapp, recording_driver_factory)
    page.mistake_slider.setValue(0)
    assert page.mistake_value_label.text() == "None"
    page.mistake_slider.setValue(4)
    assert page.mistake_value_label.text() == "Sloppy"


def test_breaks_switch_defaults_to_the_profile_value(qtbot, qapp, recording_driver_factory):
    page, _controller, _kb = _make_page(qtbot, qapp, recording_driver_factory)
    # "natural" (the default-selected profile) has session_breaks=True.
    assert page.breaks_switch.isChecked() is True


def test_effective_profile_layers_quick_controls_over_the_base_profile(
    qtbot, qapp, recording_driver_factory
):
    page, _controller, _kb = _make_page(qtbot, qapp, recording_driver_factory)
    page.speed_slider.setValue(90)
    page.mistake_slider.setValue(0)  # "None" -> 0.0
    page.breaks_switch.setChecked(False)

    profile = page.effective_profile()
    assert profile.wpm == 90.0
    assert profile.typo_rate_per_100 == 0.0
    assert profile.session_breaks is False


def test_selecting_a_profile_resets_the_quick_controls_to_its_values(
    qtbot, qapp, recording_driver_factory
):
    page, _controller, _kb = _make_page(qtbot, qapp, recording_driver_factory)

    # Push the quick controls away from "natural"'s own values.
    page.speed_slider.setValue(140)
    page.mistake_slider.setValue(4)
    page.breaks_switch.setChecked(False)

    clean = PRESETS["clean"]
    index = page.profile_combo.findData("clean")
    assert index != -1
    page.profile_combo.setCurrentIndex(index)

    assert page.speed_slider.value() == round(clean.wpm)
    assert page.breaks_switch.isChecked() == clean.session_breaks
    rate, _label = MISTAKE_STOPS[page.mistake_slider.value()]
    assert rate == clean.typo_rate_per_100


def test_builtin_profiles_are_labelled(qtbot, qapp, recording_driver_factory):
    page, _controller, _kb = _make_page(qtbot, qapp, recording_driver_factory)
    natural_index = page.profile_combo.findData("natural")
    assert "Built-in" in page.profile_combo.itemText(natural_index)


# --- empty text blocks Start / Preview ------------------------------------------------


def test_empty_text_blocks_start_and_shows_inline_message(qtbot, qapp, recording_driver_factory):
    # isHidden() (the widget's own explicit show/hide flag), not isVisible()
    # (which also depends on the whole ancestor chain being shown on screen,
    # and the page is deliberately never shown in these unit tests).
    page, controller, keyboards = _make_page(qtbot, qapp, recording_driver_factory)
    assert page.inline_message_label.isHidden() is True
    page._on_start_clicked()
    assert page.inline_message_label.isHidden() is False
    assert controller.is_busy is False
    assert not keyboards


def test_empty_text_blocks_preview(qtbot, qapp, recording_driver_factory):
    page, _controller, _kb = _make_page(qtbot, qapp, recording_driver_factory)
    page._on_preview_clicked()
    assert page.inline_message_label.isHidden() is False


def test_typing_text_hides_the_inline_message(qtbot, qapp, recording_driver_factory):
    page, _controller, _kb = _make_page(qtbot, qapp, recording_driver_factory)
    page._on_start_clicked()
    assert page.inline_message_label.isHidden() is False
    page.set_text("now there is text")
    assert page.inline_message_label.isHidden() is True


# --- permission preflight --------------------------------------------------------------


def test_permission_not_granted_shows_dialog_and_does_not_start(
    qtbot, qapp, recording_driver_factory, monkeypatch
):
    page, controller, keyboards = _make_page(
        qtbot, qapp, recording_driver_factory, permission_checker=_not_granted
    )
    page.set_text("hello")

    from auto_essay_typer.gui.pages import type_page as type_page_module

    opened = []

    def fake_exec(self):
        opened.append(True)
        return 0  # Rejected -- user cancelled

    monkeypatch.setattr(type_page_module.PermissionDialog, "exec", fake_exec)
    monkeypatch.setattr(type_page_module.PermissionDialog, "granted", lambda self: False)

    page._on_start_clicked()
    assert opened == [True]
    assert controller.is_busy is False
    assert not keyboards


def test_permission_granted_after_dialog_starts_the_run(
    qtbot, qapp, recording_driver_factory, monkeypatch
):
    page, controller, keyboards = _make_page(
        qtbot, qapp, recording_driver_factory, permission_checker=_not_granted
    )
    page.set_text("hello")

    from auto_essay_typer.gui.pages import type_page as type_page_module

    monkeypatch.setattr(type_page_module.PermissionDialog, "exec", lambda self: 1)
    monkeypatch.setattr(type_page_module.PermissionDialog, "granted", lambda self: True)

    with qtbot.waitSignal(controller.finished, timeout=5000):
        page._on_start_clicked()
    assert keyboards[0].text == "hello"


def test_permission_already_granted_starts_without_a_dialog(
    qtbot, qapp, recording_driver_factory, monkeypatch
):
    page, controller, keyboards = _make_page(qtbot, qapp, recording_driver_factory)
    page.set_text("hello")

    from auto_essay_typer.gui.pages import type_page as type_page_module

    def fail_if_called(self, *a, **k):
        raise AssertionError("PermissionDialog should not be constructed when already granted")

    monkeypatch.setattr(type_page_module, "PermissionDialog", fail_if_called)

    with qtbot.waitSignal(controller.finished, timeout=5000):
        page._on_start_clicked()
    assert keyboards[0].text == "hello"


# --- start / run panel / state sequence / progress / return to idle -------------------


def test_start_runs_the_state_sequence_and_returns_to_idle_with_text_intact(
    qtbot, qapp, recording_driver_factory
):
    page, controller, keyboards = _make_page(qtbot, qapp, recording_driver_factory)
    page.set_text("Hello world.")

    states: list[str] = []
    controller.stateChanged.connect(states.append)

    assert page.idle_actions_widget.isHidden() is False  # idle actions shown
    assert page.run_panel_widget.isHidden() is True
    with qtbot.waitSignal(controller.finished, timeout=5000):
        page._on_start_clicked()

    assert "planning" in states
    assert "typing" in states
    assert keyboards[0].text == "Hello world."

    # UI returned to idle: run panel hidden, inputs re-enabled, text intact.
    assert page.idle_actions_widget.isHidden() is False
    assert page.run_panel_widget.isHidden() is True
    assert page.text_edit.isEnabled() is True
    assert page.profile_combo.isEnabled() is True
    assert page.text_edit.toPlainText() == "Hello world."


def test_progress_reaches_100_percent_in_the_run_panel(qtbot, qapp, recording_driver_factory):
    page, controller, _keyboards = _make_page(qtbot, qapp, recording_driver_factory)
    page.set_text("abcdefghij")

    with qtbot.waitSignal(controller.finished, timeout=5000):
        page._on_start_clicked()

    assert page.run_percent_label.text() == "100%"
    assert page.run_progress_bar.value() == 100


def test_text_and_quick_settings_are_disabled_while_running(qtbot, qapp):
    from auto_essay_typer.driver import Driver, RecordingKeyboard
    import threading
    import time

    keyboard = RecordingKeyboard()
    gate = threading.Event()

    def slow_sleep(_seconds: float) -> None:
        gate.set()
        time.sleep(0.01)

    driver = Driver(keyboard=keyboard, sleep=slow_sleep, countdown_seconds=0)
    controller = RunController(driver_factory=lambda: driver)
    from PySide6.QtWidgets import QApplication

    theme_manager = ThemeManager(QApplication.instance())
    page = TypePage(controller, theme_manager, permission_checker=_granted)
    qtbot.addWidget(page)
    page.set_text("a somewhat longer sentence to type slowly enough to observe")

    page._on_start_clicked()
    qtbot.waitUntil(lambda: controller.state == "typing", timeout=5000)

    assert page.text_edit.isEnabled() is False
    assert page.profile_combo.isEnabled() is False
    assert page.speed_slider.isEnabled() is False
    assert page.mistake_slider.isEnabled() is False
    assert page.breaks_switch.isEnabled() is False
    assert page.run_panel_widget.isHidden() is False

    controller.stop()
    qtbot.waitSignal(controller.finished, timeout=5000).wait()


# --- pause / resume / stop, driven through the page's own buttons ---------------------


def test_pause_resume_button_toggles_the_running_driver(qtbot, qapp):
    import threading
    import time

    from auto_essay_typer.driver import Driver, RecordingKeyboard

    keyboard = RecordingKeyboard()
    driver = Driver(keyboard=keyboard, sleep=lambda _s: time.sleep(0.003), countdown_seconds=0)
    controller = RunController(driver_factory=lambda: driver)
    from PySide6.QtWidgets import QApplication

    theme_manager = ThemeManager(QApplication.instance())
    page = TypePage(controller, theme_manager, permission_checker=_granted)
    qtbot.addWidget(page)
    page.set_text("pause and resume this run please")

    page._on_start_clicked()
    qtbot.waitUntil(lambda: controller.state == "typing", timeout=5000)

    page._on_pause_resume_clicked()
    assert controller.state == "paused"
    assert page.pause_resume_button.text() == "Resume"

    page._on_pause_resume_clicked()  # now resumes, with a countdown
    assert controller.state == "resuming"

    qtbot.waitUntil(lambda: controller.state == "typing", timeout=5000)
    qtbot.waitSignal(controller.finished, timeout=5000).wait()
    assert keyboard.text == "pause and resume this run please"


def test_stop_button_aborts_the_run(qtbot, qapp):
    import threading
    import time

    from auto_essay_typer.driver import Driver, RecordingKeyboard

    keyboard = RecordingKeyboard()
    driver = Driver(keyboard=keyboard, sleep=lambda _s: time.sleep(0.003), countdown_seconds=0)
    controller = RunController(driver_factory=lambda: driver)
    from PySide6.QtWidgets import QApplication

    theme_manager = ThemeManager(QApplication.instance())
    page = TypePage(controller, theme_manager, permission_checker=_granted)
    qtbot.addWidget(page)
    page.set_text("a long enough piece of text that stopping mid-way is observable")

    with qtbot.waitSignal(controller.finished, timeout=5000) as blocker:
        page._on_start_clicked()
        qtbot.waitUntil(lambda: controller.state == "typing", timeout=5000)
        page.stop_button.click()

    outcome, _message = blocker.args
    assert outcome == "stopped"
    assert page.idle_actions_widget.isHidden() is False  # back to idle


# --- drag-and-drop and paste normalization ---------------------------------------------


def test_file_dropped_signal_loads_normalized_text(qtbot, qapp, recording_driver_factory):
    page, _controller, _kb = _make_page(qtbot, qapp, recording_driver_factory)
    page.text_edit.fileDropped.emit("line one\r\nline two\r\n")
    assert page.text_edit.toPlainText() == "line one\nline two\n"


def test_dropping_a_txt_file_loads_its_contents(qtbot, qapp, recording_driver_factory, tmp_path):
    from PySide6.QtCore import QMimeData, QPointF, QUrl
    from PySide6.QtGui import QDropEvent
    from PySide6.QtCore import Qt as QtCoreQt

    page, _controller, _kb = _make_page(qtbot, qapp, recording_driver_factory)

    dropped_file = tmp_path / "essay.txt"
    dropped_file.write_text("Dropped file contents.\r\nSecond line.")

    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(dropped_file))])
    event = QDropEvent(
        QPointF(10, 10),
        QtCoreQt.DropAction.CopyAction,
        mime,
        QtCoreQt.MouseButton.LeftButton,
        QtCoreQt.KeyboardModifier.NoModifier,
    )
    page.text_edit.dropEvent(event)
    assert page.text_edit.toPlainText() == "Dropped file contents.\nSecond line."


def test_dropping_an_unsupported_file_is_ignored_by_the_custom_handler(
    qtbot, qapp, recording_driver_factory, tmp_path
):
    from PySide6.QtCore import QMimeData, QPointF, QUrl
    from PySide6.QtCore import Qt as QtCoreQt
    from PySide6.QtGui import QDropEvent

    page, _controller, _kb = _make_page(qtbot, qapp, recording_driver_factory)
    page.set_text("existing text")

    other_file = tmp_path / "image.png"
    other_file.write_bytes(b"\x89PNG\r\n")

    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(other_file))])
    event = QDropEvent(
        QPointF(10, 10),
        QtCoreQt.DropAction.CopyAction,
        mime,
        QtCoreQt.MouseButton.LeftButton,
        QtCoreQt.KeyboardModifier.NoModifier,
    )
    page.text_edit.dropEvent(event)
    # A non-.txt/.md drop is not handled by fileDropped -- the existing text
    # is untouched (Qt's own default QPlainTextEdit drop handling, which
    # does not insert file contents for a plain file-URL drop either).
    assert page.text_edit.toPlainText() == "existing text"


def test_pasting_text_normalizes_line_endings(qtbot, qapp, recording_driver_factory):
    from PySide6.QtCore import QMimeData

    page, _controller, _kb = _make_page(qtbot, qapp, recording_driver_factory)
    mime = QMimeData()
    mime.setText("pasted\r\ntext\r\n")
    page.text_edit.insertFromMimeData(mime)
    assert page.text_edit.toPlainText() == "pasted\ntext\n"


# --- drops and pastes: only text and .txt/.md files ever reach the document --------------
#
# Qt reports a list of URLs as *text* (QMimeData.hasText() is true for it and text() is
# the "file:///..." line), so QPlainTextEdit's own drop and paste handling would insert
# the URL. These tests pin the box to: load a dropped .txt/.md, insert dropped or pasted
# text, and refuse every other file and link without touching the document.


def _drop(page, mime, *, position=(10, 10)):
    from PySide6.QtCore import QPointF
    from PySide6.QtCore import Qt as QtCoreQt
    from PySide6.QtGui import QDropEvent

    event = QDropEvent(
        QPointF(*position),
        QtCoreQt.DropAction.CopyAction,
        mime,
        QtCoreQt.MouseButton.LeftButton,
        QtCoreQt.KeyboardModifier.NoModifier,
    )
    page.text_edit.dropEvent(event)
    return event


def _file_mime(path):
    from PySide6.QtCore import QMimeData, QUrl

    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(path))])
    return mime


def test_dropping_plain_text_inserts_that_text(qtbot, qapp, recording_driver_factory):
    from PySide6.QtCore import QMimeData

    page, _controller, _kb = _make_page(qtbot, qapp, recording_driver_factory)
    mime = QMimeData()
    mime.setText("dropped words")
    _drop(page, mime)
    assert page.text_edit.toPlainText() == "dropped words"


def test_dropping_plain_text_normalizes_line_endings(qtbot, qapp, recording_driver_factory):
    from PySide6.QtCore import QMimeData

    page, _controller, _kb = _make_page(qtbot, qapp, recording_driver_factory)
    mime = QMimeData()
    mime.setText("one\r\ntwo")
    _drop(page, mime)
    assert page.text_edit.toPlainText() == "one\ntwo"


def test_dropping_a_txt_file_replaces_the_text_with_its_contents(
    qtbot, qapp, recording_driver_factory, tmp_path
):
    page, _controller, _kb = _make_page(qtbot, qapp, recording_driver_factory)
    page.set_text("what was there before")
    dropped = tmp_path / "essay.txt"
    dropped.write_text("From the file.\nSecond line.", encoding="utf-8")

    with qtbot.waitSignal(page.text_edit.fileDropped, timeout=1000) as blocker:
        event = _drop(page, _file_mime(dropped))

    assert blocker.args == ["From the file.\nSecond line."]
    assert page.text_edit.toPlainText() == "From the file.\nSecond line."
    assert event.isAccepted()
    assert "file://" not in page.text_edit.toPlainText()


def test_dropping_a_markdown_file_loads_its_contents(qtbot, qapp, recording_driver_factory, tmp_path):
    page, _controller, _kb = _make_page(qtbot, qapp, recording_driver_factory)
    dropped = tmp_path / "notes.MD"  # the suffix check is case-insensitive
    dropped.write_text("# Heading\n\nBody text.", encoding="utf-8")
    _drop(page, _file_mime(dropped))
    assert page.text_edit.toPlainText() == "# Heading\n\nBody text."


def test_dropping_an_unsupported_file_inserts_nothing_and_loads_nothing(
    qtbot, qapp, recording_driver_factory, tmp_path
):
    page, _controller, _kb = _make_page(qtbot, qapp, recording_driver_factory)
    page.set_text("existing text")
    dropped = []
    page.text_edit.fileDropped.connect(dropped.append)
    image = tmp_path / "picture.png"
    image.write_bytes(b"\x89PNG\r\n")

    event = _drop(page, _file_mime(image))

    assert page.text_edit.toPlainText() == "existing text"
    assert dropped == []
    assert not event.isAccepted()


def test_dropping_a_web_link_inserts_nothing(qtbot, qapp, recording_driver_factory):
    from PySide6.QtCore import QMimeData, QUrl

    page, _controller, _kb = _make_page(qtbot, qapp, recording_driver_factory)
    page.set_text("existing text")
    mime = QMimeData()
    mime.setUrls([QUrl("https://example.com/page")])
    event = _drop(page, mime)
    assert page.text_edit.toPlainText() == "existing text"
    assert not event.isAccepted()


def test_dropping_a_link_that_also_carries_text_still_inserts_nothing(
    qtbot, qapp, recording_driver_factory
):
    # Dragging a link out of a browser puts both the URL and its text on the drag.
    from PySide6.QtCore import QMimeData, QUrl

    page, _controller, _kb = _make_page(qtbot, qapp, recording_driver_factory)
    mime = QMimeData()
    mime.setUrls([QUrl("https://example.com/page")])
    mime.setText("https://example.com/page")
    _drop(page, mime)
    assert page.text_edit.toPlainText() == ""


def test_dropping_an_unreadable_txt_file_inserts_nothing(qtbot, qapp, recording_driver_factory, tmp_path):
    page, _controller, _kb = _make_page(qtbot, qapp, recording_driver_factory)
    page.set_text("existing text")
    broken = tmp_path / "binary.txt"
    broken.write_bytes(b"\xff\xfe\x00 not utf-8 \x80\x81")
    _drop(page, _file_mime(broken))
    assert page.text_edit.toPlainText() == "existing text"


def test_dropping_a_missing_txt_file_inserts_nothing(qtbot, qapp, recording_driver_factory, tmp_path):
    page, _controller, _kb = _make_page(qtbot, qapp, recording_driver_factory)
    page.set_text("existing text")
    _drop(page, _file_mime(tmp_path / "gone.txt"))
    assert page.text_edit.toPlainText() == "existing text"


def test_drag_enter_accepts_a_txt_file_and_text_but_refuses_other_files(
    qtbot, qapp, recording_driver_factory, tmp_path
):
    from PySide6.QtCore import QMimeData, QPoint
    from PySide6.QtCore import Qt as QtCoreQt
    from PySide6.QtGui import QDragEnterEvent

    page, _controller, _kb = _make_page(qtbot, qapp, recording_driver_factory)

    def enter(mime) -> bool:
        event = QDragEnterEvent(
            QPoint(10, 10),
            QtCoreQt.DropAction.CopyAction,
            mime,
            QtCoreQt.MouseButton.LeftButton,
            QtCoreQt.KeyboardModifier.NoModifier,
        )
        event.setAccepted(False)
        page.text_edit.dragEnterEvent(event)
        return event.isAccepted()

    text_file = tmp_path / "a.txt"
    text_file.write_text("x")
    image = tmp_path / "a.png"
    image.write_bytes(b"x")
    text_mime = QMimeData()
    text_mime.setText("some text")

    assert enter(_file_mime(text_file)) is True
    assert enter(text_mime) is True
    assert enter(_file_mime(image)) is False


def test_pasting_a_copied_file_inserts_nothing(qtbot, qapp, recording_driver_factory, tmp_path):
    # Copy a file in the file manager, paste into the box: no path, no file:// URL.
    page, _controller, _kb = _make_page(qtbot, qapp, recording_driver_factory)
    page.set_text("existing text")
    note = tmp_path / "note.txt"
    note.write_text("x")
    mime = _file_mime(note)
    assert page.text_edit.canInsertFromMimeData(mime) is False
    page.text_edit.insertFromMimeData(mime)
    assert page.text_edit.toPlainText() == "existing text"

    # ... even when the file manager also supplies the path as text.
    mime.setText(str(note))
    assert page.text_edit.canInsertFromMimeData(mime) is False
    page.text_edit.insertFromMimeData(mime)
    assert page.text_edit.toPlainText() == "existing text"


def test_pasting_a_bare_link_reference_inserts_nothing(qtbot, qapp, recording_driver_factory):
    from PySide6.QtCore import QMimeData, QUrl

    page, _controller, _kb = _make_page(qtbot, qapp, recording_driver_factory)
    mime = QMimeData()
    mime.setUrls([QUrl("https://example.com/page")])
    assert page.text_edit.canInsertFromMimeData(mime) is False
    page.text_edit.insertFromMimeData(mime)
    assert page.text_edit.toPlainText() == ""


def test_pasting_a_web_link_that_comes_with_its_own_text_still_works(
    qtbot, qapp, recording_driver_factory
):
    # Safari puts both a URL and the URL's text on the clipboard; the text is what the user
    # copied and expects to paste.
    from PySide6.QtCore import QMimeData, QUrl

    page, _controller, _kb = _make_page(qtbot, qapp, recording_driver_factory)
    mime = QMimeData()
    mime.setUrls([QUrl("https://example.com/page")])
    mime.setText("https://example.com/page")
    assert page.text_edit.canInsertFromMimeData(mime) is True
    page.text_edit.insertFromMimeData(mime)
    assert page.text_edit.toPlainText() == "https://example.com/page"


def test_plain_text_can_always_be_inserted(qtbot, qapp, recording_driver_factory):
    from PySide6.QtCore import QMimeData

    page, _controller, _kb = _make_page(qtbot, qapp, recording_driver_factory)
    mime = QMimeData()
    mime.setText("hello")
    assert page.text_edit.canInsertFromMimeData(mime) is True
