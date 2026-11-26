"""Semantic Scholar client: fill in a missing abstract by DOI.

Free-tier rate limits apply; the caller spaces requests out. Returns the
abstract string or None.

Endpoint used (confirm against current Semantic Scholar API docs when running):
https://api.semanticscholar.org/graph/v1/paper/DOI:{doi}
"""

from __future__ import annotations

from app.http_client import Fetcher

API_BASE = "https://api.semanticscholar.org/graph/v1"


def fetch_abstract(fetch: Fetcher, doi: str) -> str | None:
    params = {"fields": "abstract"}
    resp = fetch(f"{API_BASE}/paper/DOI:{doi}", params=params)
    if resp.status_code == 404:
        return None
    if not resp.ok:
        raise RuntimeError(f"Semantic Scholar returned status {resp.status_code} for {doi}")
    abstract = resp.json().get("abstract")
    return abstract.strip() if abstract and abstract.strip() else None
