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
import threading
import time
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
    _stderr_handler.addFilter(RfNoiseFilter(_stderr_handler))
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
# Radio log classification
# ---------------------------------------------------------------------------

_RADIO_LOGGER_SUBSTRINGS = ("SX1262", "pyMC", "meshcore_console.meshcore.session")

# Reception noise that every LoRa radio reports when a transmitter is weak,
# distant, or collides with another one. The radio driver logs these at
# WARNING, but they are not device faults, so they must not raise the
# "Radio Error" badge or a toast (issue #91).
_RF_NOISE_PATTERNS: tuple[tuple[str, str], ...] = (
    ("crc", "crc error"),
    ("header", "header error"),
    ("header", "corrupted header"),
    ("empty", "empty packet received"),
)

# How often the counter writes a noise summary to the log.
NOISE_SUMMARY_SECONDS = 60.0

_noise_logger = logging.getLogger("meshcore_console.radio_noise")


def is_radio_logger(name: str) -> bool:
    """Return True if *name* is a radio-layer logger."""
    return any(sub in name for sub in _RADIO_LOGGER_SUBSTRINGS)


def classify_rf_noise(message: str) -> str | None:
    """Return the noise category of *message*, or ``None`` for a real error."""
    lowered = message.lower()
    for category, pattern in _RF_NOISE_PATTERNS:
        if pattern in lowered:
            return category
    return None


class RfNoiseCounter:
    """Counts RF reception noise and writes a periodic rate summary."""

    def __init__(self, summary_seconds: float = NOISE_SUMMARY_SECONDS) -> None:
        self._summary_seconds = summary_seconds
        self._lock = threading.Lock()
        self._totals: dict[str, int] = {}
        self._window: dict[str, int] = {}
        self._window_start = time.monotonic()

    def record(self, category: str) -> None:
        """Count one noise event and log a summary once per window."""
        with self._lock:
            self._totals[category] = self._totals.get(category, 0) + 1
            self._window[category] = self._window.get(category, 0) + 1
            elapsed = time.monotonic() - self._window_start
            if elapsed < self._summary_seconds:
                return
            summary = ", ".join(f"{count} {name}" for name, count in sorted(self._window.items()))
            self._window = {}
            self._window_start = time.monotonic()
        _noise_logger.info("RX noise in the last %.0fs: %s", elapsed, summary)

    def totals(self) -> dict[str, int]:
        """Return the counts since start, keyed by category."""
        with self._lock:
            return dict(self._totals)

    def reset(self) -> None:
        with self._lock:
            self._totals = {}
            self._window = {}
            self._window_start = time.monotonic()


_rf_noise_counter = RfNoiseCounter()


def get_rf_noise_counts() -> dict[str, int]:
    """Return the RF reception noise counts since start, keyed by category."""
    return _rf_noise_counter.totals()


class RfNoiseFilter(logging.Filter):
    """Drops RF reception noise from the handler it is attached to.

    The rotating file handler keeps every record for bug reports, so only the
    console handler uses this filter. Set *handler* to the guarded handler to
    show the noise again at DEBUG level, where the operator asks for
    everything.
    """

    def __init__(self, handler: logging.Handler | None = None) -> None:
        super().__init__()
        self._handler = handler

    def filter(self, record: logging.LogRecord) -> bool:
        if self._handler is not None and self._handler.level <= logging.DEBUG:
            return True
        if record.levelno != logging.WARNING or not is_radio_logger(record.name):
            return True
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001
            return True
        return classify_rf_noise(message) is None


# ---------------------------------------------------------------------------
# Radio error interception
# ---------------------------------------------------------------------------


class RadioErrorHandler(logging.Handler):
    """Intercepts WARNING+ log messages from radio-layer loggers.

    Reception noise is counted instead of reported, so a busy or noisy band
    does not flood the UI with radio errors.
    """

    def __init__(self, callback: Callable[[str], None]) -> None:
        super().__init__(level=logging.WARNING)
        self._callback = callback

    def emit(self, record: logging.LogRecord) -> None:
        if not is_radio_logger(record.name):
            return
        try:
            message = record.getMessage()
            category = classify_rf_noise(message)
            if category is not None:
                _rf_noise_counter.record(category)
                return
            self._callback(message)
        except Exception:  # noqa: BLE001
            pass


def install_radio_error_handler(callback: Callable[[str], None]) -> RadioErrorHandler:
    """Attach a :class:`RadioErrorHandler` to the root logger and return it."""
    handler = RadioErrorHandler(callback)
    logging.getLogger().addHandler(handler)
    return handler
