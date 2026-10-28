"""A burst of events reaches the UI whole."""

from __future__ import annotations

from meshcore_console.meshcore.db import open_db


def _client(tmp_path, monkeypatch):
    from meshcore_console.meshcore import client as client_mod
    from meshcore_console.meshcore.client import MeshcoreClient
    from meshcore_console.meshcore.config import runtime_config_from_settings
    from meshcore_console.meshcore.settings import MeshcoreSettings
    from meshcore_console.mock import MockOpenHopCoreSession

    db_conn = open_db(str(tmp_path / "test.db"))
    monkeypatch.setattr(client_mod, "open_db", lambda *a, **k: db_conn)
    client = MeshcoreClient(
        session=MockOpenHopCoreSession(runtime_config_from_settings(MeshcoreSettings())),
        require_openhop=False,
    )
    burst = [{"type": "radio_error", "data": {"message": f"e{i}"}} for i in range(100)]
    monkeypatch.setattr(client._session, "drain_events", lambda max_items=50: burst[:max_items])
    return client


def test_poll_returns_every_drained_and_buffered_event(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    for i in range(3):
        client._append_event({"type": "advert_sent", "data": {"n": i}})

    events = client.poll_events(limit=100)

    assert len(events) == 103  # was truncated to the newest 100
    assert events[0]["data"]["message"] == "e0"
    assert not client._event_buffer


def test_one_failing_event_does_not_drop_the_rest(tmp_path, monkeypatch) -> None:
    import sqlite3

    client = _client(tmp_path, monkeypatch)
    batch = [{"type": "packet", "data": {"n": i}} for i in range(3)]
    monkeypatch.setattr(client._session, "drain_events", lambda max_items=50: list(batch))
    stored: list[int] = []

    def process(event) -> None:
        if event["data"]["n"] == 0:
            raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(client, "_process_event_for_peers", process)
    monkeypatch.setattr(client._packet_store, "append", lambda e: stored.append(e["data"]["n"]))

    events = client.poll_events(limit=100)

    assert len(events) == 3 and stored == [1, 2]


def test_each_packet_is_stored_once(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    frame = {"payload_type_name": "ACK", "packet_hash": "AB" * 8}
    batch = [{"type": "packet", "data": dict(frame)}, {"type": "raw_packet", "data": dict(frame)}]
    monkeypatch.setattr(client._session, "drain_events", lambda max_items=50: list(batch))
    stored: list[str] = []
    monkeypatch.setattr(client._packet_store, "append", lambda e: stored.append(e["type"]))

    client.poll_events(limit=100)

    assert stored == ["packet"]


def test_app_events_are_recorded_once_in_history(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    monkeypatch.setattr(client._session, "drain_events", lambda max_items=50: [])
    client._append_event({"type": "radio_error", "data": {"message": "x"}})
    client.poll_events()
    assert [e["type"] for e in client.list_recent_events()] == ["radio_error"]
