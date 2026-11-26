"""Web app logic that does not need FastAPI: queries, view models, links,
upload validation, path safety, origin checks, and form actions (task 7)."""

from __future__ import annotations

import io
import json
import re
from datetime import date
from pathlib import Path

import pytest

from app import storage
from app.http_client import Response
from app.models import Paper
from app.web import actions, security, uploads, views

TEMPLATES = Path(__file__).resolve().parent.parent / "app" / "web" / "templates"


def _body(tag="x"):
    return {
        "background": f"bg {tag}", "what_they_did": "did", "key_results": "res",
        "why_it_matters": "matters", "summary_ja": ["一", "二", "三"],
        "vocabulary": [
            {"word": f"w{i}", "ja": "訳", "note": "", "example": "ex"} for i in range(5)
        ],
        "question": "Why?",
    }


def _paper(conn, doi="10.1021/abc", title="Ionophore sensor", **kw):
    p = Paper(doi=doi, title=title, authors=["A B", "C D"], journal=kw.get("journal", "ACS Sensors"),
              abstract=kw.get("abstract", "We built an ion-selective electrode."),
              source=kw.get("source", "crossref"), genre=kw.get("genre", "electrochem"),
              published_date="2026-05-01", oa_pdf_url=kw.get("oa"))
    pid = storage.upsert_paper(conn, p)
    p.id = pid
    return p


def _intro(conn, paper_id, d="2026-05-10", status="ok", kind="daily", body=None, tokens=(10, 20)):
    return storage.save_introduction(
        conn, paper_id=paper_id, intro_date=d, kind=kind,
        body=(body or _body()) if status == "ok" else None,
        backend="claude_code", model="m", input_tokens=tokens[0],
        output_tokens=tokens[1], status=status,
    )


# --- DOI / links -------------------------------------------------------------

@pytest.mark.parametrize("raw, expected", [
    ("10.1021/JACS.5b00001", "10.1021/jacs.5b00001"),
    ("  https://doi.org/10.1021/acs.analchem.1c0  ", "10.1021/acs.analchem.1c0"),
    ("doi:10.1002/anie.202400001", "10.1002/anie.202400001"),
    ("https://dx.doi.org/10.1039/D4SC00001A", "10.1039/d4sc00001a"),
    ("not a doi", None),
    ("", None),
    (None, None),
    ("10.12/x", None),  # registrant code too short
])
def test_normalize_doi(raw, expected):
    assert views.normalize_doi(raw) == expected


def test_fulltext_links_doi_proxy_oa():
    p = Paper(doi="10.1021/a b", title="t", oa_pdf_url="https://oa.example/x.pdf")
    links = views.fulltext_links(p, "https://proxy.example/login?url=https://doi.org/{doi}")
    kinds = [lk["kind"] for lk in links]
    assert kinds == ["doi", "proxy", "oa"]
    assert links[0]["url"] == "https://doi.org/10.1021/a%20b"
    assert links[1]["url"] == "https://proxy.example/login?url=https://doi.org/10.1021/a%20b"
    assert links[2]["url"] == "https://oa.example/x.pdf"


def test_fulltext_links_without_doi_or_proxy():
    p = Paper(doi=None, title="t", url="https://chemrxiv.org/x")
    assert views.fulltext_links(p, "") == [
        {"kind": "url", "label": "論文のページ", "url": "https://chemrxiv.org/x"}
    ]
    # A template without {doi} is ignored rather than producing a broken link.
    p2 = Paper(doi="10.1/x", title="t")
    assert [lk["kind"] for lk in views.fulltext_links(p2, "https://bad.example/")] == ["doi"]


def test_safe_next_url_and_with_message():
    assert views.safe_next_url("/papers/3#library") == "/papers/3#library"
    for bad in ("https://evil.example/", "//evil.example", "/\\evil", "", None, "javascript:x"):
        assert views.safe_next_url(bad, "/") == "/"
    assert views.with_message("/papers/3#library", "pdf_saved") == "/papers/3?msg=pdf_saved#library"
    assert views.with_message("/papers?q=a", "x") == "/papers?q=a&msg=x"
    assert views.with_message("/", None) == "/"


def test_flash_message_only_known_keys():
    assert views.flash_message("extra_ok")
    assert views.flash_message("<script>") is None
    assert views.flash_message(None) is None


# --- Pages -------------------------------------------------------------------

