"""View-model assembly for the web pages. Pure functions, no FastAPI.

Each `*_page` function takes a DB connection (and config where needed) and
returns a plain dict that the Jinja2 templates render.
"""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import date
from urllib.parse import quote

from app import storage
from app.config import Config
from app.logging_setup import mask_secrets
from app.models import Paper

GENRE_LABELS = {"electrochem": "電気化学", "other": "化学（その他）"}
STATUS_LABELS = {"want": "読みたい", "reading": "読んでる", "done": "読んだ"}
SOURCE_LABELS = {
    "crossref": "Crossref",
    "chemrxiv": "ChemRxiv（プレプリント）",
    "pubmed": "PubMed",
    "manual": "手動追加",
}
BACKEND_LABELS = {"claude_code": "Claude Code（契約プラン）", "api": "Claude API（従量課金）"}
FAILURE_LABELS = {
    "not_installed": "Claude Code が見つかりません",
    "not_logged_in": "Claude Code にログインしていません",
    "usage_limit": "利用枠の上限に達しました",
    "timeout": "時間切れ",
    "no_candidate": "紹介できる論文がありません",
    "other": "その他のエラー",
}

# Short messages shown after a redirect (?msg=...). Only known keys are shown,
# so arbitrary text from the URL is never echoed onto the page.
FLASH_MESSAGES = {
    "extra_ok": "もう1本、紹介を追加しました。",
    "extra_limit": "今日の上限に達したので、これ以上は追加できません。",
    "extra_failed": "もう1本の追加に失敗しました。設定画面の「直近のジョブ結果」を確認してください。",
    "extra_none": "紹介できる論文が見つかりませんでした。",
    "feedback_saved": "評価を記録しました。",
    "library_added": "本棚に追加しました。",
    "library_saved": "本棚の内容を保存しました。",
    "pdf_saved": "PDF を登録しました。",
}

_DOI_RE = re.compile(r"^10\.\d{4,9}/\S+$")
_DOI_PREFIXES = (
    "https://doi.org/",
    "http://doi.org/",
    "https://dx.doi.org/",
    "http://dx.doi.org/",
    "doi.org/",
    "doi:",
)


def normalize_doi(text: str | None) -> str | None:
    """Turn user input like 'https://doi.org/10.1021/X' into '10.1021/x'.

    Returns None if the result does not look like a DOI. DOIs are stored in
    lower case (the collectors do the same) so lookups match.
    """
    if not text:
        return None
    s = text.strip()
    lowered = s.lower()
    for prefix in _DOI_PREFIXES:
        if lowered.startswith(prefix):
            s = s[len(prefix):]
            break
    s = s.strip().lower()
    return s if _DOI_RE.match(s) else None


def flash_message(key: str | None) -> str | None:
    return FLASH_MESSAGES.get(key or "")


def with_message(url: str, msg: str | None) -> str:
    """Add ?msg=<key> to a relative URL, keeping any #fragment at the end."""
    if not msg:
        return url
    path, sep, fragment = url.partition("#")
    path += ("&" if "?" in path else "?") + f"msg={quote(msg, safe='')}"
    return path + (sep + fragment if sep else "")


def safe_next_url(value: str | None, default: str = "/") -> str:
    """Accept only same-site relative paths for post-redirect targets."""
    if not isinstance(value, str) or not value.startswith("/") or value.startswith("//") or "\\" in value:
        return default
    return value


# --- Links ------------------------------------------------------------------

def _quote_doi(doi: str) -> str:
    return quote(doi, safe="/")


def fulltext_links(paper: Paper, proxy_url_template: str) -> list[dict]:
    """'全文を読む' links: DOI, Tohoku proxy, and OA PDF (requirements R5-2)."""
    links: list[dict] = []
    if paper.doi:
        q = _quote_doi(paper.doi)
        links.append({"kind": "doi", "label": "DOI（出版社のページ）", "url": f"https://doi.org/{q}"})
        if proxy_url_template and "{doi}" in proxy_url_template:
            links.append({
                "kind": "proxy",
                "label": "東北大の学外アクセス経由",
                "url": proxy_url_template.replace("{doi}", q),
            })
    elif paper.url:
        links.append({"kind": "url", "label": "論文のページ", "url": paper.url})
    if paper.oa_pdf_url:
        links.append({"kind": "oa", "label": "オープンアクセス版 PDF", "url": paper.oa_pdf_url})
    return links


