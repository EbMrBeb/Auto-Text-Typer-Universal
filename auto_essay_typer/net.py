"""A verifying SSL context that works the same way across macOS, Windows,
Linux, and a PyInstaller-frozen build of this app.

Two real-world situations motivate this module rather than relying on
``ssl.create_default_context()``'s own platform defaults:

* The python.org installer for macOS ships its own OpenSSL and does *not*
  wire it up to the macOS Keychain -- ``ssl.get_default_verify_paths()``
  reports no usable ``cafile``/``capath`` at all, so every HTTPS request
  fails with ``CERTIFICATE_VERIFY_FAILED`` regardless of the system's own
  (perfectly valid) trust settings.
* A PyInstaller bundle carries its own OpenSSL too, with no usable default
  CA store either -- the same failure would hit every end user on every
  platform this app ships for, not just this one Mac.

Three layers, tried in order, each only used if the one before it is
unavailable or fails:

1. **Preferred: ``truststore.SSLContext``.** Verifies against the
   operating system's own trust store (the macOS Security framework, the
   Windows certificate store, or the Linux system bundle) instead of a
   bundled list of CAs. This is what a user expects "verified" to mean on
   their own machine, and it is the *only* layer that honors a custom root
   certificate a school or corporate network has installed system-wide to
   intercept TLS -- a certifi-only fallback would reject that connection
   even though the OS itself trusts it.
2. **A well-known Linux system CA bundle, if one exists on disk.** A frozen
   interpreter's compiled-in OpenSSL default paths can point at the build
   machine's paths rather than the target system's, so before falling back
   to the bundled certifi data this tries the paths a Linux system's own
   package manager actually installs a CA bundle at.
3. **Fallback: ``certifi``.** A CA bundle shipped as an ordinary Python
   package, guaranteed importable regardless of platform or how the app is
   packaged.

Verification is never disabled at any layer: every context this module
hands back has ``verify_mode == ssl.CERT_REQUIRED`` and
``check_hostname == True``. There is no code path here that silently
downgrades to an unverified connection.
"""

from __future__ import annotations

import ssl
import threading
from pathlib import Path

# Where a Linux distribution's package manager actually installs the system
# CA bundle -- varies by distro family, so all of the common ones are tried.
_LINUX_SYSTEM_CA_BUNDLES: tuple[str, ...] = (
    "/etc/ssl/certs/ca-certificates.crt",  # Debian, Ubuntu, Gentoo, Arch
    "/etc/pki/tls/certs/ca-bundle.crt",  # Fedora, RHEL, CentOS
    "/etc/ssl/cert.pem",  # Alpine, some BSDs
    "/etc/ssl/ca-bundle.pem",  # openSUSE
)

_lock = threading.Lock()
_cached_context: ssl.SSLContext | None = None
_cached_backend: str | None = None


def _is_verifying(context: ssl.SSLContext) -> bool:
    return context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname


def _try_truststore() -> tuple[ssl.SSLContext, str] | None:
    """The OS-native trust store, via the ``truststore`` package.

    Both an unavailable import and a construction failure (a platform
    ``truststore`` cannot introspect, a sandboxed environment with no
    reachable trust store API, ...) are treated the same way: this layer
    simply isn't usable here, so the caller falls through to the next one.
    Nothing about a failure at this layer is itself an error worth raising.
    """
    try:
        import truststore
    except ImportError:
        return None

    try:
        context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    except Exception:
        return None

    if not _is_verifying(context):
        return None
    return context, "system trust store (truststore)"


def _try_linux_system_bundle(
    candidates: tuple[str, ...] | None = None,
) -> tuple[ssl.SSLContext, str] | None:
    """The first well-known Linux system CA bundle that exists on disk.

    ``candidates`` defaults to :data:`_LINUX_SYSTEM_CA_BUNDLES`, looked up
    at call time (not bound as a default argument) so tests can monkeypatch
    that module attribute, or pass an explicit list, without depending on
    the real filesystem.
    """
    if candidates is None:
        candidates = _LINUX_SYSTEM_CA_BUNDLES

    for path in candidates:
        if not Path(path).exists():
            continue
        try:
            context = ssl.create_default_context(cafile=path)
        except Exception:
            # A path that exists but isn't a loadable PEM bundle -- keep
            # trying the remaining candidates rather than giving up.
            continue
        if _is_verifying(context):
            return context, f"system CA bundle ({path})"
    return None


def _try_certifi() -> tuple[ssl.SSLContext, str]:
    """The certifi-bundled CA list: always available, the final fallback."""
    import certifi

    context = ssl.create_default_context(cafile=certifi.where())
    return context, "certifi bundle"


def _build_ssl_context() -> tuple[ssl.SSLContext, str]:
    """Run the layered fallback once, uncached. See the module docstring."""
    for builder in (_try_truststore, _try_linux_system_bundle):
        result = builder()
        if result is not None:
            return result
    return _try_certifi()


def create_ssl_context() -> ssl.SSLContext:
    """A verifying ``ssl.SSLContext``, built on first use and cached after.

    Safe to call from multiple threads: the context is built at most once
    (a lock guards the first build), and every call -- concurrent or not --
    after that returns the same cached instance. Reusing one context across
    many connections is the normal, intended way to use ``ssl.SSLContext``;
    there is no reason to rebuild it per fetch.
    """
    global _cached_context, _cached_backend
    if _cached_context is None:
        with _lock:
            if _cached_context is None:
                context, backend = _build_ssl_context()
                _cached_backend = backend
                _cached_context = context
    return _cached_context


def describe_ssl_backend() -> str:
    """Which trust store :func:`create_ssl_context` picked, e.g. ``"system
    trust store (truststore)"`` or ``"certifi bundle"`` -- for diagnostics
    and error messages, not for any decision this module makes itself.
    """
    create_ssl_context()
    assert _cached_backend is not None  # populated by create_ssl_context() above
    return _cached_backend
