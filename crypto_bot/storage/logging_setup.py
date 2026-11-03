"""Console + rotating file logging.

The log file rotates at ``max_bytes`` and keeps ``backup_count`` old files
(bot.log.1, bot.log.2, ...), so it can never fill your VPS disk.
"""
from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from config.settings import Settings

FMT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"


def setup_logging(settings: Settings) -> None:
    root = logging.getLogger()
    root.setLevel(settings.logging.level)
    for h in list(root.handlers):
        root.removeHandler(h)
    formatter = logging.Formatter(FMT, datefmt="%Y-%m-%dT%H:%M:%S%z")

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)
    root.addHandler(console)

    log_path = Path(settings.path(settings.logging.log_file))
    log_path.parent.mkdir(parents=True, exist_ok=True)
    fh = RotatingFileHandler(log_path, maxBytes=settings.logging.max_bytes,
                             backupCount=settings.logging.backup_count, encoding="utf-8")
    fh.setFormatter(formatter)
    root.addHandler(fh)

    # Third-party libraries are chatty at DEBUG/INFO.
    for noisy in ("urllib3", "httpx", "httpx2", "anthropic", "ccxt"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
