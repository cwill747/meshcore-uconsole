"""Tests must never touch the developer's real app state."""

from __future__ import annotations

from pathlib import Path

from meshcore_console.meshcore import logging_setup, paths


def test_app_dirs_are_redirected_away_from_home() -> None:
    real = Path.home()
    for path in (paths.db_path(), paths.identity_key_path(), paths.config_dir()):
        assert real / ".local" not in path.parents and real / ".config" not in path.parents
    assert real / ".local" not in logging_setup.log_file().parents
