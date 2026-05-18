from meshcore_console.meshcore.db import open_db
from meshcore_console.meshcore.repeater_store import RepeaterPasswordStore


def test_save_and_get_password(tmp_path) -> None:
    conn = open_db(str(tmp_path / "test.db"))
    store = RepeaterPasswordStore(conn)

    store.save_password("repeater-1", "s3cret")
    assert store.get_password("repeater-1") == "s3cret"
    conn.close()


def test_get_missing_password(tmp_path) -> None:
    conn = open_db(str(tmp_path / "test.db"))
    store = RepeaterPasswordStore(conn)

    assert store.get_password("nonexistent") is None
    conn.close()


def test_delete_password(tmp_path) -> None:
    conn = open_db(str(tmp_path / "test.db"))
    store = RepeaterPasswordStore(conn)

    store.save_password("repeater-1", "s3cret")
    store.delete_password("repeater-1")
    assert store.get_password("repeater-1") is None
    conn.close()


def test_overwrite_password(tmp_path) -> None:
    conn = open_db(str(tmp_path / "test.db"))
    store = RepeaterPasswordStore(conn)

    store.save_password("repeater-1", "old")
    store.save_password("repeater-1", "new")
    assert store.get_password("repeater-1") == "new"
    conn.close()


def test_list_saved(tmp_path) -> None:
    conn = open_db(str(tmp_path / "test.db"))
    store = RepeaterPasswordStore(conn)

    assert store.list_saved() == []
    store.save_password("bravo", "pw1")
    store.save_password("alpha", "pw2")
    assert store.list_saved() == ["alpha", "bravo"]
    conn.close()
