from __future__ import annotations

import logging
import time
from pathlib import Path

import pytest

import meshcore_console.meshcore.logging_setup as log_mod
from meshcore_console.meshcore.logging_setup import (
    RadioErrorHandler,
    RfNoiseCounter,
    RfNoiseFilter,
    classify_rf_noise,
    is_radio_logger,
)


def _reset_module() -> None:
    """Reset module-level state so configure_logging can run again."""
    log_mod._configured = False
    log_mod._stderr_handler = None
    # Remove any handlers we previously added to root
    root = logging.getLogger()
    root.handlers = [
        h
        for h in root.handlers
        if not isinstance(
            h,
            (
                logging.StreamHandler,
                logging.handlers.RotatingFileHandler,
            ),
        )
    ]


def test_configure_logging_creates_handlers(tmp_path: Path, monkeypatch) -> None:
    _reset_module()
    log_dir = tmp_path / "state"
    log_file = log_dir / "app.log"
    monkeypatch.setattr(log_mod, "LOG_DIR", log_dir)
    monkeypatch.setattr(log_mod, "LOG_FILE", log_file)
    monkeypatch.delenv("LOG_LEVEL", raising=False)

    log_mod.configure_logging()

    root = logging.getLogger()
    handler_types = [type(h).__name__ for h in root.handlers]
    assert "StreamHandler" in handler_types
    assert "RotatingFileHandler" in handler_types
    assert log_file.exists()

    _reset_module()


def test_set_stderr_level(tmp_path: Path, monkeypatch) -> None:
    _reset_module()
    log_dir = tmp_path / "state"
    monkeypatch.setattr(log_mod, "LOG_DIR", log_dir)
    monkeypatch.setattr(log_mod, "LOG_FILE", log_dir / "app.log")
    monkeypatch.delenv("LOG_LEVEL", raising=False)

    log_mod.configure_logging("INFO")
    assert log_mod._stderr_handler is not None
    assert log_mod._stderr_handler.level == logging.INFO

    log_mod.set_stderr_level("DEBUG")
    assert log_mod._stderr_handler.level == logging.DEBUG

    log_mod.set_stderr_level("WARNING")
    assert log_mod._stderr_handler.level == logging.WARNING

    _reset_module()


def test_export_logs_concatenates(tmp_path: Path, monkeypatch) -> None:
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    log_file = log_dir / "app.log"

    monkeypatch.setattr(log_mod, "LOG_DIR", log_dir)
    monkeypatch.setattr(log_mod, "LOG_FILE", log_file)
    monkeypatch.setattr(log_mod, "BACKUP_COUNT", 3)

    # Create backup and current log files
    (log_dir / "app.log.2").write_text("line-from-backup-2\n")
    (log_dir / "app.log.1").write_text("line-from-backup-1\n")
    log_file.write_text("line-from-current\n")

    dest = tmp_path / "export.txt"
    log_mod.export_logs_to_path(dest)

    content = dest.read_text()
    assert content == "line-from-backup-2\nline-from-backup-1\nline-from-current\n"

    # Verify chronological order (backup.2 before backup.1 before current)
    lines = content.strip().split("\n")
    assert lines == ["line-from-backup-2", "line-from-backup-1", "line-from-current"]


# ---------------------------------------------------------------------------
# Radio log classification (issue #91)
# ---------------------------------------------------------------------------

CRC_MESSAGE = (
    "[RX] CRC error #1 - RSSI=-113dBm, SNR=-7.0dB, SignalRSSI=-119dBm, "
    "Length=55, NoiseFloor=-113.8dBm, DeviceErrors=0x0000, IRQ=0x0042, RawData=0c110ee3"
)
HEADER_MESSAGE = "[RX] Header error detected (0x0022) - corrupted header, restoring RX mode"
REAL_ERROR = "Radio stayed busy - cannot start transmission"


def _record(name: str, message: str, level: int = logging.WARNING) -> logging.LogRecord:
    return logging.LogRecord(name, level, __file__, 1, message, None, None)


