"""Tests for DM channel routing and channel removal."""

from meshcore_console.mock import MockMeshcoreClient


def test_dm_send_and_receive_use_same_channel() -> None:
    """Sending a DM and receiving a reply should use the same channel_id."""
    client = MockMeshcoreClient()

    # Send a DM to "Alice" (mixed case)
    msg = client.send_message("Alice", "hello")
    assert msg.channel_id == "alice", "DM channel_id should be lowercased"

    # Verify channel exists with lowercase id
    channels = {ch.channel_id: ch for ch in client.list_channels()}
    assert "alice" in channels
    assert channels["alice"].display_name == "Alice"
    assert channels["alice"].peer_name == "Alice"

    # Simulate receiving a reply — _process_message_event lowercases the channel
    # In mock mode, we directly verify via list_messages_for_channel
    messages = client.list_messages_for_channel("alice")
    assert len(messages) == 1
    assert messages[0].body == "hello"

    # Sending again to the same peer should reuse the channel
    client.send_message("Alice", "second message")
    messages = client.list_messages_for_channel("alice")
    assert len(messages) == 2


def test_dm_ensure_channel_normalizes_case() -> None:
    """ensure_channel should normalize DM channel IDs to lowercase."""
    client = MockMeshcoreClient()

    ch1 = client.ensure_channel("Alice")
    ch2 = client.ensure_channel("alice")
    ch3 = client.ensure_channel("ALICE")

    # All should resolve to the same channel
    assert ch1.channel_id == "alice"
    assert ch2.channel_id == "alice"
    assert ch3.channel_id == "alice"

    # Only one channel should exist for Alice
    dm_channels = [ch for ch in client.list_channels() if ch.channel_id == "alice"]
    assert len(dm_channels) == 1


def test_group_channel_preserves_case() -> None:
    """Group/public channels should not be lowercased."""
    client = MockMeshcoreClient()

    ch = client.ensure_channel("public")
    assert ch.channel_id == "public"

    ch2 = client.ensure_channel("#announcements")
    assert ch2.channel_id == "#announcements"


def test_remove_channel() -> None:
    """Removing a channel should delete it and its messages."""
    client = MockMeshcoreClient()

    client.send_message("Alice", "hello")
    assert any(ch.channel_id == "alice" for ch in client.list_channels())
    assert len(client.list_messages_for_channel("alice")) == 1

    result = client.remove_channel("alice")
    assert result is True
    assert not any(ch.channel_id == "alice" for ch in client.list_channels())
    assert len(client.list_messages_for_channel("alice")) == 0


def test_remove_public_channel_blocked() -> None:
    """The #public channel cannot be removed."""
    client = MockMeshcoreClient()

    result = client.remove_channel("public")
    assert result is False
    assert any(ch.channel_id == "public" for ch in client.list_channels())
