from PySide6.QtWidgets import QDialog

from auto_essay_typer.gui.permission_dialog import PermissionDialog
from auto_essay_typer.gui.theme import LIGHT_TOKENS
from auto_essay_typer.platforms import OperatingSystem, PermissionStatus

_NOT_GRANTED = PermissionStatus(
    granted=False,
    summary="Auto Text Typer needs Accessibility access.",
    fix_steps=("Open Settings.", "Turn it on.", "Restart the app."),
    can_open_settings=True,
)
_GRANTED = PermissionStatus(granted=True, summary="Granted.", fix_steps=(), can_open_settings=True)


def _dialog(qtbot, status=_NOT_GRANTED, **kwargs):
    kwargs.setdefault("permission_checker", lambda: status)
    kwargs.setdefault("request_prompt", lambda: True)
    kwargs.setdefault("open_settings", lambda: True)
    kwargs.setdefault("current_os", lambda: OperatingSystem.LINUX)  # avoid the macOS prompt call
    dialog = PermissionDialog(status, LIGHT_TOKENS, **kwargs)
    qtbot.addWidget(dialog)
    return dialog


def test_shows_the_summary_and_numbered_steps(qtbot, qapp):
    dialog = _dialog(qtbot)
    assert _NOT_GRANTED.summary in dialog._summary_label.text()
    for step in _NOT_GRANTED.fix_steps:
        assert step in dialog._steps_label.text()


def test_open_settings_button_visible_only_when_can_open_settings(qtbot, qapp):
    shown = _dialog(qtbot, status=_NOT_GRANTED)
    assert shown.open_settings_button.isHidden() is False

    cannot = PermissionStatus(granted=False, summary="x", fix_steps=(), can_open_settings=False)
    hidden = _dialog(qtbot, status=cannot)
    assert hidden.open_settings_button.isHidden() is True


def test_open_settings_button_calls_the_injected_callback(qtbot, qapp):
    calls = []
    dialog = _dialog(qtbot, open_settings=lambda: calls.append(1) or True)
    dialog.open_settings_button.click()
    assert calls == [1]


def test_cancel_rejects_without_granting(qtbot, qapp):
    dialog = _dialog(qtbot)
    with qtbot.waitSignal(dialog.rejected, timeout=1000):
        dialog.cancel_button.click()
    assert dialog.granted() is False


def test_check_again_when_still_not_granted_shows_retry_message_and_stays_open(qtbot, qapp):
    dialog = _dialog(qtbot, permission_checker=lambda: _NOT_GRANTED)
    closed = []
    dialog.finished.connect(lambda _code: closed.append(1))
    dialog.check_again_button.click()
    assert dialog.granted() is False
    assert "not granted" in dialog._retry_label.text().lower()
    assert closed == []  # neither accept() nor reject() was called


def test_check_again_when_now_granted_accepts_and_closes(qtbot, qapp):
    dialog = _dialog(qtbot, permission_checker=lambda: _GRANTED)
    with qtbot.waitSignal(dialog.accepted, timeout=1000):
        dialog.check_again_button.click()
    assert dialog.granted() is True


def test_macos_calls_request_prompt_once_on_open(qtbot, qapp):
    calls = []
    _dialog(
        qtbot,
        current_os=lambda: OperatingSystem.MACOS,
        request_prompt=lambda: calls.append(1) or True,
    )
    assert calls == [1]


def test_without_an_injected_prompt_the_platform_one_is_looked_up_when_the_dialog_opens(qtbot, qapp, monkeypatch):
    # Looked up at that moment, not bound when the module loads, so a test can stand in for the OS
    # prompt -- and one that forgets can no longer put the real one on a developer's screen.
    from auto_essay_typer import platforms

    calls = []
    monkeypatch.setattr(platforms, "request_input_permission_prompt", lambda: calls.append(1) or True)

    _dialog(qtbot, current_os=lambda: OperatingSystem.MACOS, request_prompt=None)

    assert calls == [1]


def test_non_macos_never_calls_request_prompt(qtbot, qapp):
    calls = []
    _dialog(
        qtbot,
        current_os=lambda: OperatingSystem.WINDOWS,
        request_prompt=lambda: calls.append(1) or True,
    )
    assert calls == []
