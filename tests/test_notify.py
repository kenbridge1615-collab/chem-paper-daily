"""Slack Block Kit assembly and webhook posting with retry (task 5)."""

from __future__ import annotations

from app import notify
from app.models import Paper


def _body():
    return {
        "background": "bg",
        "what_they_did": "did",
        "key_results": "res",
        "why_it_matters": "matters",
        "summary_ja": ["a", "b", "c"],
        "vocabulary": [
            {"word": "electrode", "ja": "電極", "note": "", "example": "ex"},
            {"word": "ionophore", "ja": "イオノフォア", "note": "", "example": "ex"},
            {"word": "sensor", "ja": "センサ", "note": "", "example": "ex"},
            {"word": "assay", "ja": "分析", "note": "", "example": "ex"},
            {"word": "probe", "ja": "プローブ", "note": "", "example": "ex"},
        ],
        "question": "q?",
    }


def test_build_blocks_includes_links_and_vocab():
    p = Paper(doi="10.1/x", title="A Title", journal="ACS Sensors",
              published_date="2026-05-01", id=42)
    payload = notify.build_blocks(p, _body())
    text = str(payload)
    assert "A Title" in text
    assert "127.0.0.1:8765/papers/42" in text
    assert "https://doi.org/10.1/x" in text
    # Three vocab words surfaced.
    assert "electrode" in text and "ionophore" in text and "sensor" in text


def test_post_succeeds_first_try():
    calls = {"n": 0}

    class FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def getcode(self):
            return 200

    def opener(req, timeout=None):
        calls["n"] += 1
        return FakeResp()

    assert notify.post("https://hooks.slack.com/services/x", {"a": 1}, opener=opener)
    assert calls["n"] == 1


def test_post_retries_then_fails(monkeypatch):
    import urllib.error

    monkeypatch.setattr(notify.time, "sleep", lambda *_: None)
    calls = {"n": 0}

    def opener(req, timeout=None):
        calls["n"] += 1
        raise urllib.error.URLError("boom")

    ok = notify.post("https://hooks.slack.com/services/x", {"a": 1}, opener=opener)
    assert ok is False
    assert calls["n"] == notify.MAX_RETRIES


def test_post_no_url_returns_false():
    assert notify.post("", {"a": 1}) is False


def test_failure_blocks_reason():
    payload = notify.build_failure_blocks(None, "not_logged_in")
    assert "not logged in" in str(payload).lower()
