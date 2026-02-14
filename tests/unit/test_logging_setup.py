from __future__ import annotations

import logging
from pathlib import Path

import meshcore_console.meshcore.logging_setup as log_mod


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
