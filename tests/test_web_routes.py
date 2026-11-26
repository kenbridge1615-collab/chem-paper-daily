"""Route-level tests through FastAPI's TestClient.

Skipped automatically when fastapi / jinja2 / httpx / python-multipart are not
installed (e.g. an offline sandbox). After `uv sync --extra dev` on a real
machine these run with the rest of the suite.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("jinja2")
pytest.importorskip("httpx")
pytest.importorskip("multipart")

from fastapi.testclient import TestClient

from app import storage
from app.models import Paper
from app.web.main import create_app

ORIGIN = {"origin": "http://127.0.0.1:8765"}


class _Outcome:
    result = "ok"


@pytest.fixture
def client(cfg, conn, tmp_path):
    p = Paper(doi="10.1021/abc", title="Ionophore sensor", abstract="An electrode.",
              journal="ACS Sensors", source="crossref", genre="electrochem")
    pid = storage.upsert_paper(conn, p)
    storage.save_introduction(
        conn, paper_id=pid, intro_date="2026-05-10", kind="daily",
        body={"background": "B", "what_they_did": "W", "key_results": "K",
              "why_it_matters": "M", "summary_ja": ["一", "二", "三"],
              "vocabulary": [{"word": "w", "ja": "j", "note": "", "example": "e"}] * 5,
              "question": "Q?"},
        backend="claude_code", model="m", input_tokens=1, output_tokens=2, status="ok",
    )
    app = create_app(cfg, pdf_dir=tmp_path / "pdfs", run_job=lambda c, **kw: _Outcome(),
                     fetch_factory=lambda: None, allow_test_host=True)
    c = TestClient(app)
    c.paper_id = pid
    return c


def test_get_pages(client):
    pid = client.paper_id
    for url in ("/", "/papers", "/papers?q=Ionophore&genre=electrochem", f"/papers/{pid}",
                "/library", "/library/add", "/settings", "/static/style.css"):
        r = client.get(url)
        assert r.status_code == 200, url
    assert "要旨ベースの紹介" in client.get(f"/papers/{pid}").text
    assert client.get("/papers/9999").status_code == 404


def test_feedback_library_and_pdf_flow(client):
    pid = client.paper_id
    r = client.post(f"/papers/{pid}/feedback", data={"rating": "1", "next": "/"},
                    headers=ORIGIN, follow_redirects=False)
    assert r.status_code == 303
    client.post(f"/papers/{pid}/library", data={}, headers=ORIGIN)
    r = client.post(f"/library/{pid}", data={"status": "reading", "notes": "memo"}, headers=ORIGIN)
    assert r.status_code == 200
    r = client.post(f"/library/{pid}/pdf", files={"pdf": ("a.pdf", b"%PDF-1.4 x", "application/pdf")},
                    headers=ORIGIN, follow_redirects=False)
    assert r.status_code == 303
    r = client.get("/pdfs/10.1021_abc.pdf")
    assert r.status_code == 200 and r.content.startswith(b"%PDF")
    bad = client.post(f"/library/{pid}/pdf", files={"pdf": ("a.pdf", b"<html>", "text/html")},
                      headers=ORIGIN)
    assert bad.status_code == 400
    assert client.get("/pdfs/..%2F..%2Fconfig.toml").status_code == 404


def test_library_add_and_extra(client):
    r = client.post("/library/add", data={"doi": "10.1039/d4sc00001a", "title": "T"},
                    headers=ORIGIN, follow_redirects=False)
    assert r.status_code == 303
    assert client.post("/library/add", data={"doi": "nope"}, headers=ORIGIN).status_code == 400
    r = client.post("/extra", headers=ORIGIN, follow_redirects=False)
    assert r.status_code == 303 and "msg=extra_ok" in r.headers["location"]


def test_cross_site_post_and_foreign_host_rejected(client):
    r = client.post("/extra", headers={"origin": "https://evil.example"})
    assert r.status_code == 403
    r = client.get("/", headers={"host": "evil.example"})
    assert r.status_code == 403
