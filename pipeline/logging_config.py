"""Logging configuration — rotating file handler + stderr mirror.

Writes all pipeline events to {log_dir}/pipeline.log (default `logs/`), rotated
at 1MB with 5 backups kept. Also mirrors to stderr so running `python pipeline/run.py`
shows the output live. launchd captures stderr to its own log file as a backup.

Idempotent — calling setup_logging() multiple times in the same process will
not attach duplicate handlers.
"""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

_CONFIGURED = False

LOG_FORMAT = "%(asctime)s | %(levelname)-7s | %(name)-40s | %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def setup_logging(
    log_dir: str | Path = "logs",
    level: int = logging.INFO,
    max_bytes: int = 1_000_000,
    backup_count: int = 5,
) -> None:
    """Attach a rotating file handler + stderr stream handler to the root logger.

    Args:
        log_dir: Directory where pipeline.log will live. Created if missing.
        level: Root logger level. Default INFO.
        max_bytes: Per-file rotation threshold. Default 1MB.
        backup_count: Number of rotated files to retain. Default 5.
    """
    global _CONFIGURED
    if _CONFIGURED:
        return

    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    root.setLevel(level)

    formatter = logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT)

    file_handler = RotatingFileHandler(
        log_path / "pipeline.log",
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(level)

    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setFormatter(formatter)
    stderr_handler.setLevel(level)

    # Remove any preexisting handlers so we own the configuration
    for h in list(root.handlers):
        root.removeHandler(h)

    root.addHandler(file_handler)
    root.addHandler(stderr_handler)

    _CONFIGURED = True
