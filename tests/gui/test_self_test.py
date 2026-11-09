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


# -- the TLS trust-store check ----------------------------------------------------------------


def test_self_test_reports_which_tls_trust_store_it_used():
    """The success line names the backend ``auto_essay_typer.net`` settled on.

    Knowing that a *frozen* build fell through to certifi -- rather than reading
    the operating system's own trust store -- is the difference between "TLS
    works" and "TLS works but ignores the custom root this user's school or
    employer installed", so the backend is part of the line the release jobs
    print rather than something only a debugger could recover.
    """
    result = _run(["--self-test"])
    assert result.returncode == 0, result.stdout + result.stderr
    assert "TLS trust: " in result.stdout

    backend = result.stdout.split("TLS trust: ", 1)[1].strip()
    # The three layers net.py can return, in its own words.
    assert backend.startswith(
        ("system trust store", "system CA bundle", "certifi bundle")
    ), f"unrecognized TLS backend: {backend!r}"


def test_self_test_fails_when_the_tls_context_does_not_verify(qtbot, qapp, capsys, monkeypatch):
    """A non-verifying context must fail the gate, not be reported as OK.

    This is the failure the check exists for: an app that cannot verify
    certificates fails every Google Docs import for every user, and before this
    check the release workflow would have published it green. ``_self_test``
    resolves ``create_ssl_context`` from the module at call time, so patching
    the attribute is enough to stand in for a build whose CA data is unusable.
    """
    import ssl

    from auto_essay_typer import net
    from auto_essay_typer.gui.__main__ import _self_test

    unverified = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    unverified.check_hostname = False  # must come first: CERT_NONE is rejected while it is on
    unverified.verify_mode = ssl.CERT_NONE
    monkeypatch.setattr(net, "create_ssl_context", lambda: unverified)

    assert _self_test() == 1
    out = capsys.readouterr().out
    assert "SELFTEST FAILED" in out
    assert "verify" in out


def test_self_test_checks_tls_without_opening_a_connection(qtbot, qapp, capsys, monkeypatch):
    """The TLS check builds a context and stops there.

    A self-test that actually reached a CA or a Google endpoint would make every
    release job depend on the runner's network and could fail a build for
    reasons that have nothing to do with the build. Any hostname resolution or
    outbound socket during the run fails this test; the offscreen Qt round trip
    needs neither.
    """
    import socket

    from auto_essay_typer.gui.__main__ import _self_test

    def forbidden(*args, **kwargs):
        raise AssertionError("--self-test must not touch the network")

    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)

    assert _self_test() == 0
    assert "SELFTEST OK" in capsys.readouterr().out
