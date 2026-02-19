"""Centralised logging configuration for meshcore-uconsole.

Provides:
- Rotating file handler (always DEBUG) at ~/.local/state/meshcore-uconsole/app.log
- stderr stream handler (configurable level)
- Log export helpers for bug reports
"""

from __future__ import annotations

import logging
import os
import shutil
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Callable

LOG_DIR = Path.home() / ".local" / "state" / "meshcore-uconsole"
LOG_FILE = LOG_DIR / "app.log"
LOG_FORMAT = "[%(name)s] %(message)s"
FILE_LOG_FORMAT = "%(asctime)s %(levelname)-8s [%(name)s] %(message)s"
MAX_BYTES = 1_000_000  # 1 MB per file
BACKUP_COUNT = 3

VALID_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")

_stderr_handler: logging.StreamHandler | None = None
_configured = False


def configure_logging(console_level: str | None = None) -> None:
    """Set up root logger with stderr and rotating file handlers.

    *console_level* sets the stderr handler level.  The ``LOG_LEVEL``
    environment variable takes precedence when set.  Falls back to
    ``"INFO"`` if neither is provided.

    Safe to call more than once (duplicate handlers are skipped).
    """
    global _stderr_handler, _configured  # noqa: PLW0603

    if _configured:
        return

    env_level = os.environ.get("LOG_LEVEL", "").upper()
    if env_level and env_level in VALID_LEVELS:
        effective_level = env_level
    elif console_level and console_level.upper() in VALID_LEVELS:
        effective_level = console_level.upper()
    else:
        effective_level = "INFO"

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)

    # stderr handler
    _stderr_handler = logging.StreamHandler(sys.stderr)
    _stderr_handler.setLevel(getattr(logging, effective_level))
    _stderr_handler.setFormatter(logging.Formatter(LOG_FORMAT))
    root.addHandler(_stderr_handler)

    # Rotating file handler
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    file_handler = RotatingFileHandler(LOG_FILE, maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT)
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter(FILE_LOG_FORMAT))
    root.addHandler(file_handler)

    _configured = True


def set_stderr_level(level_name: str) -> None:
    """Change the stderr handler log level at runtime."""
    if _stderr_handler is None:
        return
    upper = level_name.upper()
    if upper in VALID_LEVELS:
        _stderr_handler.setLevel(getattr(logging, upper))


def get_log_files_chronological() -> list[Path]:
    """Return all log files oldest-first (backup.3 -> backup.1 -> current)."""
    files: list[Path] = []
    for i in range(BACKUP_COUNT, 0, -1):
        p = LOG_FILE.with_suffix(f".log.{i}")
        if p.exists():
            files.append(p)
    if LOG_FILE.exists():
        files.append(LOG_FILE)
    return files


def export_logs_to_path(dest: str | Path) -> Path:
    """Concatenate all log files into *dest* (oldest first). Returns dest path."""
    dest = Path(dest)
    with dest.open("w") as out:
        for log_file in get_log_files_chronological():
            with log_file.open() as f:
                shutil.copyfileobj(f, out)
    return dest


def export_logs_to_stdout() -> None:
    """Print concatenated logs to stdout."""
    for log_file in get_log_files_chronological():
        with log_file.open() as f:
            shutil.copyfileobj(f, sys.stdout)


# ---------------------------------------------------------------------------
# Radio error interception
# ---------------------------------------------------------------------------

_RADIO_LOGGER_SUBSTRINGS = ("SX1262", "pyMC", "meshcore_console.meshcore.session")


class RadioErrorHandler(logging.Handler):
    """Intercepts WARNING+ log messages from radio-layer loggers."""

    def __init__(self, callback: Callable[[str], None]) -> None:
        super().__init__(level=logging.WARNING)
        self._callback = callback

    def emit(self, record: logging.LogRecord) -> None:
        if any(sub in record.name for sub in _RADIO_LOGGER_SUBSTRINGS):
            try:
                self._callback(record.getMessage())
            except Exception:  # noqa: BLE001
                pass


def install_radio_error_handler(callback: Callable[[str], None]) -> RadioErrorHandler:
    """Attach a :class:`RadioErrorHandler` to the root logger and return it."""
    handler = RadioErrorHandler(callback)
    logging.getLogger().addHandler(handler)
    return handler
