"""ChemRxiv client: fetch recent chemistry preprints.

Papers from this source are preprints; the web app marks them as such. The
public API shape is confirmed at run time against the current ChemRxiv API docs.

Endpoint used (confirm when running for real):
https://chemrxiv.org/engage/chemrxiv/public-api/v1/items
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from app.http_client import Fetcher
from app.models import Paper

API_BASE = "https://chemrxiv.org/engage/chemrxiv/public-api/v1"


def _parse_iso(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date().isoformat()
    except (ValueError, TypeError):
        return value[:10] if len(value) >= 10 else None


def _authors(author_list: list[dict] | None) -> list[str]:
    names = []
    for a in author_list or []:
        first = a.get("firstName", "")
        last = a.get("lastName", "")
        name = " ".join(p for p in (first, last) if p).strip()
        if name:
            names.append(name)
    return names


def _item_to_paper(item: dict) -> Paper:
    # The public API wraps each entry; tolerate both wrapped and flat shapes.
    node = item.get("item", item)
    doi = node.get("doi")
    pub = node.get("publishedDate") or node.get("statusDate")
    return Paper(
        doi=doi.lower() if doi else None,
        title=(node.get("title") or "(untitled)").strip(),
        authors=_authors(node.get("authors")),
        journal="ChemRxiv (preprint)",
        issn=None,
        published_date=_parse_iso(pub),
        abstract=(node.get("abstract") or None),
        source="chemrxiv",
        url=(f"https://doi.org/{doi}" if doi else node.get("asset", {}).get("original", {}).get("url")),
    )


def fetch_recent(
    fetch: Fetcher,
    *,
    lookback_days: int = 3,
    limit: int = 50,
    today: date | None = None,
) -> list[Paper]:
    """Fetch recent preprints and keep those within the lookback window."""
    params = {"limit": str(limit), "sort": "PUBLISHED_DATE_DESC"}
    resp = fetch(f"{API_BASE}/items", params=params)
    if not resp.ok:
        raise RuntimeError(f"ChemRxiv returned status {resp.status_code}")
    data = resp.json()
    items = data.get("itemHits") or data.get("items") or []
    papers = [_item_to_paper(it) for it in items]

    cutoff = (today or datetime.now(UTC).date()) - timedelta(days=lookback_days)
    recent = []
    for p in papers:
        if p.published_date:
            try:
                if date.fromisoformat(p.published_date) < cutoff:
                    continue
            except ValueError:
                pass
        recent.append(p)
    return recent
