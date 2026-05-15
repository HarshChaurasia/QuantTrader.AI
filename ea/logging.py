"""Loguru-based logging with run-id correlation.

Every log record gets a `run_id` field (a per-process UUID) so that interleaved logs
from the live system can be reconstructed per-run. Override the run_id with
`set_run_id()` for sub-tasks (backtests, paper sessions, etc.).

Usage:
    from ea.logging import logger, setup_logging
    setup_logging()
    logger.info("hello")
"""
from __future__ import annotations

import sys
import uuid
from contextvars import ContextVar
from pathlib import Path

from loguru import logger

from ea.config import REPO_ROOT, get_config

_LOG_DIR = REPO_ROOT / "logs"
_run_id: ContextVar[str] = ContextVar("run_id", default=uuid.uuid4().hex[:8])

# Re-export the configured logger so callers do `from ea.logging import logger`
__all__ = ["logger", "setup_logging", "set_run_id", "get_run_id"]


def get_run_id() -> str:
    return _run_id.get()


def set_run_id(run_id: str) -> None:
    _run_id.set(run_id)


def _patcher(record: dict) -> None:
    record["extra"].setdefault("run_id", get_run_id())


_configured = False


def setup_logging(
    level: str | None = None,
    log_dir: Path | None = None,
    console: bool = True,
) -> None:
    """Configure loguru sinks. Idempotent — safe to call multiple times."""
    global _configured

    cfg_level = level
    if cfg_level is None:
        try:
            cfg_level = get_config().env.ea_log_level
        except Exception:
            cfg_level = "INFO"

    log_dir = log_dir or _LOG_DIR
    log_dir.mkdir(parents=True, exist_ok=True)

    logger.remove()
    logger.configure(patcher=_patcher)

    if console:
        logger.add(
            sys.stderr,
            level=cfg_level,
            format=(
                "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> "
                "<level>{level: <8}</level> "
                "<cyan>[{extra[run_id]}]</cyan> "
                "<cyan>{name}:{function}:{line}</cyan> - "
                "<level>{message}</level>"
            ),
            enqueue=False,
            backtrace=False,
            diagnose=False,
        )

    logger.add(
        log_dir / "ea.log",
        level=cfg_level,
        rotation="50 MB",
        retention="14 days",
        compression="zip",
        format=(
            "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | "
            "[{extra[run_id]}] | {name}:{function}:{line} | {message}"
        ),
        enqueue=True,  # safe across threads/processes
        backtrace=False,
        diagnose=False,
    )

    _configured = True
    logger.debug("logging configured at level={}", cfg_level)
