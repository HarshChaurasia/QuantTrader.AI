"""Daily equity baseline persistence — survives process restart same day.

We need a stable "starting equity for today" anchor so PnL% in the dashboard
doesn't reset to 0 every time you restart the dashboard.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from ea.config import REPO_ROOT
from ea.logging import logger

PATH = REPO_ROOT / "data" / "equity_baseline.json"


def today_key() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def load_today() -> float | None:
    if not PATH.exists():
        return None
    try:
        data = json.loads(PATH.read_text())
        if data.get("date") == today_key():
            return float(data["equity"])
    except Exception as e:
        logger.warning("equity baseline load failed: {}", e)
    return None


def save_today(equity: float) -> None:
    PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        PATH.write_text(json.dumps({"date": today_key(), "equity": float(equity)}))
    except Exception as e:
        logger.warning("equity baseline save failed: {}", e)
