"""Database access helpers: store papers, introductions, feedback, job runs.

These wrap the raw SQLite connection so the rest of the app does not write SQL
inline. Deduplication by DOI lives in `upsert_paper`.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime

from app.models import Paper


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _row_to_paper(row: sqlite3.Row) -> Paper:
    return Paper(
        id=row["id"],
        doi=row["doi"],
        title=row["title"],
        authors=json.loads(row["authors_json"]) if row["authors_json"] else [],
        journal=row["journal"],
        issn=row["issn"],
        published_date=row["published_date"],
        abstract=row["abstract"],
        source=row["source"],
        url=row["url"],
        oa_pdf_url=row["oa_pdf_url"],
        genre=row["genre"],
    )


def upsert_paper(conn: sqlite3.Connection, paper: Paper) -> int:
    """Insert a paper, or merge into the existing row with the same DOI.

    On a DOI collision we keep the existing row but fill in any fields that were
    previously empty (for example an abstract found by a later source). Returns
    the paper id.
    """
    cur = conn.cursor()
    if paper.doi:
        existing = cur.execute(
            "SELECT * FROM papers WHERE doi = ?", (paper.doi,)
        ).fetchone()
        if existing:
            merged = {
                "abstract": existing["abstract"] or paper.abstract,
                "oa_pdf_url": existing["oa_pdf_url"] or paper.oa_pdf_url,
                "genre": existing["genre"] or paper.genre,
                "url": existing["url"] or paper.url,
                "journal": existing["journal"] or paper.journal,
                "issn": existing["issn"] or paper.issn,
                "published_date": existing["published_date"] or paper.published_date,
            }
            cur.execute(
                """UPDATE papers SET abstract=?, oa_pdf_url=?, genre=?, url=?,
                   journal=?, issn=?, published_date=? WHERE id=?""",
                (
                    merged["abstract"], merged["oa_pdf_url"], merged["genre"],
                    merged["url"], merged["journal"], merged["issn"],
                    merged["published_date"], existing["id"],
                ),
            )
            conn.commit()
            return existing["id"]

    cur.execute(
        """INSERT INTO papers
           (doi, title, authors_json, journal, issn, published_date, abstract,
            source, url, oa_pdf_url, genre, fetched_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            paper.doi, paper.title, json.dumps(paper.authors), paper.journal,
            paper.issn, paper.published_date, paper.abstract, paper.source,
            paper.url, paper.oa_pdf_url, paper.genre, _now(),
        ),
    )
    conn.commit()
    return cur.lastrowid


def get_paper(conn: sqlite3.Connection, paper_id: int) -> Paper | None:
    row = conn.execute("SELECT * FROM papers WHERE id = ?", (paper_id,)).fetchone()
    return _row_to_paper(row) if row else None


def update_paper_fields(conn: sqlite3.Connection, paper_id: int, **fields) -> None:
    if not fields:
        return
    cols = ", ".join(f"{k} = ?" for k in fields)
    conn.execute(
        f"UPDATE papers SET {cols} WHERE id = ?", (*fields.values(), paper_id)
    )
    conn.commit()


def introduced_paper_ids(conn: sqlite3.Connection) -> set[int]:
    """Paper ids that already have a successful introduction (never re-pick)."""
    rows = conn.execute(
        "SELECT DISTINCT paper_id FROM introductions WHERE status = 'ok'"
    ).fetchall()
    return {r["paper_id"] for r in rows}


def candidate_papers(conn: sqlite3.Connection) -> list[Paper]:
    """Papers with an abstract that have not yet been introduced."""
    introduced = introduced_paper_ids(conn)
    rows = conn.execute(
        "SELECT * FROM papers WHERE abstract IS NOT NULL AND abstract != ''"
    ).fetchall()
    return [_row_to_paper(r) for r in rows if r["id"] not in introduced]


def recent_intro_genres(conn: sqlite3.Connection, since_date: str) -> list[str]:
    """Genres of papers introduced on or after `since_date` (for balancing)."""
    rows = conn.execute(
        """SELECT p.genre AS genre
           FROM introductions i JOIN papers p ON p.id = i.paper_id
           WHERE i.intro_date >= ? AND i.status = 'ok'""",
        (since_date,),
    ).fetchall()
    return [r["genre"] for r in rows if r["genre"]]


def feedback_map(conn: sqlite3.Connection) -> dict[int, int]:
    rows = conn.execute("SELECT paper_id, rating FROM feedback").fetchall()
    return {r["paper_id"]: r["rating"] for r in rows}


def set_feedback(conn: sqlite3.Connection, paper_id: int, rating: int) -> None:
    conn.execute(
        """INSERT INTO feedback (paper_id, rating, created_at) VALUES (?,?,?)
           ON CONFLICT(paper_id) DO UPDATE SET rating=excluded.rating,
           created_at=excluded.created_at""",
        (paper_id, rating, _now()),
    )
    conn.commit()


def save_introduction(
    conn: sqlite3.Connection,
    *,
    paper_id: int,
    intro_date: str,
    kind: str,
    body: dict | None,
    backend: str,
    model: str,
    input_tokens: int | None,
    output_tokens: int | None,
    status: str,
    slack_status: str | None = None,
) -> int:
    cur = conn.cursor()
    cur.execute(
        """INSERT INTO introductions
           (paper_id, intro_date, kind, body_json, backend, model,
            input_tokens, output_tokens, status, slack_status, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (
            paper_id, intro_date, kind,
            json.dumps(body) if body is not None else None,
            backend, model, input_tokens, output_tokens, status,
            slack_status, _now(),
        ),
    )
    conn.commit()
    return cur.lastrowid


def update_intro_slack_status(conn: sqlite3.Connection, intro_id: int, status: str) -> None:
    conn.execute(
        "UPDATE introductions SET slack_status = ? WHERE id = ?", (status, intro_id)
    )
    conn.commit()


def count_intros_on(conn: sqlite3.Connection, intro_date: str) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM introductions WHERE intro_date = ? AND status = 'ok'",
        (intro_date,),
    ).fetchone()
    return row["n"]


def monthly_token_usage(conn: sqlite3.Connection, year_month: str) -> dict[str, int]:
    """Sum input/output tokens for introductions created in YYYY-MM."""
    row = conn.execute(
        """SELECT COALESCE(SUM(input_tokens),0) AS inp,
                  COALESCE(SUM(output_tokens),0) AS outp
           FROM introductions WHERE substr(created_at,1,7) = ?""",
        (year_month,),
    ).fetchone()
    return {"input_tokens": row["inp"], "output_tokens": row["outp"]}


# --- job_runs: same-day guard ---------------------------------------------

def try_start_run(conn: sqlite3.Connection, run_date: str) -> bool:
    """Claim today's run. Returns False if a run for this date already exists.

    The UNIQUE primary key on run_date makes this atomic, so two processes
    cannot both start the same day's job.
    """
    try:
        conn.execute(
            "INSERT INTO job_runs (run_date, started_at) VALUES (?, ?)",
            (run_date, _now()),
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False


def finish_run(conn: sqlite3.Connection, run_date: str, result: str, log: str) -> None:
    conn.execute(
        "UPDATE job_runs SET finished_at=?, result=?, log=? WHERE run_date=?",
        (_now(), result, log, run_date),
    )
    conn.commit()


def get_run(conn: sqlite3.Connection, run_date: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM job_runs WHERE run_date = ?", (run_date,)
    ).fetchone()
