"""DM threads and group channels can't be confused for each other."""

from __future__ import annotations

import sqlite3

from meshcore_console.core.enums import EventType
from meshcore_console.meshcore import db as db_mod
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
    from meshcore_console.meshcore.contact_book import ContactBook

    monkeypatch.setattr(client._session, "contact_book", ContactBook(), raising=False)
    client._connected = True
    sent: list[tuple[str, str]] = []

    async def send_text(*, peer_name, message):
        sent.append(("dm", peer_name))
        return {"success": True}

    async def send_group_text(*, channel_name, message, region_scope=None):
        sent.append(("group", channel_name))
        return {"success": True}

    monkeypatch.setattr(client._session, "send_text", send_text, raising=False)
    monkeypatch.setattr(client._session, "send_group_text", send_group_text, raising=False)
    return client, sent


def dm_from(name: str, text: str) -> dict:
    return {
        "type": EventType.MESH_MESSAGE_NEW,
        "data": {"sender_name": name, "message_text": text, "message_id": text},
    }


def test_dm_from_a_name_that_is_also_a_channel_stays_private(tmp_path, monkeypatch) -> None:
    client, sent = _client(tmp_path, monkeypatch)
    client.ensure_channel("test", "#test")  # group #test
    client._process_message_event(dm_from("Test", "psst")["data"], EventType.MESH_MESSAGE_NEW)

    assert [m.body for m in client.list_messages_for_channel("test")] == []
    assert [m.body for m in client.list_messages_for_channel("dm:test")] == ["psst"]

    client.send_message("dm:test", "reply")  # replying in the DM thread
    assert sent == [("dm", "Test")]


def test_messaging_a_node_named_public_is_a_dm(tmp_path, monkeypatch) -> None:
    client, sent = _client(tmp_path, monkeypatch)
    thread = client.ensure_dm_channel("Public")
    client.send_message(thread.channel_id, "hi")
    assert thread.channel_id == "dm:public" and sent == [("dm", "Public")]
    assert client._channels["public"].kind == "group"


def test_migration_moves_existing_dm_threads(tmp_path) -> None:
    path = tmp_path / "old.db"
    conn = sqlite3.connect(str(path))
    for version in db_mod.MIGRATIONS[:8]:
        for stmt in version:
            conn.execute(stmt)
    db_mod._backfill_channel_secrets(conn)
    db_mod._add_region_scope_column(conn)
    conn.execute("UPDATE schema_version SET version = 8")
    conn.execute(
        "INSERT INTO channels (channel_id, display_name, unread_count, peer_name, kind) "
        "VALUES ('bob', 'Bob', 0, 'Bob', 'dm')"
    )
    conn.execute(
        "INSERT INTO messages (message_id, sender_id, body, channel_id, created_at) "
        "VALUES ('m1', 'Bob', 'hi', 'bob', '2026-10-27T20:00:00+00:00')"
    )
    conn.commit()
    conn.close()

    conn = open_db(str(path))
    assert conn.execute("SELECT channel_id FROM channels WHERE kind = 'dm'").fetchall() == [
        ("dm:bob",)
    ]
    assert conn.execute("SELECT channel_id FROM messages").fetchall() == [("dm:bob",)]
    conn.close()


def test_send_reports_delivery_and_refuses_untransmitted_posts(tmp_path, monkeypatch) -> None:
    import pytest

    client, _sent = _client(tmp_path, monkeypatch)

    async def no_ack(*, peer_name, message):
        return {"success": False, "attempts": 3}

    async def not_transmitted(*, channel_name, message, region_scope=None):
        return {"success": False}

    monkeypatch.setattr(client._session, "send_text", no_ack, raising=False)
    monkeypatch.setattr(client._session, "send_group_text", not_transmitted, raising=False)

    message = client.send_message(client.ensure_dm_channel("Bob").channel_id, "hi")
    assert message.delivered is False

    with pytest.raises(RuntimeError):
        client.send_message("public", "hello all")
    assert [m.body for m in client.list_messages_for_channel("public")] == []


def test_send_status_text() -> None:
    from meshcore_console.ui_gtk.views.messages import send_status_text

    assert send_status_text("Bob", True) == "Delivered to Bob"
    assert send_status_text("Bob", False) == "Sent to Bob, but no acknowledgement came back"
    assert send_status_text("#ops", None) == "Sent to #ops"


