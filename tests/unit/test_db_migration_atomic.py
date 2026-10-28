"""An interrupted schema upgrade leaves the database as it was."""

from __future__ import annotations

import sqlite3

import pytest

from meshcore_console.meshcore import db as db_mod


def v1_database(path) -> None:
    conn = sqlite3.connect(str(path))
    for stmt in db_mod.MIGRATIONS[0]:
        conn.execute(stmt)
    conn.commit()
    conn.close()


def test_failed_upgrade_rolls_back_and_the_next_open_succeeds(tmp_path, monkeypatch) -> None:
    path = tmp_path / "meshcore.db"
    v1_database(path)

    def power_cut(_conn) -> None:
        raise OSError("interrupted")

    monkeypatch.setattr(db_mod, "_add_region_scope_column", power_cut)
    with pytest.raises(OSError):
        db_mod.open_db(str(path))
    monkeypatch.undo()

    raw = sqlite3.connect(str(path))
    assert raw.execute("SELECT version FROM schema_version").fetchone()[0] == 1
    columns = [row[1] for row in raw.execute("PRAGMA table_info(channels)")]
    assert "peer_name" not in columns  # the v2 ALTER was rolled back with the rest
    raw.close()

    conn = db_mod.open_db(str(path))  # used to fail with "duplicate column name"
    assert conn.execute("SELECT version FROM schema_version").fetchone()[0] == len(
        db_mod.MIGRATIONS
    )
    conn.close()