def test_today_page_shows_today_and_cap(cfg, conn):
    p = _paper(conn)
    _intro(conn, p.id, d="2026-05-10")
    page = views.today_page(conn, cfg, today="2026-05-10")
    assert len(page["items"]) == 1
    item = page["items"][0]
    assert item["intro"]["ok"]
    assert [s["heading"] for s in item["intro"]["sections"]] == [
        "Background", "What they did", "Key results", "Why it matters"
    ]
    assert len(item["intro"]["vocabulary"]) == 5
    assert item["paper"]["title"] == "Ionophore sensor"
    assert item["library"] is None
    assert page["fallback"] is None
    assert page["used_today"] == 1
    assert page["can_add_extra"] is True  # cap is 3


def test_today_page_falls_back_to_latest(cfg, conn):
    p = _paper(conn)
    _intro(conn, p.id, d="2026-05-01")
    page = views.today_page(conn, cfg, today="2026-05-10")
    assert page["items"] == []
    assert page["fallback"]["paper"]["id"] == p.id


def test_today_page_failed_intro_and_cap_reached(cfg, conn):
    p = _paper(conn)
    _intro(conn, p.id, d="2026-05-10", status="failed")
    page = views.today_page(conn, cfg, today="2026-05-10")
    assert page["items"][0]["intro"]["ok"] is False
    for i in range(3):
        q = _paper(conn, doi=f"10.1000/x{i}")
        _intro(conn, q.id, d="2026-05-10")
    page = views.today_page(conn, cfg, today="2026-05-10")
    assert page["can_add_extra"] is False


def test_papers_page_search_and_filters(conn):
    a = _paper(conn, doi="10.1000/a", title="Potentiometric pH sensor", journal="ACS Sensors")
    b = _paper(conn, doi="10.1000/b", title="Total synthesis of X", journal="JACS",
               genre="other", abstract="A synthesis.")
    c = _paper(conn, doi="10.1000/c", title="Never introduced")
    _intro(conn, a.id, d="2026-05-01")
    _intro(conn, b.id, d="2026-05-02", body=_body("organocatalysis"))
    _intro(conn, c.id, status="failed")

    all_ids = [i["id"] for i in views.papers_page(conn)["items"]]
    assert all_ids == [b.id, a.id]  # newest first, failed-only paper excluded

    assert [i["id"] for i in views.papers_page(conn, q="pH")["items"]] == [a.id]
    # Search reaches the introduction text too.
    assert [i["id"] for i in views.papers_page(conn, q="organocatalysis")["items"]] == [b.id]
    assert [i["id"] for i in views.papers_page(conn, genre="other")["items"]] == [b.id]
    assert [i["id"] for i in views.papers_page(conn, journal="ACS Sensors")["items"]] == [a.id]
    # LIKE wildcards in the query are matched literally.
    assert views.papers_page(conn, q="%")["items"] == []
    # Unknown genre values are ignored.
    assert len(views.papers_page(conn, genre="bogus")["items"]) == 2
    assert views.papers_page(conn)["journals"] == ["ACS Sensors", "JACS"]


def test_paper_detail_page(cfg, conn):
    p = _paper(conn, oa="https://oa.example/p.pdf", source="chemrxiv")
    _intro(conn, p.id)
    storage.set_feedback(conn, p.id, 1)
    storage.upsert_library(conn, p.id, status="reading", notes="memo", pdf_path="10.1021_abc.pdf")
    page = views.paper_detail_page(conn, cfg, p.id)
    assert page["intro"]["ok"]
    assert page["feedback"] == 1
    assert page["library"]["status_label"] == "読んでる"
    assert page["library"]["pdf_url"] == "/pdfs/10.1021_abc.pdf"
    assert page["paper"]["is_preprint"] is True
    assert {lk["kind"] for lk in page["links"]} == {"doi", "proxy", "oa"}
    assert views.paper_detail_page(conn, cfg, 9999) is None


def test_library_page_search(conn):
    a = _paper(conn, doi="10.1000/a", title="Alpha")
    b = _paper(conn, doi="10.1000/b", title="Beta")
    storage.upsert_library(conn, a.id, notes="calibration curve idea")
    storage.upsert_library(conn, b.id, status="done")
    assert {i["id"] for i in views.library_page(conn)["items"]} == {a.id, b.id}
    assert [i["id"] for i in views.library_page(conn, q="calibration")["items"]] == [a.id]
    assert [i["id"] for i in views.library_page(conn, q="Beta")["items"]] == [b.id]
    assert [i["id"] for i in views.library_page(conn, status="done")["items"]] == [b.id]
    assert len(views.library_page(conn, status="nonsense")["items"]) == 2


