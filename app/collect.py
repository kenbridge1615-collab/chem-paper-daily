"""Collect new papers from all sources and enrich missing metadata.

Each source runs inside its own try/except so one source failing does not stop
the others (requirements R1-3). Deduplication by DOI happens at storage time
(storage.upsert_paper) and in-memory within a single run.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, field

from app.config import Config
from app.http_client import Fetcher
from app.models import Paper
from app.sources import chemrxiv, crossref, pubmed, semanticscholar, unpaywall

log = logging.getLogger("chem_paper_daily.collect")


@dataclass
class CollectResult:
    collected: list[Paper] = field(default_factory=list)
    source_errors: dict[str, str] = field(default_factory=dict)
    enriched_abstracts: int = 0
    oa_found: int = 0


def _dedup(papers: list[Paper]) -> list[Paper]:
    """Collapse duplicates by DOI within one run, keeping the first with an
    abstract where possible. Papers without a DOI are all kept."""
    by_doi: dict[str, Paper] = {}
    no_doi: list[Paper] = []
    for p in papers:
        if not p.doi:
            no_doi.append(p)
            continue
        existing = by_doi.get(p.doi)
        if existing is None:
            by_doi[p.doi] = p
        elif not existing.has_abstract() and p.has_abstract():
            by_doi[p.doi] = p
    return list(by_doi.values()) + no_doi


def collect_all(fetch: Fetcher, cfg: Config) -> CollectResult:
    """Run every source, isolating failures, and return the merged papers."""
    result = CollectResult()
    all_papers: list[Paper] = []

    # Crossref, per configured journal ISSN.
    for journal in cfg.journals:
        try:
            papers = crossref.fetch_journal(
                fetch,
                journal.issn,
                contact_email=cfg.contact_email,
                lookback_days=cfg.lookback_days,
                journal_name=journal.name,
            )
            all_papers.extend(papers)
        except Exception as exc:  # isolate per journal
            result.source_errors[f"crossref:{journal.issn}"] = str(exc)
            log.warning("Crossref failed for %s: %s", journal.issn, exc)

    # ChemRxiv.
    try:
        all_papers.extend(
            chemrxiv.fetch_recent(fetch, lookback_days=cfg.lookback_days)
        )
    except Exception as exc:
        result.source_errors["chemrxiv"] = str(exc)
        log.warning("ChemRxiv failed: %s", exc)

    # PubMed.
    try:
        all_papers.extend(
            pubmed.fetch_recent(
                fetch,
                cfg.pubmed_query,
                contact_email=cfg.contact_email,
                lookback_days=cfg.lookback_days,
            )
        )
    except Exception as exc:
        result.source_errors["pubmed"] = str(exc)
        log.warning("PubMed failed: %s", exc)

    result.collected = _dedup(all_papers)
    return result


def enrich(fetch: Fetcher, cfg: Config, papers: list[Paper]) -> CollectResult:
    """Fill missing abstracts (Semantic Scholar) and OA PDF URLs (Unpaywall).

    Mutates the papers in place and reports counts. Failures per paper are
    logged and skipped, never fatal.
    """
    result = CollectResult(collected=papers)
    for p in papers:
        if not p.doi:
            continue
        if not p.has_abstract():
            try:
                abstract = semanticscholar.fetch_abstract(fetch, p.doi)
                if abstract:
                    p.abstract = abstract
                    result.enriched_abstracts += 1
            except Exception as exc:
                log.info("Semantic Scholar enrichment failed for %s: %s", p.doi, exc)
        if not p.oa_pdf_url:
            try:
                oa = unpaywall.fetch_oa_pdf_url(fetch, p.doi, email=cfg.contact_email)
                if oa:
                    p.oa_pdf_url = oa
                    result.oa_found += 1
            except Exception as exc:
                log.info("Unpaywall lookup failed for %s: %s", p.doi, exc)
    return result


def store(conn: sqlite3.Connection, papers: list[Paper]) -> list[int]:
    """Persist papers (dedup by DOI in storage). Returns stored paper ids."""
    from app import storage

    ids = []
    for p in papers:
        ids.append(storage.upsert_paper(conn, p))
    return ids
