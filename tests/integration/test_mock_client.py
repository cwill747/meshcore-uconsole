from meshcore_console.mock import MockMeshcoreClient


def test_mock_client_send_message_round_trip() -> None:
    client = MockMeshcoreClient()
    message = client.send_message("peer-001", "test")

    latest = client.list_messages(limit=1)
    assert latest[0].message_id == message.message_id
    assert latest[0].body == "test"
