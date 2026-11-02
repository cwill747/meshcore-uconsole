from meshcore_console.meshcore.db import open_db
from meshcore_console.meshcore.repeater_store import RepeaterPasswordStore

KEY = "13ab5c7e" + "11" * 28


def test_save_and_get_password(tmp_path) -> None:
    conn = open_db(str(tmp_path / "test.db"))
    store = RepeaterPasswordStore(conn)

    store.save_password(KEY, "s3cret")
    assert store.get_password(KEY) == "s3cret"
    conn.close()


def test_get_missing_password(tmp_path) -> None:
    conn = open_db(str(tmp_path / "test.db"))
    store = RepeaterPasswordStore(conn)

    assert store.get_password("ee" * 32) is None
    conn.close()


def test_delete_password(tmp_path) -> None:
    conn = open_db(str(tmp_path / "test.db"))
    store = RepeaterPasswordStore(conn)

    store.save_password(KEY, "s3cret")
    store.delete_password(KEY)
    assert store.get_password(KEY) is None
    conn.close()


def test_overwrite_password(tmp_path) -> None:
    conn = open_db(str(tmp_path / "test.db"))
    store = RepeaterPasswordStore(conn)

    store.save_password(KEY, "old")
    store.save_password(KEY, "new")
    assert store.get_password(KEY) == "new"
    conn.close()


def test_list_saved(tmp_path) -> None:
    conn = open_db(str(tmp_path / "test.db"))
    store = RepeaterPasswordStore(conn)

    assert store.list_saved() == []
    store.save_password("BB" * 32, "pw1")
    store.save_password("aa" * 32, "pw2")
    assert store.list_saved() == ["aa" * 32, "bb" * 32]
    conn.close()


def test_migration_binds_saved_passwords_to_keys(tmp_path) -> None:
    import sqlite3

    from meshcore_console.meshcore import db as db_mod

    path = tmp_path / "old.db"
    conn = sqlite3.connect(str(path))
    for version in db_mod.MIGRATIONS[:9]:
        for stmt in version:
            conn.execute(stmt)
    db_mod._backfill_channel_secrets(conn)
    db_mod._add_region_scope_column(conn)
    conn.execute("UPDATE schema_version SET version = 9")
    peers = [("p1", "Hill", KEY.upper()), ("p2", "Twin", "22" * 32), ("p3", "Twin", "33" * 32)]
    conn.executemany(
        "INSERT INTO peers (peer_id, display_name, public_key) VALUES (?, ?, ?)", peers
    )
    for name, password in (("Hill", "aGlsbA=="), ("Twin", "dHdpbg=="), ("Gone", "Z29uZQ==")):
        conn.execute(
            "INSERT INTO repeater_passwords (peer_name, password, created_at) VALUES (?, ?, '')",
            (name, password),
        )
    conn.commit()
    conn.close()

    store = RepeaterPasswordStore(open_db(str(path)))
    # Twin is two keys and Gone none: no telling whose those were
    assert store.list_saved() == [KEY]
    assert store.get_password(KEY) == "hill"