def pdf_url(pdf_path: str | None) -> str | None:
    """URL for a stored PDF. pdf_path is the bare file name under data/pdfs/."""
    if not pdf_path:
        return None
    return "/pdfs/" + quote(pdf_path, safe="")


# --- View models ------------------------------------------------------------

def paper_view(paper: Paper) -> dict:
    return {
        "id": paper.id,
        "doi": paper.doi,
        "title": paper.title,
        "authors": paper.authors,
        "authors_text": _authors_text(paper.authors),
        "journal": paper.journal or "",
        "published_date": paper.published_date or "",
        "abstract": paper.abstract or "",
        "source": paper.source,
        "source_label": SOURCE_LABELS.get(paper.source or "", paper.source or ""),
        "is_preprint": paper.source == "chemrxiv",
        "genre": paper.genre,
        "genre_label": GENRE_LABELS.get(paper.genre or "", ""),
    }


def _authors_text(authors: list[str], limit: int = 6) -> str:
    if not authors:
        return ""
    if len(authors) <= limit:
        return ", ".join(authors)
    return ", ".join(authors[:limit]) + f" ほか {len(authors) - limit} 名"


def intro_view(row: sqlite3.Row | dict) -> dict:
    """Unpack an introductions row into what the template needs."""
    body = json.loads(row["body_json"]) if row["body_json"] else None
    sections = []
    if body:
        for key, heading in (
            ("background", "Background"),
            ("what_they_did", "What they did"),
            ("key_results", "Key results"),
            ("why_it_matters", "Why it matters"),
        ):
            sections.append({"heading": heading, "text": body.get(key, "")})
    return {
        "id": row["id"],
        "paper_id": row["paper_id"],
        "intro_date": row["intro_date"],
        "kind": row["kind"],
        "kind_label": "毎朝の1本" if row["kind"] == "daily" else "もう1本",
        "ok": row["status"] == "ok" and body is not None,
        "sections": sections,
        "summary_ja": (body or {}).get("summary_ja", []),
        "vocabulary": (body or {}).get("vocabulary", []),
        "question": (body or {}).get("question", ""),
        "backend_label": BACKEND_LABELS.get(row["backend"] or "", row["backend"] or ""),
        "model": row["model"] or "",
        "input_tokens": row["input_tokens"],
        "output_tokens": row["output_tokens"],
    }


