"""Smoke tests for logging_config.setup_logging()."""

from __future__ import annotations

import logging
from pathlib import Path

from pipeline import logging_config


def test_setup_logging_creates_log_dir_and_file(tmp_path: Path, monkeypatch):
    # Reset global flag so each test can reconfigure
    monkeypatch.setattr(logging_config, "_CONFIGURED", False)

    log_dir = tmp_path / "logs"
    logging_config.setup_logging(log_dir=log_dir, level=logging.DEBUG)

    assert log_dir.exists()
    logger = logging.getLogger("test.pipeline")
    logger.info("test message")

    log_file = log_dir / "pipeline.log"
    assert log_file.exists()
    assert "test message" in log_file.read_text(encoding="utf-8")


def test_setup_logging_is_idempotent(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(logging_config, "_CONFIGURED", False)

    log_dir = tmp_path / "logs"
    logging_config.setup_logging(log_dir=log_dir)
    handler_count_after_first = len(logging.getLogger().handlers)

    logging_config.setup_logging(log_dir=log_dir)  # second call — should no-op
    handler_count_after_second = len(logging.getLogger().handlers)

    assert handler_count_after_first == handler_count_after_second
