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
