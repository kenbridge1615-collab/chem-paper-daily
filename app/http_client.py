"""Thin HTTP layer shared by the source clients.

The source clients never import httpx directly. They receive a `Fetcher`
callable, which keeps them trivially testable with a fake fetcher and keeps the
httpx dependency isolated to one place. Production code builds the default
fetcher with `httpx_fetcher()`, which imports httpx lazily so that modules can
be imported (and unit-tested) in environments where httpx is not installed.
"""

from __future__ import annotations

import json as _json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol


@dataclass
class Response:
    """A minimal HTTP response the source clients rely on."""

    status_code: int
    text: str
    _url: str = ""

    def json(self) -> Any:
        return _json.loads(self.text)

    @property
    def ok(self) -> bool:
        return 200 <= self.status_code < 300


class Fetcher(Protocol):
    """A callable that performs a GET-style request and returns a Response."""

    def __call__(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = ...,
        headers: dict[str, str] | None = ...,
    ) -> Response: ...


def httpx_fetcher(
    *, timeout: float = 30.0, default_headers: dict[str, str] | None = None
) -> Callable[..., Response]:
    """Build a Fetcher backed by httpx.

    httpx is imported here, not at module load, so unit tests that inject their
    own fetcher do not require httpx to be installed.
    """
    import httpx  # noqa: PLC0415  (intentional lazy import)

    client = httpx.Client(timeout=timeout, headers=default_headers or {})

    def fetch(
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Response:
        resp = client.get(url, params=params, headers=headers)
        return Response(status_code=resp.status_code, text=resp.text, _url=str(resp.url))

    return fetch
