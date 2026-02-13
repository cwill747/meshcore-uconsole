from meshcore_console.mock import MockMeshcoreClient


def test_mock_client_emits_events_for_send_and_advert() -> None:
    client = MockMeshcoreClient()

    initial_events = client.list_recent_events(limit=10)
    assert initial_events == []

    advert_result = client.send_advert(name="test-node")
    assert advert_result["success"] is True

    client.send_message("peer-001", "hello event flow")

    recent = client.list_recent_events(limit=10)
    assert any(event.get("type") == "advert_sent" for event in recent)
    assert any(event.get("type") == "message_sent" for event in recent)


def test_poll_events_drains_buffer_and_history_remains() -> None:
    client = MockMeshcoreClient()
    client.send_message("peer-001", "first")

    polled_first = client.poll_events(limit=50)
    assert polled_first

    polled_second = client.poll_events(limit=50)
    assert polled_second == []

    history = client.list_recent_events(limit=50)
    assert any(event.get("type") == "message_sent" for event in history)
