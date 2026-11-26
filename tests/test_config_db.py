"""Config loading and schema creation."""

from __future__ import annotations

from app.config import PROJECT_ROOT, load_config


def test_config_defaults():
    cfg = load_config(config_path=PROJECT_ROOT / "config.toml", load_env=False)
    assert cfg.notify_time == "07:00"
    assert cfg.llm_backend == "claude_code"
    assert cfg.genre_electrochem == 0.4
    assert cfg.genre_other == 0.6
    assert cfg.max_intros_per_day == 3
    # Journal list parsed with tiers.
    names = {j.name for j in cfg.journals}
    assert "Journal of the American Chemical Society" in names
    assert any(j.tier == "top" for j in cfg.journals)
    assert any(j.tier == "specialist" for j in cfg.journals)
    # Keyword weights and electrochem terms present.
    assert cfg.keyword_weights["ionophore"] == 2
    assert "voltammetry" in cfg.electrochem_keywords


def test_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.com/services/x")
    cfg = load_config(config_path=PROJECT_ROOT / "config.toml", load_env=False)
    assert cfg.slack_webhook_url.endswith("/x")


def test_schema_created(conn):
    tables = {
        r["name"]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert {"papers", "introductions", "feedback", "library", "job_runs"} <= tables
