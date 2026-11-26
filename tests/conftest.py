"""Shared fixtures and helpers for the test suite."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Make the project importable without installing it.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import load_config  # noqa: E402
from app.db import init_db  # noqa: E402


@pytest.fixture
def cfg(tmp_path):
    return load_config(
        config_path=ROOT / "config.toml",
        db_path=tmp_path / "app.db",
        load_env=False,
    )


@pytest.fixture
def conn(cfg):
    c = init_db(cfg.db_path)
    yield c
    c.close()
