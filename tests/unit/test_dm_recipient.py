"""A DM thread bound to a key reaches that key's node, or nobody.

Names move between nodes (renames, and a name freed by an evicted peer is
claimed by the next node advertising it).  A key-bound thread is therefore
sent to by key, resolved when the packet is built; name-only threads and the
CLI still send by name."""

from __future__ import annotations

import asyncio

import pytest
from openhop_core.protocol.packet_builder import PacketBuilder

from meshcore_console.core.enums import EventType
from meshcore_console.core.models import dm_thread_key
from meshcore_console.meshcore import operations
from meshcore_console.meshcore.contact_book import Contact, ContactBook
from meshcore_console.meshcore.db import open_db

KEY_A = "aa" * 32
KEY_B = "bb" * 32


class AckDispatcher:
    def __init__(self) -> None:
        self.sent: list[object] = []

    async def send_packet(self, packet, wait_for_ack=False, expected_crc=None, **_kw) -> bool:
        self.sent.append(packet)
        return True


class FakeNode:
    def __init__(self, contacts: ContactBook) -> None:
        self.identity = object()
        self.contacts = contacts
        self.dispatcher = AckDispatcher()


@pytest.fixture
def built_for(monkeypatch) -> list[str]:
    """The public key of each contact a text packet is built for."""
    keys: list[str] = []

    def create_text_message(*, contact, **_kw):
        keys.append(contact.public_key)
        return object(), len(keys)

    monkeypatch.setattr(PacketBuilder, "create_text_message", staticmethod(create_text_message))
    return keys


def _send(node: FakeNode, peer_name: str, public_key: str | None = None) -> dict:
    return asyncio.run(
        operations.send_text(node=node, peer_name=peer_name, message="hi", public_key=public_key)
    )


# --- operations: resolved by key where the packet is built ---------------------


def test_keyed_send_goes_to_the_key_not_the_name(built_for) -> None:
    book = ContactBook()
    book.add_contact(Contact(name="Robert", public_key=KEY_A))  # A, renamed
    book.add_contact(Contact(name="Alice", public_key=KEY_B))  # B took A's old name
    _send(FakeNode(book), "Alice", public_key=KEY_A)
    assert built_for == [KEY_A]


def test_keyed_send_fails_closed_when_the_key_is_gone(built_for) -> None:
    book = ContactBook()
    book.add_contact(Contact(name="Alice", public_key=KEY_B))  # A evicted, B claimed the name
    node = FakeNode(book)
    with pytest.raises(RuntimeError, match="no longer known"):
        _send(node, "Alice", public_key=KEY_A)
    assert built_for == [] and node.dispatcher.sent == []  # nothing on air


def test_name_only_send_is_unchanged(built_for) -> None:
    book = ContactBook()
    book.add_contact(Contact(name="Alice", public_key=KEY_B))
    _send(FakeNode(book), "Alice")
    assert built_for == [KEY_B]
    with pytest.raises(RuntimeError, match="not found"):
        _send(FakeNode(book), "Nobody")


def test_get_by_key_ignores_hex_case() -> None:
    book = ContactBook()
    book.add_contact(Contact(name="Alice", public_key=KEY_A.upper()))
    assert book.get_by_key(KEY_A) is not None
    assert book.get_by_key(bytes.fromhex(KEY_A)) is not None
    assert book.get_by_key(KEY_B) is None


def test_dm_thread_key() -> None:
    assert dm_thread_key("dm:" + KEY_A) == KEY_A
    assert dm_thread_key("dm:" + KEY_A.upper()) == KEY_A
    assert dm_thread_key("dm:alice") is None  # name-only thread
    assert dm_thread_key("dm:" + "a" * 63) is None
    assert dm_thread_key("dm:" + "g" * 64) is None
    assert dm_thread_key(KEY_A) is None  # not a DM thread id


# --- the client: a real thread, contact book and send path ---------------------


