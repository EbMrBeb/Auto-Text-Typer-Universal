"""Tests for auto_essay_typer.net: the verifying SSL context every network
fetch in this project uses.

``_build_ssl_context()`` (uncached) is used directly for the fallback-chain
tests below so each one is fully isolated -- no test needs to know or reset
what a previous test cached. ``create_ssl_context()`` / ``describe_ssl_backend()``
(the cached public API) get their own dedicated tests via the autouse fixture
that resets the module-level cache before and after every test in this file.

Every fallback-chain test that forces truststore out of the picture also
pins ``_LINUX_SYSTEM_CA_BUNDLES`` to a controlled value (usually ``()``):
several real systems -- this Mac included -- have a file at
``/etc/ssl/cert.pem``, one of the well-known Linux bundle paths this module
also probes, so leaving that layer untouched would make "falls back to
certifi" tests pass or fail depending on what happens to exist on the
machine running them.
"""

from __future__ import annotations

import ssl
import sys
from pathlib import Path

import pytest

from auto_essay_typer import net


@pytest.fixture(autouse=True)
def _reset_ssl_context_cache():
    """Isolate ``create_ssl_context``'s module-level cache per test."""
    net._cached_context = None
    net._cached_backend = None
    yield
    net._cached_context = None
    net._cached_backend = None


# --- create_ssl_context(): the real, unmocked result ---------------------------


def test_create_ssl_context_returns_a_verifying_context():
    context = net.create_ssl_context()
    assert isinstance(context, ssl.SSLContext)
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True


def test_create_ssl_context_is_cached():
    first = net.create_ssl_context()
    second = net.create_ssl_context()
    assert first is second


def test_describe_ssl_backend_does_not_rebuild_an_already_cached_context():
    net.create_ssl_context()
    cached = net._cached_context
    description = net.describe_ssl_backend()
    assert net._cached_context is cached
    assert isinstance(description, str) and description


# --- fallback chain: truststore unavailable -------------------------------------


def test_falls_back_to_certifi_when_truststore_import_fails(monkeypatch):
    monkeypatch.setitem(sys.modules, "truststore", None)
    monkeypatch.setattr(net, "_LINUX_SYSTEM_CA_BUNDLES", ())

    context, backend = net._build_ssl_context()

    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True
    assert backend == "certifi bundle"


def test_falls_back_to_certifi_when_truststore_construction_fails(monkeypatch):
    import truststore

    def _raise(*_args, **_kwargs):
        raise RuntimeError("platform trust store unavailable in this sandbox")

    monkeypatch.setattr(truststore, "SSLContext", _raise)
    monkeypatch.setattr(net, "_LINUX_SYSTEM_CA_BUNDLES", ())

    context, backend = net._build_ssl_context()

    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True
    assert backend == "certifi bundle"


def test_backend_description_reflects_the_certifi_fallback(monkeypatch):
    monkeypatch.setitem(sys.modules, "truststore", None)
    monkeypatch.setattr(net, "_LINUX_SYSTEM_CA_BUNDLES", ())

    assert net.describe_ssl_backend() == "certifi bundle"


# --- fallback chain: Linux system bundle probe ----------------------------------


def test_uses_linux_system_bundle_when_a_candidate_path_exists(tmp_path, monkeypatch):
    import certifi

    monkeypatch.setitem(sys.modules, "truststore", None)  # force past layer 1
    bundle = tmp_path / "ca-certificates.crt"
    bundle.write_bytes(Path(certifi.where()).read_bytes())  # a real, loadable PEM bundle
    monkeypatch.setattr(net, "_LINUX_SYSTEM_CA_BUNDLES", (str(bundle),))

    context, backend = net._build_ssl_context()

    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True
    assert backend == f"system CA bundle ({bundle})"


def test_linux_system_bundle_probe_skips_a_missing_candidate(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "truststore", None)
    missing = tmp_path / "does-not-exist.crt"
    monkeypatch.setattr(net, "_LINUX_SYSTEM_CA_BUNDLES", (str(missing),))

    context, backend = net._build_ssl_context()

    assert context.verify_mode == ssl.CERT_REQUIRED
    assert backend == "certifi bundle"


def test_linux_system_bundle_probe_accepts_an_injected_candidate_list(tmp_path):
    """``candidates`` can be passed directly rather than via the module
    attribute -- the dependency-injection path the docstring promises."""
    import certifi

    bundle = tmp_path / "ca-bundle.crt"
    bundle.write_bytes(Path(certifi.where()).read_bytes())

    result = net._try_linux_system_bundle(candidates=(str(bundle),))

    assert result is not None
    context, backend = result
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert backend == f"system CA bundle ({bundle})"


def test_linux_system_bundle_probe_returns_none_with_no_existing_candidates(tmp_path):
    missing = tmp_path / "nope.crt"
    assert net._try_linux_system_bundle(candidates=(str(missing),)) is None
