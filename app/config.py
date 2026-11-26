"""Load configuration from config.toml and secrets from the environment.

config.toml holds everything that is safe to read on screen (journals, keywords,
genre ratio, notification time, proxy template). Secrets (Slack webhook,
Anthropic API key) come from the environment / .env and are never stored here.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

# Project root is the directory that contains config.toml (parent of app/).
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config.toml"
DATA_DIR = PROJECT_ROOT / "data"
DB_PATH = DATA_DIR / "app.db"
PDF_DIR = DATA_DIR / "pdfs"
LOG_DIR = DATA_DIR / "logs"


@dataclass(frozen=True)
class Journal:
    name: str
    issn: str
    tier: str  # "top" | "specialist" | "other"


@dataclass(frozen=True)
class Config:
    """Parsed configuration plus the secrets read from the environment."""

    raw: dict
    contact_email: str
    notify_time: str
    lookback_days: int

    genre_electrochem: float
    genre_other: float
    balance_window_days: int

    max_intros_per_day: int

    llm_backend: str
    llm_model: str
    llm_timeout_seconds: int
    llm_max_retries: int

    proxy_url_template: str

    journal_weight_top: int
    journal_weight_specialist: int
    journal_weight_other: int
    feedback_bonus: int
    keyword_weights: dict[str, int]
    electrochem_keywords: list[str]

    journals: list[Journal]
    pubmed_query: str

    # Secrets (may be empty depending on the active backend).
    slack_webhook_url: str = ""
    anthropic_api_key: str = ""

    # Where the SQLite database lives; overridable mainly for tests.
    db_path: Path = field(default=DB_PATH)


def _load_env_file(path: Path) -> None:
    """Load KEY=VALUE pairs from a .env file into os.environ if not already set.

    Existing environment variables win, so the real environment can override the
    file. This is a minimal loader to avoid a python-dotenv dependency for the
    small number of secrets we read.
    """
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def load_config(
    config_path: Path | None = None,
    *,
    db_path: Path | None = None,
    load_env: bool = True,
) -> Config:
    """Read config.toml (and optionally .env) into a Config object."""
    config_path = config_path or DEFAULT_CONFIG_PATH
    if load_env:
        _load_env_file(PROJECT_ROOT / ".env")

    with open(config_path, "rb") as fh:
        raw = tomllib.load(fh)

    general = raw.get("general", {})
    genre = raw.get("genre", {})
    limits = raw.get("limits", {})
    llm = raw.get("llm", {})
    fulltext = raw.get("fulltext", {})
    scoring = raw.get("scoring", {})

    keyword_weights = {
        str(k): int(v) for k, v in scoring.get("keywords", {}).items()
    }
    electrochem_keywords = [
        str(t) for t in genre.get("electrochem_keywords", {}).get("terms", [])
    ]

    journals = [
        Journal(name=j["name"], issn=j["issn"], tier=j.get("tier", "other"))
        for j in raw.get("journals", [])
    ]

    return Config(
        raw=raw,
        contact_email=general.get("contact_email", "you@example.com"),
        notify_time=general.get("notify_time", "07:00"),
        lookback_days=int(general.get("lookback_days", 3)),
        genre_electrochem=float(genre.get("electrochem", 0.4)),
        genre_other=float(genre.get("other", 0.6)),
        balance_window_days=int(genre.get("balance_window_days", 10)),
        max_intros_per_day=int(limits.get("max_intros_per_day", 3)),
        llm_backend=str(llm.get("backend", "claude_code")),
        llm_model=str(llm.get("model", "claude-sonnet-4-5")),
        llm_timeout_seconds=int(llm.get("timeout_seconds", 180)),
        llm_max_retries=int(llm.get("max_retries", 2)),
        proxy_url_template=str(fulltext.get("proxy_url_template", "")),
        journal_weight_top=int(scoring.get("journal_weight_top", 3)),
        journal_weight_specialist=int(scoring.get("journal_weight_specialist", 2)),
        journal_weight_other=int(scoring.get("journal_weight_other", 1)),
        feedback_bonus=int(scoring.get("feedback_bonus", 1)),
        keyword_weights=keyword_weights,
        electrochem_keywords=electrochem_keywords,
        journals=journals,
        pubmed_query=str(raw.get("pubmed", {}).get("query", "")),
        slack_webhook_url=os.environ.get("SLACK_WEBHOOK_URL", ""),
        anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY", ""),
        db_path=db_path or DB_PATH,
    )