def _advert(name: str, key: str, ts: int) -> dict:
    return {
        "type": EventType.MESH_NODE_DISCOVERED,
        "data": {"name": name, "public_key": key, "contact_type": 1, "advert_timestamp": ts},
    }


def _client(tmp_path, monkeypatch):
    """A connected client whose session sends through the real operations
    code, against the client's own contact book."""
    from meshcore_console.meshcore import client as client_mod
    from meshcore_console.meshcore.client import MeshcoreClient
    from meshcore_console.meshcore.config import runtime_config_from_settings
    from meshcore_console.meshcore.session import OpenHopCoreSession
    from meshcore_console.meshcore.settings import MeshcoreSettings

    db_conn = open_db(str(tmp_path / "test.db"))
    monkeypatch.setattr(client_mod, "open_db", lambda *a, **k: db_conn)
    session = OpenHopCoreSession(runtime_config_from_settings(MeshcoreSettings()))
    client = MeshcoreClient(session=session, require_openhop=False)
    session._node = FakeNode(session.contact_book)
    client._connected = True
    return client, session.contact_book


def _evict(client, name: str) -> None:
    """What _make_room_for_peer does to a peer (here regardless of its
    guards: a thread can outlive its peer, e.g. one restored without it)."""
    peer = client._peers.pop(name)
    client._peer_store.remove(peer.peer_id)
    client._advert_timestamps.pop(peer.public_key, None)
    client._session.contact_book.remove_key(peer.public_key)


def test_keyed_thread_never_reaches_the_node_that_claimed_its_name(
    tmp_path, monkeypatch, built_for
) -> None:
    client, book = _client(tmp_path, monkeypatch)
    client._process_event_for_peers(_advert("Alice", KEY_A, 1))
    thread = client.ensure_dm_channel("Alice", public_key=KEY_A)
    assert thread.channel_id == "dm:" + KEY_A

    client.send_message(thread.channel_id, "to A")
    assert built_for == [KEY_A]

    _evict(client, "Alice")
    client._process_event_for_peers(_advert("Alice", KEY_B, 2))  # B claims the name
    assert book.get_by_name("Alice").public_key == KEY_B
    assert client._channels[thread.channel_id].peer_name == "Alice"  # the thread's label

    with pytest.raises(RuntimeError, match="no longer known"):
        client.send_message(thread.channel_id, "still for A")
    assert built_for == [KEY_A]  # nothing was built for B
    bodies = [m.body for m in client.list_messages_for_channel(thread.channel_id)]
    assert bodies == ["to A"]  # the failed send is not recorded as sent

    # A comes back under another name: its thread reaches it again
    client._process_event_for_peers(_advert("Alice A", KEY_A, 3))
    client.send_message(thread.channel_id, "A again")
    assert built_for == [KEY_A, KEY_A]


def test_name_only_thread_still_sends_by_name(tmp_path, monkeypatch, built_for) -> None:
    client, book = _client(tmp_path, monkeypatch)
    book.add_contact(Contact(name="Carol", public_key=KEY_B))  # not a peer: no keyed thread
    thread = client.ensure_dm_channel("Carol")
    assert thread.channel_id == "dm:carol"
    client.send_message(thread.channel_id, "hi")
    assert built_for == [KEY_B]


def test_unknown_keyed_thread_id_stays_bound_to_its_key(tmp_path, monkeypatch) -> None:
    """ensure_channel with a dm:<key> id not opened yet: the thread is that
    key's, whatever name comes with it."""
    client, _book = _client(tmp_path, monkeypatch)
    client._process_event_for_peers(_advert("Robert", KEY_A, 1))
    client._process_event_for_peers(_advert("Alice", KEY_B, 2))

    thread = client.ensure_channel("dm:" + KEY_A, "Alice")  # a stale label
    assert thread.channel_id == "dm:" + KEY_A
    assert thread.peer_name == "Robert"  # the key's current name

    unknown = "cc" * 32
    thread = client.ensure_channel("dm:" + unknown, "Dora")
    assert thread.channel_id == "dm:" + unknown and thread.kind == "dm"