def test_settings_page_hides_secrets(cfg, conn):
    from dataclasses import replace

    secret_hook = "https://hooks.slack.com/services/T000/B000/SECRET"
    secret_key = "sk-ant-api03-SECRETKEY"
    cfg2 = replace(cfg, slack_webhook_url=secret_hook, anthropic_api_key=secret_key)
    p = _paper(conn)
    _intro(conn, p.id, tokens=(100, 200))
    storage.try_start_run(conn, "2026-05-10")
    storage.finish_run(conn, "2026-05-10", "ok", f"posted to {secret_hook}")

    month = date.today()
    page = views.settings_page(conn, cfg2, today=month)
    dumped = json.dumps(page, ensure_ascii=False, default=str)
    assert "SECRET" not in dumped
    assert dict(page["secrets"]) == {
        "Slack Webhook URL": "設定済み", "Anthropic API キー": "設定済み"
    }
    assert page["usage"] == {"input_tokens": 100, "output_tokens": 200}
    assert page["runs"][0]["run_date"] == "2026-05-10"
    assert "hooks.slack.com/***" in page["runs"][0]["log"]

    page_unset = views.settings_page(conn, cfg, today=month)
    assert dict(page_unset["secrets"])["Slack Webhook URL"] == "未設定"


# --- Uploads / path safety ----------------------------------------------------

def test_safe_pdf_filename():
    assert uploads.safe_pdf_filename("10.1021/jacs.5b00001", 1) == "10.1021_jacs.5b00001.pdf"
    assert uploads.safe_pdf_filename("10.1/../../etc/passwd", 2) == "10.1_.._.._etc_passwd.pdf"
    assert uploads.safe_pdf_filename(None, 7) == "paper-7.pdf"
    assert uploads.safe_pdf_filename("///", 8) == "paper-8.pdf"
    assert uploads.safe_pdf_filename("CON", 9) == "paper-9.pdf"
    name = uploads.safe_pdf_filename("10.1/" + "x" * 500, 3)
    assert len(name) <= 124 and name.endswith(".pdf")
    assert re.fullmatch(r"[A-Za-z0-9._-]+\.pdf", name)


def test_resolve_pdf_path_blocks_traversal(tmp_path):
    pdf_dir = tmp_path / "pdfs"
    pdf_dir.mkdir()
    assert uploads.resolve_pdf_path(pdf_dir, "a.pdf") == (pdf_dir / "a.pdf").resolve()
    for bad in ("../a.pdf", "..\\a.pdf", "sub/a.pdf", "C:a.pdf", "/etc/a.pdf",
                "..", "a.txt", "", None, "a.pdf\x00"):
        assert uploads.resolve_pdf_path(pdf_dir, bad) is None, bad


def test_check_pdf_filename():
    uploads.check_pdf_filename("Paper.PDF")
    for bad in ("paper.exe", "paper.pdf.exe", "", None):
        with pytest.raises(uploads.UploadError):
            uploads.check_pdf_filename(bad)


def test_save_pdf_validates_header_and_size(tmp_path):
    pdf_dir = tmp_path / "pdfs"
    path = uploads.save_pdf(pdf_dir, "ok.pdf", io.BytesIO(b"%PDF-1.7\nbody"))
    assert path.read_bytes() == b"%PDF-1.7\nbody"

    with pytest.raises(uploads.UploadError):
        uploads.save_pdf(pdf_dir, "fake.pdf", io.BytesIO(b"MZ\x90\x00not a pdf"))
    with pytest.raises(uploads.UploadError):
        uploads.save_pdf(pdf_dir, "big.pdf", io.BytesIO(b"%PDF-" + b"x" * 2000), max_bytes=1000)
    with pytest.raises(uploads.UploadError):
        uploads.save_pdf(pdf_dir, "../escape.pdf", io.BytesIO(b"%PDF-1.7"))
    # Rejected uploads leave nothing behind (no partial or temp files).
    assert sorted(p.name for p in pdf_dir.iterdir()) == ["ok.pdf"]
    assert not (tmp_path / "escape.pdf").exists()


# --- Security checks -----------------------------------------------------------