@pytest.mark.parametrize(
    ("message", "category"),
    [
        (CRC_MESSAGE, "crc"),
        (HEADER_MESSAGE, "header"),
        ("[RX] Empty packet received", "empty"),
        (REAL_ERROR, None),
        ("[RX] RX timeout detected", None),
    ],
)
def test_classify_rf_noise(message: str, category: str | None) -> None:
    assert classify_rf_noise(message) == category


def test_is_radio_logger() -> None:
    assert is_radio_logger("SX1262_wrapper")
    assert is_radio_logger("meshcore_console.meshcore.session")
    assert not is_radio_logger("meshcore_console.ui_gtk.views.settings")


def test_rf_noise_filter_drops_noise_and_keeps_errors() -> None:
    noise_filter = RfNoiseFilter()
    assert not noise_filter.filter(_record("SX1262_wrapper", CRC_MESSAGE))
    assert not noise_filter.filter(_record("SX1262_wrapper", HEADER_MESSAGE))
    assert noise_filter.filter(_record("SX1262_wrapper", REAL_ERROR))
    # Records from other loggers pass through untouched.
    assert noise_filter.filter(_record("meshcore_console.app", CRC_MESSAGE))


def test_rf_noise_filter_yields_at_debug_level() -> None:
    handler = logging.StreamHandler()
    handler.setLevel(logging.DEBUG)
    noise_filter = RfNoiseFilter(handler)
    assert noise_filter.filter(_record("SX1262_wrapper", CRC_MESSAGE))

    handler.setLevel(logging.INFO)
    assert not noise_filter.filter(_record("SX1262_wrapper", CRC_MESSAGE))


def test_radio_error_handler_counts_noise_instead_of_reporting_it() -> None:
    log_mod._rf_noise_counter.reset()
    seen: list[str] = []
    handler = RadioErrorHandler(seen.append)

    handler.emit(_record("SX1262_wrapper", CRC_MESSAGE))
    handler.emit(_record("SX1262_wrapper", HEADER_MESSAGE))
    handler.emit(_record("SX1262_wrapper", REAL_ERROR))
    handler.emit(_record("meshcore_console.ui_gtk", REAL_ERROR))

    assert seen == [REAL_ERROR]
    assert log_mod.get_rf_noise_counts() == {"crc": 1, "header": 1}
    log_mod._rf_noise_counter.reset()


def test_rf_noise_counter_flush_summarises_the_open_window(
    caplog: pytest.LogCaptureFixture,
) -> None:
    counter = RfNoiseCounter(summary_seconds=3600.0)
    try:
        with caplog.at_level(logging.INFO, logger="meshcore_console.radio_noise"):
            counter.record("crc")
            counter.record("crc")
            counter.record("header")
            counter.flush()
        assert counter.totals() == {"crc": 2, "header": 1}
        assert len(caplog.records) == 1
        assert "2 crc" in caplog.records[0].message
        assert "1 header" in caplog.records[0].message
    finally:
        counter.reset()


def test_rf_noise_counter_flush_is_quiet_with_no_noise(caplog: pytest.LogCaptureFixture) -> None:
    counter = RfNoiseCounter(summary_seconds=3600.0)
    with caplog.at_level(logging.INFO, logger="meshcore_console.radio_noise"):
        counter.flush()
        counter.flush()
    assert caplog.records == []


def test_rf_noise_counter_reports_a_burst_that_stops(caplog: pytest.LogCaptureFixture) -> None:
    """A burst shorter than the window must still reach the log (PR #93 review)."""
    counter = RfNoiseCounter(summary_seconds=0.05)
    try:
        with caplog.at_level(logging.INFO, logger="meshcore_console.radio_noise"):
            counter.record("crc")
            # No further events: only the window timer can write the summary.
            deadline = time.monotonic() + 5.0
            while not caplog.records and time.monotonic() < deadline:
                time.sleep(0.01)
        assert len(caplog.records) == 1
        assert "1 crc" in caplog.records[0].message
    finally:
        counter.reset()


def test_rf_noise_counter_reset_cancels_the_window(caplog: pytest.LogCaptureFixture) -> None:
    counter = RfNoiseCounter(summary_seconds=0.05)
    with caplog.at_level(logging.INFO, logger="meshcore_console.radio_noise"):
        counter.record("crc")
        counter.reset()
        time.sleep(0.2)
    assert counter.totals() == {}
    assert caplog.records == []