def library_view(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    return {
        "status": row["status"] or "want",
        "status_label": STATUS_LABELS.get(row["status"] or "want", ""),
        "notes": row["notes"] or "",
        "pdf_path": row["pdf_path"],
        "pdf_url": pdf_url(row["pdf_path"]),
        "updated_at": (row["updated_at"] or "")[:16].replace("T", " "),
    }


def paper_block(conn: sqlite3.Connection, cfg: Config, paper: Paper) -> dict:
    """Everything shown around a paper: metadata, links, feedback, library."""
    return {
        "paper": paper_view(paper),
        "links": fulltext_links(paper, cfg.proxy_url_template),
        "feedback": storage.get_feedback(conn, paper.id),
        "library": library_view(storage.get_library_entry(conn, paper.id)),
    }


def _intro_with_paper(conn, cfg, row) -> dict | None:
    paper = storage.get_paper(conn, row["paper_id"])
    if paper is None:
        return None
    return {"intro": intro_view(row), **paper_block(conn, cfg, paper)}


def today_page(conn: sqlite3.Connection, cfg: Config, *, today: str | None = None) -> dict:
    """'/' — today's introductions, or the most recent one as a fallback."""
    today = today or date.today().isoformat()
    rows = storage.intros_on(conn, today)
    items = [x for x in (_intro_with_paper(conn, cfg, r) for r in rows) if x]
    fallback = None
    if not any(x["intro"]["ok"] for x in items):
        latest = storage.latest_ok_intro(conn)
        if latest is not None:
            fallback = _intro_with_paper(conn, cfg, latest)
    used = storage.count_intros_on(conn, today)
    return {
        "today": today,
        "items": items,
        "fallback": fallback,
        "used_today": used,
        "max_per_day": cfg.max_intros_per_day,
        "can_add_extra": used < cfg.max_intros_per_day,
        "notify_time": cfg.notify_time,
    }


def papers_page(
    conn: sqlite3.Connection, *, q: str = "", genre: str = "", journal: str = ""
) -> dict:
    genre = genre if genre in GENRE_LABELS else ""
    rows = storage.search_introduced_papers(conn, q=q, genre=genre, journal=journal)
    items = []
    for r in rows:
        authors = json.loads(r["authors_json"]) if r["authors_json"] else []
        items.append({
            "id": r["id"],
            "title": r["title"],
            "journal": r["journal"] or "",
            "published_date": r["published_date"] or "",
            "intro_date": r["intro_date"],
            "authors_text": _authors_text(authors, limit=3),
            "genre_label": GENRE_LABELS.get(r["genre"] or "", ""),
            "is_preprint": r["source"] == "chemrxiv",
        })
    return {
        "items": items,
        "q": q,
        "genre": genre,
        "journal": journal,
        "genres": GENRE_LABELS,
        "journals": storage.introduced_journals(conn),
    }


def paper_detail_page(conn: sqlite3.Connection, cfg: Config, paper_id: int) -> dict | None:
    paper = storage.get_paper(conn, paper_id)
    if paper is None:
        return None
    intros = [intro_view(r) for r in storage.intros_for_paper(conn, paper_id)]
    ok_intros = [i for i in intros if i["ok"]]
    return {
        **paper_block(conn, cfg, paper),
        "intro": ok_intros[0] if ok_intros else None,
        "statuses": STATUS_LABELS,
    }


def library_page(conn: sqlite3.Connection, *, q: str = "", status: str = "") -> dict:
    status = status if status in STATUS_LABELS else ""
    rows = storage.search_library(conn, q=q, status=status)
    items = []
    for r in rows:
        items.append({
            "id": r["id"],
            "title": r["title"],
            "journal": r["journal"] or "",
            "doi": r["doi"],
            "published_date": r["published_date"] or "",
            "status": r["lib_status"] or "want",
            "status_label": STATUS_LABELS.get(r["lib_status"] or "want", ""),
            "notes": r["notes"] or "",
            "pdf_url": pdf_url(r["pdf_path"]),
            "updated_at": (r["lib_updated_at"] or "")[:10],
        })
    return {"items": items, "q": q, "status": status, "statuses": STATUS_LABELS}


def _secret_state(value: str) -> str:
    return "設定済み" if value else "未設定"


def settings_page(conn: sqlite3.Connection, cfg: Config, *, today: date | None = None) -> dict:
    """'/settings' — config overview, token totals, recent job runs.

    Secrets are reduced to 設定済み / 未設定; their values never reach the
    template. Job logs are passed through mask_secrets as a second guard.
    """
    today = today or date.today()
    month = today.strftime("%Y-%m")
    usage = storage.monthly_token_usage(conn, month)
    runs = []
    for r in storage.recent_job_runs(conn, limit=10):
        runs.append({
            "run_date": r["run_date"],
            "result": r["result"] or "実行中",
            "started_at": (r["started_at"] or "")[:19].replace("T", " "),
            "finished_at": (r["finished_at"] or "")[:19].replace("T", " "),
            "log": mask_secrets(r["log"] or ""),
        })
    return {
        "general": [
            ("連絡用メール（contact_email）", cfg.contact_email),
            ("通知時刻", cfg.notify_time),
            ("新着を探す日数", f"{cfg.lookback_days} 日"),
            ("1日の紹介の上限", f"{cfg.max_intros_per_day} 本"),
            ("ジャンル配分", f"電気化学 {cfg.genre_electrochem:.0%} / その他 {cfg.genre_other:.0%}"),
            ("生成方式", BACKEND_LABELS.get(cfg.llm_backend, cfg.llm_backend)),
            ("モデル", cfg.llm_model),
            ("生成のタイムアウト", f"{cfg.llm_timeout_seconds} 秒"),
            ("学外アクセス URL の形式", cfg.proxy_url_template or "（未設定）"),
        ],
        "secrets": [
            ("Slack Webhook URL", _secret_state(cfg.slack_webhook_url)),
            ("Anthropic API キー", _secret_state(cfg.anthropic_api_key)),
        ],
        "journals": [
            {"name": j.name, "issn": j.issn, "tier": j.tier} for j in cfg.journals
        ],
        "keywords": sorted(cfg.keyword_weights.items(), key=lambda kv: (-kv[1], kv[0])),
        "month": month,
        "usage": usage,
        "runs": runs,
    }
