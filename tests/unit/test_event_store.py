from meshcore_console.mock import MockMeshcoreClient
from meshcore_console.ui_gtk.state import UiEventStore


def test_event_store_pump_and_since_cursor() -> None:
    client = MockMeshcoreClient()
    store = UiEventStore(client)

    pumped = store.pump(limit=100)
    assert len(pumped) >= 1

    cursor, events = store.since(0, limit=100)
    assert cursor >= len(events)
    assert len(events) >= 1


def test_event_store_recent_limit() -> None:
    client = MockMeshcoreClient()
    store = UiEventStore(client)
    store.pump(limit=100)

    client.send_message("peer-001", "store limit")
    store.pump(limit=100)

    recent = store.recent(limit=1)
    assert len(recent) == 1
    assert recent[0].get("type") in {
        "message_sent",
        "mock_message_tx",
        "session_connected",
        "peer_seen",
        "mock_boot",
    }


def test_pump_emits_events_available_signal() -> None:
    client = MockMeshcoreClient()
    store = UiEventStore(client)

    received: list[bool] = []
    store.connect("events-available", lambda _store: received.append(True))

    store.pump(limit=100)
    assert len(received) == 1


def test_pump_does_not_emit_signal_when_empty() -> None:
    client = MockMeshcoreClient()
    store = UiEventStore(client)

    # Drain all initial events
    store.pump(limit=500)

    received: list[bool] = []
    store.connect("events-available", lambda _store: received.append(True))

    # Pump again — should have nothing to drain
    store.pump(limit=100)
    assert len(received) == 0
