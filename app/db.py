"""SQLite schema creation and connection helpers.

The schema follows design section 4. job_runs.run_date carries a UNIQUE
constraint so the daily job cannot run (and notify) twice on the same day.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS papers (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    doi           TEXT UNIQUE,
    title         TEXT NOT NULL,
    authors_json  TEXT,
    journal       TEXT,
    issn          TEXT,
    published_date TEXT,
    abstract      TEXT,
    source        TEXT,          -- crossref | chemrxiv | pubmed | manual
    url           TEXT,
    oa_pdf_url    TEXT,
    genre         TEXT,          -- electrochem | other
    fetched_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS introductions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    paper_id      INTEGER NOT NULL REFERENCES papers(id),
    intro_date    TEXT NOT NULL,  -- DATE (YYYY-MM-DD)
    kind          TEXT NOT NULL,  -- daily | extra
    body_json     TEXT,           -- English sections, Japanese points, vocab, question
    backend       TEXT,           -- claude_code | api
    model         TEXT,
    input_tokens  INTEGER,
    output_tokens INTEGER,
    status        TEXT NOT NULL,  -- ok | failed
    slack_status  TEXT,           -- ok | failed | skipped
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS feedback (
    paper_id   INTEGER NOT NULL UNIQUE REFERENCES papers(id),
    rating     INTEGER NOT NULL,       -- +1 | -1
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS library (
    paper_id   INTEGER NOT NULL UNIQUE REFERENCES papers(id),
    pdf_path   TEXT,
    status     TEXT,                   -- want | reading | done
    notes      TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS job_runs (
    run_date    TEXT PRIMARY KEY,      -- DATE, UNIQUE guards double runs
    started_at  TEXT,
    finished_at TEXT,
    result      TEXT,                  -- ok | failed | partial
    log         TEXT
);

CREATE INDEX IF NOT EXISTS idx_papers_genre ON papers(genre);
CREATE INDEX IF NOT EXISTS idx_papers_issn ON papers(issn);
CREATE INDEX IF NOT EXISTS idx_intros_date ON introductions(intro_date);
CREATE INDEX IF NOT EXISTS idx_intros_paper ON introductions(paper_id);
"""


def connect(db_path: Path | str) -> sqlite3.Connection:
    """Open a connection with row access by name and foreign keys enabled."""
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(db_path: Path | str) -> sqlite3.Connection:
    """Create the schema if needed and return an open connection."""
    conn = connect(db_path)
    conn.executescript(SCHEMA)
    conn.commit()
    return conn
