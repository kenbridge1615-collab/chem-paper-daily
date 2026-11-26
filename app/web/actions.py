"""State-changing operations behind the web forms. Pure functions, no FastAPI.

Each takes an open connection, config, and (where the network is involved) an
injectable fetcher or job runner so the logic can be unit-tested.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import BinaryIO

from app import select, storage
from app.config import Config
from app.http_client import Fetcher
from app.models import Paper
from app.sources import crossref, unpaywall
from app.web import uploads
from app.web.views import normalize_doi

log = logging.getLogger("chem_paper_daily.web")


class ActionError(ValueError):
    """Raised with a Japanese message suitable for showing to the user."""


def set_feedback(conn: sqlite3.Connection, paper_id: int, rating: int) -> None:
    if rating not in (1, -1):
        raise ActionError("評価は 👍 か 👎 のどちらかです。")
    if storage.get_paper(conn, paper_id) is None:
        raise ActionError("論文が見つかりません。")
    storage.set_feedback(conn, paper_id, rating)


def add_to_library(conn: sqlite3.Connection, paper_id: int) -> None:
    """Add an existing paper to the shelf (status 'want'); no-op if present."""
    if storage.get_paper(conn, paper_id) is None:
        raise ActionError("論文が見つかりません。")
    if storage.get_library_entry(conn, paper_id) is None:
        storage.upsert_library(conn, paper_id)


def update_library(conn: sqlite3.Connection, paper_id: int, *, status: str, notes: str) -> None:
    if storage.get_paper(conn, paper_id) is None:
        raise ActionError("論文が見つかりません。")
    if status not in storage.LIBRARY_STATUSES:
        raise ActionError("読書状態の値が不正です。")
    storage.upsert_library(conn, paper_id, status=status, notes=notes[:20000])


def attach_pdf(
    conn: sqlite3.Connection,
    paper_id: int,
    *,
    filename: str | None,
    stream: BinaryIO,
    pdf_dir: Path,
) -> str:
    """Validate and store an uploaded PDF for a paper; returns the file name.

    The extension check uses the uploader's file name; the stored name is
    generated from the DOI / id. The paper is added to the shelf if needed.
    """
    paper = storage.get_paper(conn, paper_id)
    if paper is None:
        raise ActionError("論文が見つかりません。")
    uploads.check_pdf_filename(filename)
    name = uploads.safe_pdf_filename(paper.doi, paper.id)
    uploads.save_pdf(pdf_dir, name, stream)
    storage.upsert_library(conn, paper_id, pdf_path=name)
    return name


@dataclass
class AddResult:
    paper_id: int
    created: bool          # False if the DOI was already in the database
    metadata_found: bool   # False if Crossref lookup failed and we used fallbacks


def add_by_doi(
    conn: sqlite3.Connection,
    cfg: Config,
    fetch: Fetcher | None,
    doi_input: str,
    *,
    title: str = "",
) -> AddResult:
    """Add a paper to the shelf by DOI (requirements R6-1).

    Looks up metadata on Crossref and an OA copy on Unpaywall. If the lookup
    fails (offline, unknown DOI), the paper is still added using the title the
    user typed, or the DOI itself as a placeholder title.
    """
    doi = normalize_doi(doi_input)
    if doi is None:
        raise ActionError("DOI の形式が正しくありません（例: 10.1021/jacs.5b00001）。")

    existing = storage.find_paper_by_doi(conn, doi)
    if existing is not None:
        add_to_library(conn, existing.id)
        return AddResult(existing.id, created=False, metadata_found=True)

    paper: Paper | None = None
    if fetch is not None:
        try:
            paper = crossref.fetch_work(fetch, doi, contact_email=cfg.contact_email)
        except Exception as exc:  # network or parse failure: fall back below
            log.warning("Crossref lookup failed for %s: %s", doi, exc)
    metadata_found = paper is not None
    if paper is None:
        paper = Paper(doi=doi, title=title.strip() or doi, source="manual",
                      url=f"https://doi.org/{doi}")
    elif title.strip() and paper.title == "(untitled)":
        paper.title = title.strip()

    if fetch is not None and not paper.oa_pdf_url:
        try:
            paper.oa_pdf_url = unpaywall.fetch_oa_pdf_url(fetch, doi, email=cfg.contact_email)
        except Exception as exc:
            log.warning("Unpaywall lookup failed for %s: %s", doi, exc)

    paper.genre = select.classify_genre(paper, cfg.electrochem_keywords)
    paper_id = storage.upsert_paper(conn, paper)
    add_to_library(conn, paper_id)
    return AddResult(paper_id, created=True, metadata_found=metadata_found)


def request_extra(
    conn: sqlite3.Connection,
    cfg: Config,
    *,
    run_job: Callable[..., object],
    today: str | None = None,
) -> str:
    """'もう1本': run the daily job's extra path if under the daily cap.

    Returns a flash-message key (see views.FLASH_MESSAGES). The cap is checked
    here first so a click at the limit does not trigger collection at all;
    daily_job.run_once enforces it again.
    """
    today = today or date.today().isoformat()
    if storage.count_intros_on(conn, today) >= cfg.max_intros_per_day:
        return "extra_limit"
    outcome = run_job(cfg, kind="extra", run_date=today)
    result = getattr(outcome, "result", "failed")
    if result == "ok":
        return "extra_ok"
    if result == "skipped":
        return "extra_limit"
    if result == "partial":
        return "extra_none"
    return "extra_failed"
