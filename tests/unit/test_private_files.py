"""Identity seed, database and logs are owner-only (0600)."""

from __future__ import annotations

import os
import stat
from pathlib import Path

from openhop_core.protocol.identity import LocalIdentity

from meshcore_console.meshcore import logging_setup, paths
from meshcore_console.meshcore.db import open_db
from meshcore_console.meshcore.runtime import create_mesh_node


def mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def make_node(**_kwargs: object) -> object:
    return object()


def test_new_identity_seed_is_owner_only() -> None:
    old = os.umask(0o022)  # even under a permissive umask
    try:
        create_mesh_node(make_node, LocalIdentity, radio=None, event_service=None, node_name="n")
    finally:
        os.umask(old)
    assert mode(paths.identity_key_path()) == 0o600


def test_existing_world_readable_seed_is_tightened() -> None:
    key = paths.identity_key_path()
    key.parent.mkdir(parents=True)
    key.write_bytes(LocalIdentity().get_signing_key_bytes())
    key.chmod(0o644)
    create_mesh_node(make_node, LocalIdentity, radio=None, event_service=None, node_name="n")
    assert mode(key) == 0o600


def test_database_files_are_owner_only(tmp_path: Path) -> None:
    db = tmp_path / "meshcore.db"
    open_db(str(db)).close()
    db.chmod(0o644)
    conn = open_db(str(db))
    assert mode(db) == 0o600
    for extra in ("meshcore.db-wal", "meshcore.db-shm"):
        if (tmp_path / extra).exists():
            assert mode(tmp_path / extra) == 0o600
    conn.close()


def test_log_export_is_owner_only(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(logging_setup, "LOG_DIR", tmp_path)
    (tmp_path / "app.log").write_text("line\n")
    dest = logging_setup.export_logs_to_path(tmp_path / "export.txt")
    assert dest.read_text() == "line\n" and mode(dest) == 0o600
