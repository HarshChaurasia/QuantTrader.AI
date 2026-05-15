"""Tests for ea.logging — sinks, rotation config, run-id correlation."""
from __future__ import annotations

import time

from ea.logging import get_run_id, logger, set_run_id, setup_logging


def test_logging_writes_to_file(tmp_path):
    setup_logging(level="DEBUG", log_dir=tmp_path, console=False)
    logger.info("hello from test")

    # loguru with enqueue=True is async; give it a moment, then complete
    logger.complete()
    time.sleep(0.05)

    log_file = tmp_path / "ea.log"
    assert log_file.exists(), f"log file not created at {log_file}"
    content = log_file.read_text(encoding="utf-8")
    assert "hello from test" in content


def test_run_id_appears_in_log(tmp_path):
    setup_logging(level="DEBUG", log_dir=tmp_path, console=False)
    set_run_id("test1234")
    logger.info("tagged event")
    logger.complete()
    time.sleep(0.05)

    content = (tmp_path / "ea.log").read_text(encoding="utf-8")
    assert "test1234" in content
    assert "tagged event" in content


def test_run_id_changes_between_runs():
    set_run_id("aaaa1111")
    assert get_run_id() == "aaaa1111"
    set_run_id("bbbb2222")
    assert get_run_id() == "bbbb2222"
