"""Logging configuration that masks secrets before anything is written.

Slack webhook URLs and Anthropic API keys must never appear in logs or on
screen (requirements R7-4). A logging filter rewrites those patterns in every
record, so even an accidental log.info(webhook_url) is scrubbed.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

# Patterns that identify secrets regardless of the surrounding text.
_SLACK_WEBHOOK_RE = re.compile(r"https://hooks\.slack\.com/\S+")
_ANTHROPIC_KEY_RE = re.compile(r"sk-ant-[A-Za-z0-9_\-]+")


def mask_secrets(text: str) -> str:
    text = _SLACK_WEBHOOK_RE.sub("https://hooks.slack.com/***", text)
    text = _ANTHROPIC_KEY_RE.sub("sk-ant-***", text)
    return text


class SecretMaskingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = mask_secrets(record.msg)
        if record.args:
            record.args = tuple(
                mask_secrets(a) if isinstance(a, str) else a for a in record.args
            )
        return True


def setup_logging(log_dir: Path | None = None, *, level: int = logging.INFO) -> logging.Logger:
    """Configure the root 'chem_paper_daily' logger with secret masking."""
    logger = logging.getLogger("chem_paper_daily")
    logger.setLevel(level)
    logger.handlers.clear()

    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    mask = SecretMaskingFilter()

    stream = logging.StreamHandler()
    stream.setFormatter(fmt)
    stream.addFilter(mask)
    logger.addHandler(stream)

    if log_dir is not None:
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_dir / "daily_job.log", encoding="utf-8")
        file_handler.setFormatter(fmt)
        file_handler.addFilter(mask)
        logger.addHandler(file_handler)

    return logger
