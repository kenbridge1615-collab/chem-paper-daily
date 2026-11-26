"""Mocked integration test of the daily job and same-day guard (task 6.1/6.3)."""

from __future__ import annotations

import json

from app import daily_job, notify, storage
from app.http_client import Response
from app.llm.base import GenerationResult, NotLoggedInError


def _valid_body():
    return {
        "background": "bg", "what_they_did": "did", "key_results": "res",
        "why_it_matters": "matters", "summary_ja": ["a", "b", "c"],
        "vocabulary": [
            {"word": f"w{i}", "ja": "訳", "note": "", "example": "ex"} for i in range(5)
        ],
        "question": "q?",
    }


class RouteFetcher:
    def __init__(self, routes):
        self.routes = routes

    def __call__(self, url, *, params=None, headers=None):
        for needle, resp in self.routes.items():
            if needle in url:
                return resp
        return Response(404, "{}")


def _crossref_payload():
    item = {
        "DOI": "10.1021/elec1",
        "title": ["An ion-selective electrode for pH sensing"],
        "author": [{"given": "A", "family": "B"}],
        "container-title": ["ACS Sensors"],
        "published": {"date-parts": [[2026, 5, 1]]},
        "abstract": "<jats:p>We built a potentiometric sensor.</jats:p>",
        "URL": "https://doi.org/10.1021/elec1",
    }
    return Response(200, json.dumps({"message": {"items": [item]}}))


def _empty_routes():
    return {
        "/journals/": _crossref_payload(),
        "chemrxiv.org": Response(200, '{"itemHits": []}'),
        "esearch": Response(200, '{"esearchresult": {"idlist": []}}'),
        "semanticscholar.org": Response(200, '{"abstract": null}'),
        "unpaywall.org": Response(200, '{"best_oa_location": null}'),
    }


def _patch_slack_ok(monkeypatch):
    posted = []
    monkeypatch.setattr(notify, "post", lambda url, payload, **kw: posted.append(payload) or True)
    return posted


def test_full_run_success(cfg, conn, monkeypatch):
    posted = _patch_slack_ok(monkeypatch)
    monkeypatch.setattr(
        daily_job.introduce, "generate_introduction",
        lambda paper, c, **kw: (_valid_body(), GenerationResult("x", 10, 20, "m")),
    )
    fetch = RouteFetcher(_empty_routes())
    outcome = daily_job.run_once(cfg, conn=conn, fetch=fetch, run_date="2026-05-10")

    assert outcome.ran
    assert outcome.result == "ok"
    assert outcome.intro_id is not None
    assert len(posted) == 1
    # Introduction stored with token usage and ok status.
    row = conn.execute("SELECT * FROM introductions WHERE id=?", (outcome.intro_id,)).fetchone()
    assert row["status"] == "ok"
    assert row["input_tokens"] == 10
    assert row["slack_status"] == "ok"
    # job_runs recorded for the date.
    run = storage.get_run(conn, "2026-05-10")
    assert run["result"] == "ok"


def test_same_day_double_run_prevented(cfg, conn, monkeypatch):
    _patch_slack_ok(monkeypatch)
    monkeypatch.setattr(
        daily_job.introduce, "generate_introduction",
        lambda paper, c, **kw: (_valid_body(), GenerationResult("x", 1, 1, "m")),
    )
    fetch = RouteFetcher(_empty_routes())
    first = daily_job.run_once(cfg, conn=conn, fetch=fetch, run_date="2026-05-11")
    second = daily_job.run_once(cfg, conn=conn, fetch=fetch, run_date="2026-05-11")

    assert first.result == "ok"
    assert second.result == "skipped"
    assert second.ran is False
    # Exactly one introduction for that date.
    assert storage.count_intros_on(conn, "2026-05-11") == 1


def test_generation_failure_records_and_notifies(cfg, conn, monkeypatch):
    posted = []
    monkeypatch.setattr(notify, "post", lambda url, payload, **kw: posted.append(payload) or True)

    def fail(paper, c, **kw):
        raise NotLoggedInError("log in")

    monkeypatch.setattr(daily_job.introduce, "generate_introduction", fail)
    fetch = RouteFetcher(_empty_routes())
    outcome = daily_job.run_once(cfg, conn=conn, fetch=fetch, run_date="2026-05-12")

    assert outcome.result == "failed"
    # A failed introduction row is recorded.
    row = conn.execute(
        "SELECT * FROM introductions WHERE intro_date='2026-05-12'"
    ).fetchone()
    assert row["status"] == "failed"
    # A failure notice was posted to Slack.
    assert any("failed" in str(p).lower() for p in posted)


def test_no_candidate_is_partial(cfg, conn, monkeypatch):
    _patch_slack_ok(monkeypatch)
    # All sources empty -> no candidate.
    routes = {
        "/journals/": Response(200, '{"message": {"items": []}}'),
        "chemrxiv.org": Response(200, '{"itemHits": []}'),
        "esearch": Response(200, '{"esearchresult": {"idlist": []}}'),
    }
    fetch = RouteFetcher(routes)
    outcome = daily_job.run_once(cfg, conn=conn, fetch=fetch, run_date="2026-05-13")
    assert outcome.result == "partial"
