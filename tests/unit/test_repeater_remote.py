"""Remote repeater management: binary requests and their replies."""

from __future__ import annotations

import asyncio
import struct

from openhop_core.node.node import MeshNode
from openhop_core.protocol import CryptoUtils, Identity, Packet
from openhop_core.protocol.constants import PAYLOAD_TYPE_RESPONSE
from openhop_core.protocol.identity import LocalIdentity
from openhop_core.protocol.packet_builder import PacketBuilder

from meshcore_console.meshcore.config import runtime_config_from_settings
from meshcore_console.meshcore.contact_book import Contact
from meshcore_console.meshcore.db import open_db
from meshcore_console.meshcore.session import OpenHopCoreSession
from meshcore_console.meshcore.settings import MeshcoreSettings


class FakeRadio:
    def set_rx_callback(self, callback) -> None:
        self.callback = callback

    async def send(self, raw: bytes) -> dict:
        return {"ok": True}

    def get_last_rssi(self) -> int:
        return -50

    def get_last_snr(self) -> float:
        return 5.0


def neighbours_page(total: int, entries: list[tuple[bytes, int, int]]) -> bytes:
    """Firmware layout: total(2) + count(2) + [prefix(6) + secs_ago(4) + snr*4(1)]..."""
    body = b"".join(
        p + struct.pack("<I", secs) + struct.pack("<b", snr4) for p, secs, snr4 in entries
    )
    return struct.pack("<HH", total, len(entries)) + body


def test_session_gets_the_whole_reply_through_openhops_handler(monkeypatch) -> None:
    """A full neighbours page is 125 bytes: through openhop_core's parsed path
    it would be taken for a status reply and cut to 56."""
    session = OpenHopCoreSession(runtime_config_from_settings(MeshcoreSettings()))
    me, repeater = LocalIdentity(), LocalIdentity()
    node = MeshNode(FakeRadio(), me, config={"node": {"name": "me"}}, contacts=session.contact_book)
    session._node, session._identity = node, me
    node.dispatcher.protocol_response_handler.set_binary_response_callback(
        session._on_binary_response
    )
    session.contact_book.add_contact(
        Contact(
            name="Hilltop", public_key=repeater.get_public_key().hex(), out_path=[], out_path_len=0
        )
    )
    page = neighbours_page(11, [(bytes([i]) * 6, 30 * i, 4 * i) for i in range(11)])
    handler = node.dispatcher._handler_instances[PAYLOAD_TYPE_RESPONSE]
    secret = Identity(me.get_public_key()).calc_shared_secret(repeater.get_private_key())
    seen: list[int] = []

    async def send_packet(pkt, wait_for_ack=False, **_kw) -> bool:
        # what the repeater does: decrypt, reflect the timestamp as the tag, reply
        plain = CryptoUtils.mac_then_decrypt(secret[:16], secret, bytes(pkt.payload[2:]))
        seen.append(plain[4])
        reply = PacketBuilder.create_datagram(
            PAYLOAD_TYPE_RESPONSE, Identity(me.get_public_key()), repeater, secret, plain[:4] + page
        )
        received = Packet()
        received.read_from(reply.write_to())
        asyncio.get_running_loop().create_task(handler(received))
        return True

    monkeypatch.setattr(node.dispatcher, "send_packet", send_packet)

    data = asyncio.run(session.repeater_request("Hilltop", 0x06, b"\x00\x0b\x00\x00\x00\x06"))

    assert seen == [0x06] and session._raw_waiters == {}
    # whole page, plus the zero padding of the last AES block (as from firmware)
    assert data[: len(page)] == page and not data[len(page) :].strip(b"\x00")


def test_request_timestamps_strictly_increase() -> None:
    session = OpenHopCoreSession(runtime_config_from_settings(MeshcoreSettings()))
    stamps = [session.next_request_timestamp() for _ in range(5)]
    assert stamps == sorted(set(stamps))


def _client(tmp_path, monkeypatch, replies: dict[int, list[bytes]]):
    from meshcore_console.meshcore import client as client_mod
    from meshcore_console.meshcore.client import MeshcoreClient
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
    requests: list[tuple[int, bytes]] = []

    async def repeater_request(peer_name, req_type, data=b"", timeout=10.0):
        requests.append((req_type, data))
        return replies[req_type].pop(0)

    monkeypatch.setattr(client._session, "repeater_request", repeater_request, raising=False)
    return client, requests


def test_neighbours_are_fetched_page_by_page_and_named(tmp_path, monkeypatch) -> None:
    from meshcore_console.core.enums import EventType

    first = [(bytes([0xA0 + i]) * 6, 10 * i, 8) for i in range(11)]
    pages = [neighbours_page(13, first), neighbours_page(13, [(b"\x13" * 6, 600, -12)] * 2)]
    client, requests = _client(tmp_path, monkeypatch, {0x06: pages})
    client._process_event_for_peers(
        {
            "type": EventType.MESH_NODE_DISCOVERED,
            "data": {"name": "Hilltop", "public_key": "13" * 32, "advert_timestamp": 1},
        }
    )

    neighbours = client.get_repeater_neighbours("Bergblick")

    assert len(neighbours) == 13
    # request data: version, count, offset (u16), order, key prefix length, random
    assert [r[1][2:4] for r in requests] == [b"\x00\x00", b"\x0b\x00"]  # offsets 0, 11
    assert neighbours[-1] == {"pubkey": "13" * 6, "secs_ago": 600, "snr": -3.0, "name": "Hilltop"}
    assert neighbours[0]["name"] is None


def test_status_owner_and_access_list_decode(tmp_path, monkeypatch) -> None:
    status = struct.pack(
        "<HHhhIIIIIIIIHhHHI",
        4012,
        0,
        -112,
        -71,
        18234,
        6110,
        1834,
        277200,
        1,
        2,
        3,
        4,
        0,
        29,
        1,
        2,
        9,
    )
    acl = bytes.fromhex("46a5a5adaf41") + b"\x03" + bytes.fromhex("a1b2c3d4e5f6") + b"\x00"
    replies = {
        0x01: [status],
        0x07: [b"v1.16.0-07a3ca9\nDF9ES_Home\nErik|Leipzig\x00\x00\x00"],  # + AES padding
        0x05: [acl],
    }
    client, _requests = _client(tmp_path, monkeypatch, replies)

    s = client.get_repeater_status("DF9ES_Home")
    assert (s["bat"], s["noise_floor"], s["uptime"], s["last_snr"]) == (4012, -112, 277200, 7.25)
    assert client.get_repeater_owner_info("DF9ES_Home") == {
        "version": "v1.16.0-07a3ca9",
        "name": "DF9ES_Home",
        "owner_info": "Erik|Leipzig",
    }
    roles = [(e["key"], e["role"]) for e in client.get_repeater_access_list("DF9ES_Home")]
    assert roles == [("46a5a5adaf41", "admin"), ("a1b2c3d4e5f6", "guest")]
