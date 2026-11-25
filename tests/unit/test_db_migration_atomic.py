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


def test_two_openers_can_upgrade_same_database(tmp_path, monkeypatch) -> None:
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    path = tmp_path / "meshcore.db"
    v1_database(path)
    before_lock = Barrier(2)
    read_version = db_mod._get_version

    def synchronize_first_reads(conn):
        version = read_version(conn)
        if not conn.in_transaction:
            before_lock.wait(timeout=5)
        return version

    monkeypatch.setattr(db_mod, "_get_version", synchronize_first_reads)

    def upgrade():
        conn = sqlite3.connect(path)
        try:
            db_mod._migrate(conn)
            return read_version(conn)
        finally:
            conn.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(upgrade) for _ in range(2)]
        assert [f.result() for f in futures] == [len(db_mod.MIGRATIONS)] * 2
