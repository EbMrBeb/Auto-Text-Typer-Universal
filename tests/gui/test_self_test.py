"""Tests for the `--self-test` and `--version` CLI entry points.

Run in a subprocess (not by importing `auto_essay_typer.gui.__main__` and
calling `main()` in-process): `--self-test` constructs and tears down a real
QApplication, which -- unlike a page/dialog test -- must not collide with
the session-scoped `qapp` pytest-qt fixture other tests in this suite share.
"""

from __future__ import annotations

import os
import subprocess
import sys


def _run(args: list[str]) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    return subprocess.run(
        [sys.executable, "-m", "auto_essay_typer.gui", *args],
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )


def test_self_test_returns_0_and_prints_selftest_ok():
    result = _run(["--self-test"])
    assert result.returncode == 0, result.stdout + result.stderr
    assert "SELFTEST OK" in result.stdout
    assert "actions" in result.stdout
    assert "ms estimated" in result.stdout


def test_version_flag_prints_name_and_version():
    result = _run(["--version"])
    assert result.returncode == 0
    from auto_essay_typer import __version__

    assert result.stdout.strip() == f"Auto Text Typer {__version__}"


def test_main_self_test_returns_0_in_process(qtbot, qapp, capsys):
    """The literal contract from the module spec: main(["--self-test"]) == 0.

    Runs in-process (reusing the session's `qapp`, exactly as `_self_test()`'s
    own `QApplication.instance() or QApplication(...)` guard is designed to
    allow) so it also verifies that guard works when a QApplication already
    exists, which is exactly pytest-qt's situation for every other test here.
    """
    from auto_essay_typer.gui.__main__ import main

    assert main(["--self-test"]) == 0
    out = capsys.readouterr().out
    assert "SELFTEST OK" in out
