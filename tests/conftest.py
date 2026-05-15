"""Pytest conftest: ensure .env is loaded before tests collect.

Without this, `os.environ.get("ALPACA_KEY_ID")` returns None in test guards even
when .env is populated, because pydantic-settings doesn't inject into os.environ.
"""
from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(REPO_ROOT / ".env", override=False)
