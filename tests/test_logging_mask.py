"""Secret masking in logs (task 6.2, requirements R7-4)."""

from __future__ import annotations

import logging

from app.logging_setup import mask_secrets, setup_logging


def test_mask_webhook_and_key():
    masked = mask_secrets(
        "posting to https://hooks.slack.com/services/T000/B000/abcDEF "
        "with key sk-ant-api03-SECRETVALUE123"
    )
    assert "abcDEF" not in masked
    assert "SECRETVALUE123" not in masked
    assert "hooks.slack.com/***" in masked
    assert "sk-ant-***" in masked


def test_filter_scrubs_log_records(tmp_path, caplog):
    setup_logging(tmp_path)
    with caplog.at_level(logging.INFO, logger="chem_paper_daily"):
        logging.getLogger("chem_paper_daily.test").info(
            "url=%s", "https://hooks.slack.com/services/zzz"
        )
    log_file = (tmp_path / "daily_job.log").read_text()
    assert "zzz" not in log_file
    assert "hooks.slack.com/***" in log_file
