"""Crossref client: fetch recent works per journal ISSN.

Uses the works endpoint filtered by publication date. A `mailto` is attached so
requests use the polite pool. Some journals (notably Elsevier titles) do not
expose abstracts via Crossref; enrichment fills those in later.

Endpoint used (confirm against current Crossref REST API docs when running for
real): https://api.crossref.org/journals/{issn}/works
"""

from __future__ import annotations

import html
import re
from datetime import date, timedelta

from app.http_client import Fetcher
from app.models import Paper

API_BASE = "https://api.crossref.org"
# Fields requested to keep responses small.
_SELECT = "DOI,title,author,container-title,ISSN,published,abstract,URL"


def _strip_jats(abstract: str | None) -> str | None:
    """Crossref abstracts arrive as JATS XML; reduce to plain text."""
    if not abstract:
        return None
    text = re.sub(r"<[^>]+>", " ", abstract)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text or None


def _parse_date(published: dict | None) -> str | None:
    if not published:
        return None
    parts = published.get("date-parts") or []
    if not parts or not parts[0]:
        return None
    ymd = parts[0] + [1, 1]  # pad missing month/day
    try:
        return date(ymd[0], ymd[1], ymd[2]).isoformat()
    except (ValueError, TypeError):
        return None


def _authors(author_list: list[dict] | None) -> list[str]:
    names = []
    for a in author_list or []:
        given = a.get("given", "")
        family = a.get("family", "")
        name = " ".join(p for p in (given, family) if p).strip()
        if name:
            names.append(name)
    return names


def _item_to_paper(item: dict, issn: str, journal_name: str | None) -> Paper:
    title_list = item.get("title") or []
    title = title_list[0] if title_list else "(untitled)"
    container = item.get("container-title") or []
    journal = container[0] if container else journal_name
    doi = item.get("DOI")
    return Paper(
        doi=doi.lower() if doi else None,
        title=title,
        authors=_authors(item.get("author")),
        journal=journal,
        issn=issn,
        published_date=_parse_date(item.get("published")),
        abstract=_strip_jats(item.get("abstract")),
        source="crossref",
        url=item.get("URL") or (f"https://doi.org/{doi}" if doi else None),
    )


def fetch_journal(
    fetch: Fetcher,
    issn: str,
    *,
    contact_email: str,
    lookback_days: int = 3,
    journal_name: str | None = None,
    rows: int = 50,
    today: date | None = None,
) -> list[Paper]:
    """Fetch works published in the last `lookback_days` for one ISSN."""
    today = today or date.today()
    from_date = (today - timedelta(days=lookback_days)).isoformat()
    params = {
        "filter": f"from-pub-date:{from_date}",
        "select": _SELECT,
        "rows": str(rows),
        "sort": "published",
        "order": "desc",
        "mailto": contact_email,
    }
    resp = fetch(f"{API_BASE}/journals/{issn}/works", params=params)
    if not resp.ok:
        raise RuntimeError(f"Crossref returned status {resp.status_code} for ISSN {issn}")
    data = resp.json()
    items = data.get("message", {}).get("items", [])
    return [_item_to_paper(it, issn, journal_name) for it in items]
