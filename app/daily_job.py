"""The daily job: collect -> enrich -> select -> introduce -> notify.

Steps run in order; each is wrapped so a failure is recorded rather than
crashing the whole run. The same-day guard (storage.try_start_run) prevents a
second run (and a second Slack post) on the same date, which is what makes the
"catch up at next startup without double-posting" behaviour safe (R4-3).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta

from app import collect, introduce, notify, select, storage
from app.config import Config, load_config
from app.db import init_db
from app.http_client import httpx_fetcher
from app.llm.base import GenerationError
from app.logging_setup import setup_logging

log = logging.getLogger("chem_paper_daily.daily_job")


@dataclass
class JobOutcome:
    ran: bool
    result: str = "ok"  # ok | failed | partial | skipped
    intro_id: int | None = None
    paper_id: int | None = None
    messages: list[str] = field(default_factory=list)

    def note(self, msg: str) -> None:
        self.messages.append(msg)
        log.info(msg)


def _feedback_signals(conn, cfg):
    fmap = storage.feedback_map(conn)
    rated = []
    for paper_id, rating in fmap.items():
        p = storage.get_paper(conn, paper_id)
        if p:
            rated.append((p, rating))
    return select.build_feedback_signals(rated, cfg)


def run_once(
    cfg: Config,
    *,
    conn=None,
    fetch=None,
    run_date: str | None = None,
    kind: str = "daily",
    force: bool = False,
) -> JobOutcome:
    """Run the pipeline once for `run_date` (default today).

    `conn` and `fetch` are injectable for tests. With real settings they default
    to a SQLite connection and an httpx-backed fetcher.
    """
    run_date = run_date or date.today().isoformat()
    owns_conn = conn is None
    if conn is None:
        conn = init_db(cfg.db_path)
    if fetch is None:
        fetch = httpx_fetcher(default_headers={"User-Agent": "chem-paper-daily/0.1"})

    outcome = JobOutcome(ran=False)

    # Same-day guard (daily kind only; "extra" manual picks bypass it but still
    # respect the per-day cap below).
    if kind == "daily" and not force:
        if not storage.try_start_run(conn, run_date):
            outcome.note(f"run for {run_date} already exists; skipping")
            outcome.result = "skipped"
            if owns_conn:
                conn.close()
            return outcome

    # Per-day cap (requirements R2-4).
    if storage.count_intros_on(conn, run_date) >= cfg.max_intros_per_day:
        outcome.note("daily introduction limit reached; skipping")
        outcome.result = "skipped"
        if kind == "daily" and not force:
            storage.finish_run(conn, run_date, "skipped", "\n".join(outcome.messages))
        if owns_conn:
            conn.close()
        return outcome

    outcome.ran = True
    selected = None
    try:
        # 1. Collect.
        collected = collect.collect_all(fetch, cfg)
        outcome.note(
            f"collected {len(collected.collected)} papers; "
            f"source errors: {len(collected.source_errors)}"
        )

        # 2. Enrich (and store).
        collect.enrich(fetch, cfg, collected.collected)
        collect.store(conn, collected.collected)

        # 3. Select from all un-introduced candidates with abstracts.
        candidates = storage.candidate_papers(conn)
        since = (date.fromisoformat(run_date) - timedelta(days=cfg.balance_window_days)).isoformat()
        recent_genres = storage.recent_intro_genres(conn, since)
        fj, fk = _feedback_signals(conn, cfg)
        chosen = select.select_paper(
            candidates, cfg, recent_genres=recent_genres,
            feedback_journals=fj, feedback_keywords=fk,
        )
        if chosen is None:
            outcome.note("no candidate paper available")
            outcome.result = "partial"
            _notify_failure(cfg, None, "no_candidate", conn)
            return _finish(conn, run_date, kind, force, outcome, owns_conn)

        selected = chosen.paper
        storage.update_paper_fields(conn, selected.id, genre=chosen.genre)
        outcome.paper_id = selected.id
        outcome.note(f"selected paper {selected.id} (score {chosen.score}, {chosen.genre})")

        # 4. Introduce.
        try:
            body, gen = introduce.generate_introduction(selected, cfg)
        except GenerationError as exc:
            kind_label = getattr(exc, "kind", "other")
            outcome.note(f"generation failed ({kind_label}): {exc}")
            outcome.result = "failed"
            storage.save_introduction(
                conn, paper_id=selected.id, intro_date=run_date, kind=kind,
                body=None, backend=cfg.llm_backend, model=cfg.llm_model,
                input_tokens=None, output_tokens=None, status="failed",
                slack_status="pending",
            )
            _notify_failure(cfg, selected, kind_label, conn)
            return _finish(conn, run_date, kind, force, outcome, owns_conn)

        intro_id = storage.save_introduction(
            conn, paper_id=selected.id, intro_date=run_date, kind=kind,
            body=body, backend=cfg.llm_backend, model=gen.model or cfg.llm_model,
            input_tokens=gen.input_tokens, output_tokens=gen.output_tokens,
            status="ok", slack_status="pending",
        )
        outcome.intro_id = intro_id

        # 5. Notify.
        payload = notify.build_blocks(selected, body)
        ok = notify.post(cfg.slack_webhook_url, payload)
        storage.update_intro_slack_status(conn, intro_id, "ok" if ok else "failed")
        outcome.note(f"slack post {'ok' if ok else 'failed'}")

    except Exception as exc:  # unexpected: record and continue to finish
        outcome.note(f"unexpected error: {exc}")
        outcome.result = "failed"

    return _finish(conn, run_date, kind, force, outcome, owns_conn)


def _notify_failure(cfg: Config, paper, reason_kind: str, conn) -> None:
    try:
        payload = notify.build_failure_blocks(paper, reason_kind)
        notify.post(cfg.slack_webhook_url, payload)
    except Exception as exc:  # never let a failed failure-notice crash the job
        log.warning("failed to post failure notice: %s", exc)


def _finish(conn, run_date, kind, force, outcome: JobOutcome, owns_conn: bool) -> JobOutcome:
    if kind == "daily" and not force:
        storage.finish_run(conn, run_date, outcome.result, "\n".join(outcome.messages))
    if owns_conn:
        conn.close()
    return outcome


def main() -> int:
    """Entry point for run_daily.bat / the `chem-daily` script."""
    cfg = load_config()
    from app.config import LOG_DIR

    setup_logging(LOG_DIR)
    outcome = run_once(cfg)
    log.info("daily job finished: result=%s", outcome.result)
    return 0 if outcome.result in ("ok", "skipped") else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