def test_host_and_origin_checks():
    assert security.host_allowed("127.0.0.1:8765")
    assert security.host_allowed("localhost:8765")
    assert not security.host_allowed("evil.example:8765")
    assert not security.host_allowed(None)
    assert not security.host_allowed("testserver")
    assert security.host_allowed("testserver", allow_test=True)

    assert security.origin_allowed("http://127.0.0.1:8765", None)
    assert security.origin_allowed(None, "http://127.0.0.1:8765/papers/1")
    assert security.origin_allowed(None, None)
    assert not security.origin_allowed("https://evil.example", None)
    assert not security.origin_allowed("null", None)
    assert not security.origin_allowed(None, "http://evil.example/x")


# --- Actions --------------------------------------------------------------------

def test_feedback_and_library_actions(conn):
    p = _paper(conn)
    actions.set_feedback(conn, p.id, -1)
    assert storage.get_feedback(conn, p.id) == -1
    with pytest.raises(actions.ActionError):
        actions.set_feedback(conn, p.id, 5)
    with pytest.raises(actions.ActionError):
        actions.set_feedback(conn, 999, 1)

    actions.add_to_library(conn, p.id)
    actions.add_to_library(conn, p.id)  # idempotent
    entry = storage.get_library_entry(conn, p.id)
    assert entry["status"] == "want"

    actions.update_library(conn, p.id, status="done", notes="良かった")
    entry = storage.get_library_entry(conn, p.id)
    assert (entry["status"], entry["notes"]) == ("done", "良かった")
    with pytest.raises(actions.ActionError):
        actions.update_library(conn, p.id, status="lost", notes="")


def test_attach_pdf(conn, tmp_path):
    p = _paper(conn, doi="10.1021/jacs.5b00001")
    name = actions.attach_pdf(conn, p.id, filename="my paper.pdf",
                              stream=io.BytesIO(b"%PDF-1.4 x"), pdf_dir=tmp_path)
    assert name == "10.1021_jacs.5b00001.pdf"
    assert (tmp_path / name).exists()
    assert storage.get_library_entry(conn, p.id)["pdf_path"] == name

    with pytest.raises(uploads.UploadError):
        actions.attach_pdf(conn, p.id, filename="evil.html",
                           stream=io.BytesIO(b"%PDF-1.4"), pdf_dir=tmp_path)
    with pytest.raises(uploads.UploadError):
        actions.attach_pdf(conn, p.id, filename="evil.pdf",
                           stream=io.BytesIO(b"<html>"), pdf_dir=tmp_path)


class RouteFetcher:
    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def __call__(self, url, *, params=None, headers=None):
        self.calls.append(url)
        for needle, resp in self.routes.items():
            if needle in url:
                if isinstance(resp, Exception):
                    raise resp
                return resp
        return Response(404, "{}")


def test_add_by_doi_with_crossref_metadata(cfg, conn):
    work = {"message": {
        "DOI": "10.1021/acssensors.6b00001",
        "title": ["A potentiometric ionophore study"],
        "author": [{"given": "Taro", "family": "Yamada"}],
        "container-title": ["ACS Sensors"],
        "ISSN": ["2379-3694"],
        "published": {"date-parts": [[2026, 4, 2]]},
        "abstract": "<jats:p>Potentiometric sensing.</jats:p>",
    }}
    fetch = RouteFetcher({
        "api.crossref.org/works/": Response(200, json.dumps(work)),
        "unpaywall.org": Response(200, json.dumps({"best_oa_location": {"url_for_pdf": "https://oa/x.pdf"}})),
    })
    res = actions.add_by_doi(conn, cfg, fetch, "https://doi.org/10.1021/ACSSENSORS.6b00001")
    assert res.created and res.metadata_found
    p = storage.get_paper(conn, res.paper_id)
    assert p.title == "A potentiometric ionophore study"
    assert p.source == "manual"
    assert p.issn == "2379-3694"
    assert p.oa_pdf_url == "https://oa/x.pdf"
    assert p.genre == "electrochem"
    assert storage.get_library_entry(conn, p.id)["status"] == "want"

    # Adding the same DOI again reuses the existing paper.
    again = actions.add_by_doi(conn, cfg, fetch, "10.1021/acssensors.6b00001")
    assert again.paper_id == res.paper_id and not again.created


def test_add_by_doi_offline_fallback_and_invalid(cfg, conn):
    fetch = RouteFetcher({"crossref.org": OSError("offline"), "unpaywall.org": OSError("offline")})
    res = actions.add_by_doi(conn, cfg, fetch, "10.1039/d4sc00001a", title="My title")
    assert res.created and not res.metadata_found
    assert storage.get_paper(conn, res.paper_id).title == "My title"

    res2 = actions.add_by_doi(conn, cfg, None, "10.1039/d4sc00002a")
    assert storage.get_paper(conn, res2.paper_id).title == "10.1039/d4sc00002a"

    with pytest.raises(actions.ActionError):
        actions.add_by_doi(conn, cfg, None, "hello")


