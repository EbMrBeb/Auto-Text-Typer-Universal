"""One opt-in test that touches the real network.

Every other test in this suite injects a fake ``fetch`` (see
``tests/test_importers_google.py`` and ``tests/test_net.py``), which proves
the parsing and error-mapping logic but can never catch an environment-level
problem like a broken CA trust store -- exactly the class of bug that shipped
undetected (see ``auto_essay_typer/net.py``). This test exists specifically
to catch that class of bug by performing one real TLS handshake with Google.

Skipped by default: set ``ATT_NETWORK_TESTS=1`` to opt in, e.g.::

    ATT_NETWORK_TESTS=1 .venv/bin/pytest tests/test_net_live.py -v

Also marked ``network`` (registered in ``pyproject.toml``) so it can be
excluded by marker too, with ``-m "not network"``.
"""

from __future__ import annotations

import os

import pytest

from auto_essay_typer.importers.google import NotFoundError, load_from_google

pytestmark = pytest.mark.network

_SKIP_REASON = "opt-in live network test; set ATT_NETWORK_TESTS=1 to run"


@pytest.mark.skipif(os.environ.get("ATT_NETWORK_TESTS") != "1", reason=_SKIP_REASON)
def test_default_fetch_completes_a_verified_tls_handshake_with_google():
    """A nonexistent document id must fail with Google's real 404
    (``NotFoundError``) -- never a certificate or connection error. Getting
    as far as a clean 404 proves the default fetcher's SSL context was
    actually trusted by docs.google.com during a real handshake, which is
    the one thing no faked-fetcher test can demonstrate.
    """
    url = "https://docs.google.com/document/d/THIS_DOES_NOT_EXIST_12345/export?format=html"
    with pytest.raises(NotFoundError):
        load_from_google(url)