def test_identical_dms_minutes_apart_both_show_retries_do_not(tmp_path, monkeypatch) -> None:
    client, _sent = _client(tmp_path, monkeypatch)
    bob = "bb" * 32

    def dm(ts: int) -> dict:
        return {
            "sender_name": "Bob",
            "contact_pubkey": bob,
            "message_text": "ok",
            "timestamp": ts,
            "message_id": "rx_1761595000_5ab3c1f2",  # openhop's 1000 s bucket id
        }

    client._process_message_event(dm(1_761_595_000), EventType.MESH_MESSAGE_NEW)
    client._process_message_event(dm(1_761_595_000), EventType.MESH_MESSAGE_NEW)  # retry
    client._process_message_event(dm(1_761_595_600), EventType.MESH_MESSAGE_NEW)  # 10 min later

    assert [m.body for m in client.list_messages_for_channel("dm:bob")] == ["ok", "ok"]


def test_messages_pair_with_their_own_packet(tmp_path, monkeypatch) -> None:
    client, _sent = _client(tmp_path, monkeypatch)
    monkeypatch.setattr(client._packet_store, "update_by_hash", lambda *a, **k: None)

    def packet(hash16: str, snr: float) -> dict:
        return {
            "type": EventType.PACKET,
            "data": {"payload_type_name": "GRP_TXT", "packet_hash": hash16, "snr": snr},
        }

    echo = packet("AAAAAAAAAAAAAAAA", -12.0)  # our own post, echoed by a repeater
    real = packet("BBBBBBBBBBBBBBBB", 7.0)
    decoded = {
        "type": EventType.MESH_CHANNEL_MESSAGE_NEW,
        "data": {
            "sender_name": "Bob",
            "channel_name": "public",
            "message_text": "hi",
            "message_id": "BBBBBBBBBBBBBBBB",
            "packet_hash": "BBBBBBBBBBBBBBBB" + "C" * 48,
        },
    }
    client._enrich_sender_names([echo, real, decoded])

    assert decoded["data"]["snr"] == 7.0  # not the echo's -12
    assert real["data"]["payload_text"] == "hi" and "payload_text" not in echo["data"]
    assert list(client._unenriched_grp) == [echo["data"]]


def test_late_cli_reply_goes_to_admin_not_a_dm_thread(tmp_path, monkeypatch) -> None:
    client, _sent = _client(tmp_path, monkeypatch)
    reply = {"sender_name": "Bergblick", "message_text": "v1.18.0", "txt_type": 1}
    client._process_message_event(reply, EventType.MESH_MESSAGE_NEW)

    assert client.list_messages_for_channel("dm:bergblick") == []
    late = [e for e in client._event_buffer if e["type"] == EventType.REPEATER_COMMAND_RESPONSE]
    assert late[0]["data"] == {"peer_name": "Bergblick", "late": True, "response_text": "v1.18.0"}


def test_room_post_shows_its_author(tmp_path, monkeypatch) -> None:
    client, _sent = _client(tmp_path, monkeypatch)
    client._process_event_for_peers(
        {
            "type": EventType.MESH_NODE_DISCOVERED,
            "data": {"name": "Alice", "public_key": "a1b2c3d4" + "00" * 28, "advert_timestamp": 1},
        }
    )
    post = {
        "sender_name": "Leipzig Room",
        "message_text": "hello room",
        "txt_type": 2,
        "sender_prefix": "a1b2c3d4",
    }
    client._process_message_event(post, EventType.MESH_MESSAGE_NEW)
    unknown = dict(post, message_text="who?", sender_prefix="99887766")
    client._process_message_event(unknown, EventType.MESH_MESSAGE_NEW)

    thread = client.list_messages_for_channel("dm:leipzig room")
    assert [(m.sender_id, m.body) for m in thread] == [
        ("Alice", "hello room"),
        ("99887766", "who?"),
    ]


def test_a_busy_channel_does_not_push_out_dms(tmp_path, monkeypatch) -> None:
    from meshcore_console.meshcore import client as client_mod
    from meshcore_console.meshcore import state_store

    monkeypatch.setattr(state_store, "MAX_MESSAGES", 5)
    monkeypatch.setattr(client_mod, "MAX_MESSAGES", 5)
    client, _sent = _client(tmp_path, monkeypatch)
    dm = {"sender_name": "Bob", "message_text": "private", "message_id": "dm1"}
    client._process_message_event(dm, EventType.MESH_MESSAGE_NEW)
    for i in range(20):
        post = {"sender_name": "X", "channel_name": "public", "message_text": f"p{i}"}
        client._process_message_event(dict(post, message_id=f"p{i}"), "")

    assert [m.body for m in client.list_messages_for_channel("dm:bob")] == ["private"]
    assert len(client.list_messages_for_channel("public", limit=100)) == 5
    rows = dict(client._db.execute("SELECT channel_id, COUNT(*) FROM messages GROUP BY channel_id"))
    assert rows == {"dm:bob": 1, "public": 5}