class _Outcome:
    def __init__(self, result):
        self.result = result


def test_request_extra_respects_cap_and_maps_results(cfg, conn):
    calls = []

    def job(c, **kw):
        calls.append(kw)
        return _Outcome("ok")

    assert actions.request_extra(conn, cfg, run_job=job, today="2026-05-10") == "extra_ok"
    assert calls == [{"kind": "extra", "run_date": "2026-05-10"}]

    for result, key in (("skipped", "extra_limit"), ("partial", "extra_none"), ("failed", "extra_failed")):
        assert actions.request_extra(
            conn, cfg, run_job=lambda c, **kw: _Outcome(result), today="2026-05-10"
        ) == key

    # At the cap the job is not started at all.
    for i in range(cfg.max_intros_per_day):
        q = _paper(conn, doi=f"10.1000/cap{i}")
        _intro(conn, q.id, d="2026-05-10")
    calls.clear()
    assert actions.request_extra(conn, cfg, run_job=job, today="2026-05-10") == "extra_limit"
    assert calls == []


def test_request_extra_uses_daily_job_extra_path(cfg, conn, monkeypatch):
    """End-to-end through daily_job.run_once(kind='extra') with mocks."""
    from app import daily_job, notify
    from app.llm.base import GenerationResult

    monkeypatch.setattr(notify, "post", lambda *a, **k: True)
    monkeypatch.setattr(
        daily_job.introduce, "generate_introduction",
        lambda paper, c, **kw: (_body(), GenerationResult("x", 1, 1, "m")),
    )
    _paper(conn)  # an un-introduced candidate with an abstract
    empty = RouteFetcher({
        "/journals/": Response(200, '{"message": {"items": []}}'),
        "chemrxiv.org": Response(200, '{"itemHits": []}'),
        "esearch": Response(200, '{"esearchresult": {"idlist": []}}'),
    })

    def job(c, **kw):
        return daily_job.run_once(c, conn=conn, fetch=empty, **kw)

    assert actions.request_extra(conn, cfg, run_job=job, today="2026-05-10") == "extra_ok"
    rows = storage.intros_on(conn, "2026-05-10")
    assert [r["kind"] for r in rows] == ["extra"]
    # The extra path does not claim the day's job_runs slot.
    assert storage.get_run(conn, "2026-05-10") is None


# --- Templates ------------------------------------------------------------------

_TAG_RE = re.compile(r"{%-?\s*(\w+)")
_PAIRS = {"if": "endif", "for": "endfor", "block": "endblock", "macro": "endmacro"}


@pytest.mark.parametrize("path", sorted(TEMPLATES.glob("*.html")), ids=lambda p: p.name)
def test_template_blocks_balanced(path):
    """Cheap structural check, since Jinja2 may not be installed here."""
    stack = []
    for tag in _TAG_RE.findall(path.read_text(encoding="utf-8")):
        if tag in _PAIRS:
            stack.append(tag)
        elif tag in _PAIRS.values():
            assert stack and _PAIRS[stack.pop()] == tag, f"unbalanced {tag} in {path.name}"
    assert not stack, f"unclosed {stack} in {path.name}"


def test_templates_render_with_jinja_if_available(cfg, conn):
    jinja2 = pytest.importorskip("jinja2")
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(TEMPLATES), autoescape=True)
    env.globals["status_labels"] = views.STATUS_LABELS
    p = _paper(conn, title="<b>T</b>")
    _intro(conn, p.id, d=date.today().isoformat())
    storage.upsert_library(conn, p.id, pdf_path="x.pdf")
    common = {"flash": None, "nav": "/"}
    pages = {
        "index.html": views.today_page(conn, cfg),
        "papers.html": views.papers_page(conn),
        "paper_detail.html": views.paper_detail_page(conn, cfg, p.id),
        "library.html": views.library_page(conn),
        "library_add.html": {"doi": "", "title": ""},
        "settings.html": views.settings_page(conn, cfg),
        "message.html": {"message": "m", "back": "/"},
    }
    for name, ctx in pages.items():
        html = env.get_template(name).render(**common, **ctx)
        assert "<b>T</b>" not in html  # autoescaped
    assert "要旨ベースの紹介" in env.get_template("index.html").render(**common, **pages["index.html"])
