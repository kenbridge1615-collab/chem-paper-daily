"""Collection: dedup, per-source failure isolation, enrichment, storage."""

from __future__ import annotations

from app import collect, storage
from app.http_client import Response
from app.models import Paper


class FakeFetcher:
    """A fetcher that dispatches by URL substring to canned responses."""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def __call__(self, url, *, params=None, headers=None):
        self.calls.append((url, params))
        for needle, resp in self.routes.items():
            if needle in url:
                if isinstance(resp, Exception):
                    raise resp
                return resp
        return Response(status_code=404, text="{}")


def test_dedup_keeps_abstract_version():
    a = Paper(doi="10.1/x", title="t", abstract=None, source="crossref")
    b = Paper(doi="10.1/x", title="t", abstract="full text", source="pubmed")
    c = Paper(doi=None, title="no doi", abstract="z", source="chemrxiv")
    merged = collect._dedup([a, b, c])
    by_doi = {p.doi: p for p in merged if p.doi}
    assert by_doi["10.1/x"].abstract == "full text"
    assert sum(1 for p in merged if p.doi is None) == 1


def test_source_failure_isolated(cfg):
    # Crossref fails for every ISSN, ChemRxiv/PubMed return empty but succeed.
    routes = {
        "/journals/": RuntimeError("crossref down"),
        "chemrxiv.org": Response(200, '{"itemHits": []}'),
        "esearch": Response(200, '{"esearchresult": {"idlist": []}}'),
    }
    fetch = FakeFetcher(routes)
    result = collect.collect_all(fetch, cfg)
    # Every crossref journal recorded an error, but the run still completed.
    assert len(result.source_errors) >= 1
    assert all("crossref" in k for k in result.source_errors)
    assert result.collected == []


def test_enrich_fills_abstract_and_oa(cfg):
    routes = {
        "semanticscholar.org": Response(200, '{"abstract": "recovered abstract"}'),
        "unpaywall.org": Response(200, '{"best_oa_location": {"url_for_pdf": "http://oa/pdf"}}'),
    }
    fetch = FakeFetcher(routes)
    papers = [Paper(doi="10.1/x", title="t", abstract=None)]
    res = collect.enrich(fetch, cfg, papers)
    assert papers[0].abstract == "recovered abstract"
    assert papers[0].oa_pdf_url == "http://oa/pdf"
    assert res.enriched_abstracts == 1
    assert res.oa_found == 1


def test_store_dedup_by_doi(conn):
    p1 = Paper(doi="10.1/x", title="t", abstract=None, source="crossref")
    p2 = Paper(doi="10.1/x", title="t", abstract="late abstract", source="pubmed")
    id1 = storage.upsert_paper(conn, p1)
    id2 = storage.upsert_paper(conn, p2)
    assert id1 == id2  # same DOI -> same row
    stored = storage.get_paper(conn, id1)
    assert stored.abstract == "late abstract"  # merged in


def test_crossref_parse():
    from app.sources import crossref

    item = {
        "DOI": "10.1021/XYZ",
        "title": ["A Title"],
        "author": [{"given": "A", "family": "Author"}],
        "container-title": ["ACS Sensors"],
        "published": {"date-parts": [[2026, 5, 2]]},
        "abstract": "<jats:p>Hello <b>world</b></jats:p>",
        "URL": "https://doi.org/10.1021/xyz",
    }
    p = crossref._item_to_paper(item, "2379-3694", "ACS Sensors")
    assert p.doi == "10.1021/xyz"  # lowercased
    assert p.abstract == "Hello world"  # JATS stripped
    assert p.published_date == "2026-05-02"
    assert p.authors == ["A Author"]
