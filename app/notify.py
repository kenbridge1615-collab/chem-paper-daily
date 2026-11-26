"""Slack notification via Incoming Webhook (design section 8, requirements R4).

Posts a Block Kit message with the title, journal/date, a short English
summary, three vocabulary words, and buttons linking to the local web page and
the DOI. Posting retries with exponential backoff up to 3 times. A separate
helper posts a generation-failure notice.

The webhook URL is a secret: it is never logged (see app.logging_setup masking).
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request

from app.models import Paper

log = logging.getLogger("chem_paper_daily.notify")

WEB_BASE = "http://127.0.0.1:8765"
MAX_RETRIES = 3


def _short(text: str, limit: int = 300) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def build_blocks(paper: Paper, body: dict, *, web_base: str = WEB_BASE) -> dict:
    """Build the Slack Block Kit payload for a successful introduction."""
    summary = " ".join(
        body.get(k, "") for k in ("background", "what_they_did", "why_it_matters")
    )
    vocab = body.get("vocabulary", [])[:3]
    vocab_line = ", ".join(
        f"*{v.get('word', '')}* ({v.get('ja', '')})" for v in vocab
    )
    date_journal = " · ".join(
        p for p in (paper.journal, paper.published_date) if p
    )
    web_url = f"{web_base}/papers/{paper.id}"
    doi_url = f"https://doi.org/{paper.doi}" if paper.doi else None

    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": _short(paper.title, 150)}},
        {"type": "context", "elements": [{"type": "mrkdwn", "text": date_journal or " "}]},
        {"type": "section", "text": {"type": "mrkdwn", "text": _short(summary, 600)}},
    ]
    if vocab_line:
        blocks.append(
            {"type": "section", "text": {"type": "mrkdwn", "text": f"🔑 {vocab_line}"}}
        )

    actions = [
        {
            "type": "button",
            "text": {"type": "plain_text", "text": "Read on web"},
            "url": web_url,
        }
    ]
    if doi_url:
        actions.append(
            {
                "type": "button",
                "text": {"type": "plain_text", "text": "DOI"},
                "url": doi_url,
            }
        )
    blocks.append({"type": "actions", "elements": actions})

    return {"text": f"Today's paper: {paper.title}", "blocks": blocks}


def build_failure_blocks(paper: Paper | None, reason_kind: str) -> dict:
    """Message for when generation failed (requirements R3-5)."""
    reason_text = {
        "not_logged_in": "Claude Code is not logged in.",
        "usage_limit": "The Claude usage limit was reached.",
        "not_installed": "Claude Code is not installed.",
        "timeout": "Generation timed out.",
    }.get(reason_kind, "Generation failed for another reason.")
    title = paper.title if paper else "(no paper selected)"
    return {
        "text": f"Introduction generation failed: {reason_text}",
        "blocks": [
            {"type": "header", "text": {"type": "plain_text", "text": "Generation failed"}},
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*{reason_text}*\nPaper: {_short(title, 150)}\n"
                    f"You can still see any saved details in the web app.",
                },
            },
        ],
    }


def _post_once(webhook_url: str, payload: dict, *, opener=urllib.request.urlopen) -> int:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        webhook_url, data=data, headers={"Content-Type": "application/json"}
    )
    with opener(req, timeout=30) as resp:
        return resp.getcode()


def post(webhook_url: str, payload: dict, *, opener=urllib.request.urlopen) -> bool:
    """Post to Slack with exponential backoff. Returns True on success.

    `opener` is injectable so tests avoid real network calls.
    """
    if not webhook_url:
        log.error("no Slack webhook URL configured; skipping notification")
        return False

    delay = 1.0
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            code = _post_once(webhook_url, payload, opener=opener)
            if 200 <= code < 300:
                return True
            log.warning("Slack post attempt %d returned status %s", attempt, code)
        except (urllib.error.URLError, OSError) as exc:
            log.warning("Slack post attempt %d failed: %s", attempt, exc)
        if attempt < MAX_RETRIES:
            time.sleep(delay)
            delay *= 2
    return False
