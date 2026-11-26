"""Request checks for a web app that must only serve this PC (R5-4).

Binding to 127.0.0.1 keeps other machines out. Two browser-based attacks can
still reach a localhost server, so we also check:
- Host header: blocks DNS-rebinding (a page on evil.example resolving to
  127.0.0.1 would send Host: evil.example).
- Origin / Referer on POST: blocks cross-site form posts from other web pages
  (e.g. a page silently pressing "もう1本" and spending usage).
"""

from __future__ import annotations

from urllib.parse import urlsplit

HOST = "127.0.0.1"
PORT = 8765
ALLOWED_HOSTS = frozenset({f"127.0.0.1:{PORT}", f"localhost:{PORT}", "127.0.0.1", "localhost"})
# The test client sends Host: testserver.
_TEST_HOSTS = frozenset({"testserver"})


def host_allowed(host_header: str | None, *, allow_test: bool = False) -> bool:
    if not host_header:
        return False
    host = host_header.strip().lower()
    return host in ALLOWED_HOSTS or (allow_test and host in _TEST_HOSTS)


def origin_allowed(origin: str | None, referer: str | None, *, allow_test: bool = False) -> bool:
    """True if a state-changing request comes from this app's own pages.

    Browsers send Origin on form POSTs; Referer is the fallback. If neither is
    present (some privacy settings strip both) the request is allowed, since a
    cross-site browser POST always carries an Origin header in modern browsers.
    """
    value = origin or referer
    if not value:
        return True
    if value == "null":
        return False
    parts = urlsplit(value)
    if parts.scheme not in ("http", "https"):
        return False
    return host_allowed(parts.netloc, allow_test=allow_test)
