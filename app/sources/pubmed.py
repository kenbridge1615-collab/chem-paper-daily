"""PubMed client: search the electrochem sensor / biosensor area via E-utilities.

Two calls: esearch returns PMIDs for the configured query, efetch returns the
records (title, abstract, journal, dates, DOI). XML is parsed with the stdlib.

Endpoints used (confirm against current NCBI E-utilities docs when running):
https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi
https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi
"""

from __future__ import annotations

from datetime import date
from xml.etree import ElementTree as ET

from app.http_client import Fetcher
from app.models import Paper

API_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"


def _text(el: ET.Element | None) -> str | None:
    if el is None:
        return None
    text = "".join(el.itertext()).strip()
    return text or None


def _parse_article(article: ET.Element) -> Paper:
    medline = article.find("MedlineCitation")
    art = medline.find("Article") if medline is not None else None

    title = _text(art.find("ArticleTitle")) if art is not None else None

    abstract_parts = []
    if art is not None:
        for ab in art.findall("./Abstract/AbstractText"):
            label = ab.get("Label")
            txt = _text(ab)
            if txt:
                abstract_parts.append(f"{label}: {txt}" if label else txt)
    abstract = " ".join(abstract_parts) or None

    journal = None
    issn = None
    if art is not None:
        journal = _text(art.find("./Journal/Title"))
        issn = _text(art.find("./Journal/ISSN"))

    authors = []
    if art is not None:
        for a in art.findall("./AuthorList/Author"):
            last = _text(a.find("LastName"))
            fore = _text(a.find("ForeName"))
            name = " ".join(p for p in (fore, last) if p)
            if name:
                authors.append(name)

    doi = None
    for aid in article.findall(".//ArticleId"):
        if aid.get("IdType") == "doi" and aid.text:
            doi = aid.text.strip().lower()
            break

    pub_date = None
    pd = art.find("./Journal/JournalIssue/PubDate") if art is not None else None
    if pd is not None:
        year = _text(pd.find("Year"))
        month = _text(pd.find("Month")) or "1"
        day = _text(pd.find("Day")) or "1"
        months = {
            "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
            "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
        }
        if year:
            try:
                m = int(month) if month.isdigit() else months.get(month[:3].lower(), 1)
                pub_date = date(int(year), m, int(day) if day.isdigit() else 1).isoformat()
            except (ValueError, TypeError):
                pub_date = year

    return Paper(
        doi=doi,
        title=title or "(untitled)",
        authors=authors,
        journal=journal,
        issn=issn,
        published_date=pub_date,
        abstract=abstract,
        source="pubmed",
        url=f"https://doi.org/{doi}" if doi else None,
    )


def search_pmids(
    fetch: Fetcher, query: str, *, contact_email: str, lookback_days: int = 3, retmax: int = 50
) -> list[str]:
    params = {
        "db": "pubmed",
        "term": query,
        "retmax": str(retmax),
        "retmode": "json",
        "datetype": "pdat",
        "reldate": str(lookback_days),
        "sort": "pub+date",
        "email": contact_email,
        "tool": "chem-paper-daily",
    }
    resp = fetch(f"{API_BASE}/esearch.fcgi", params=params)
    if not resp.ok:
        raise RuntimeError(f"PubMed esearch returned status {resp.status_code}")
    return resp.json().get("esearchresult", {}).get("idlist", [])


def fetch_records(fetch: Fetcher, pmids: list[str], *, contact_email: str) -> list[Paper]:
    if not pmids:
        return []
    params = {
        "db": "pubmed",
        "id": ",".join(pmids),
        "retmode": "xml",
        "email": contact_email,
        "tool": "chem-paper-daily",
    }
    resp = fetch(f"{API_BASE}/efetch.fcgi", params=params)
    if not resp.ok:
        raise RuntimeError(f"PubMed efetch returned status {resp.status_code}")
    root = ET.fromstring(resp.text)
    return [_parse_article(a) for a in root.findall(".//PubmedArticle")]


def fetch_recent(
    fetch: Fetcher, query: str, *, contact_email: str, lookback_days: int = 3, retmax: int = 50
) -> list[Paper]:
    """Full PubMed flow: esearch for PMIDs, then efetch the records."""
    pmids = search_pmids(
        fetch, query, contact_email=contact_email, lookback_days=lookback_days, retmax=retmax
    )
    return fetch_records(fetch, pmids, contact_email=contact_email)
