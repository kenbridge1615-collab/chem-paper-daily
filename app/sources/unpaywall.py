"""Unpaywall client: find an open-access PDF URL by DOI.

The `email` query parameter is required by Unpaywall.

Endpoint used (confirm against current Unpaywall API docs when running):
https://api.unpaywall.org/v2/{doi}?email=...
"""

from __future__ import annotations

from app.http_client import Fetcher

API_BASE = "https://api.unpaywall.org/v2"


def fetch_oa_pdf_url(fetch: Fetcher, doi: str, *, email: str) -> str | None:
    """Return the best OA PDF URL for a DOI, or None if not open access."""
    resp = fetch(f"{API_BASE}/{doi}", params={"email": email})
    if resp.status_code == 404:
        return None
    if not resp.ok:
        raise RuntimeError(f"Unpaywall returned status {resp.status_code} for {doi}")
    data = resp.json()
    best = data.get("best_oa_location") or {}
    return best.get("url_for_pdf") or best.get("url") or None
