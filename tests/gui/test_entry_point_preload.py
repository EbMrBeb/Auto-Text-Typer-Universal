"""``python -m auto_essay_typer.gui`` must build the keyboard backend before anything else.

The real call on the normal launch path cannot be exercised in-process (``main()``
creates its own ``QApplication`` and then blocks in its event loop), but the call
sits above the ``--self-test`` branch, so checking that path covers the same line.
"""

from __future__ import annotations

from auto_essay_typer.gui import __main__ as entry


def test_main_preloads_the_keyboard_backend_on_the_self_test_path(qtbot, qapp, monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(entry, "preload_keyboard_backend", lambda: calls.append("preload"))

    assert entry.main(["--self-test"]) == 0

    assert calls == ["preload"]


def test_version_flag_does_not_build_the_keyboard_backend(monkeypatch, capsys):
    calls: list[str] = []
    monkeypatch.setattr(entry, "preload_keyboard_backend", lambda: calls.append("preload"))

    assert entry.main(["--version"]) == 0

    assert calls == []
    assert "Auto Text Typer" in capsys.readouterr().out
