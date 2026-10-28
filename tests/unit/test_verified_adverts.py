"""Peers come only from verified adverts, and replays don't roll them back."""

from __future__ import annotations

from meshcore_console.core.enums import EventType
from meshcore_console.meshcore.client import hops_from_path
from meshcore_console.meshcore.db import open_db

KEY = "13ab5c7e" + "11" * 28


def _client(tmp_path, monkeypatch):
    from meshcore_console.meshcore import client as client_mod
    from meshcore_console.meshcore.client import MeshcoreClient
    from meshcore_console.meshcore.config import runtime_config_from_settings
    from meshcore_console.meshcore.settings import MeshcoreSettings
    from meshcore_console.mock import MockOpenHopCoreSession

    db_conn = open_db(str(tmp_path / "test.db"))
    monkeypatch.setattr(client_mod, "open_db", lambda *a, **k: db_conn)
    return MeshcoreClient(
        session=MockOpenHopCoreSession(runtime_config_from_settings(MeshcoreSettings())),
        require_openhop=False,
    )


def verified(name: str, ts: int, lat: float = 0.0) -> dict:
    return {
        "type": EventType.MESH_NODE_DISCOVERED,
        "data": {
            "name": name,
            "public_key": KEY,
            "contact_type": 2,
            "lat": lat,
            "lon": 13.4 if lat else 0.0,
            "advert_timestamp": ts,
            "inbound_path": bytes.fromhex("13"),
            "path_len_encoded": 1,
            "rssi": -40,
            "snr": 9.0,
        },
    }


def test_unverified_packet_adverts_make_no_peers(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    forged = {
        "type": EventType.PACKET,
        "data": {
            "payload_type_name": "ADVERT",
            "sender_name": "Alice",
            "sender_pubkey": KEY,
            "sender_id": KEY[:16],
        },
    }
    client._process_event_for_peers(forged)
    assert client.list_peers() == []


def test_verified_advert_makes_a_peer_keyed_like_packet_events(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    client._process_event_for_peers(verified("Bergblick", 100))
    (peer,) = client.list_peers()
    assert (peer.display_name, peer.peer_id, peer.public_key) == ("Bergblick", KEY[:16], KEY)
    assert peer.is_repeater and peer.last_path == ["13"]


def test_replayed_advert_is_ignored(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    client._process_event_for_peers(verified("Bergblick", 200, lat=52.5))
    client._process_event_for_peers(verified("Bergblick", 100, lat=48.1))  # older, replayed
    client._process_event_for_peers(verified("Bergblick", 200, lat=48.1))  # same, replayed
    (peer,) = client.list_peers()
    assert peer.latitude == 52.5


def test_hops_from_path() -> None:
    assert hops_from_path(bytes.fromhex("13a7"), 2) == ["13", "a7"]
    assert hops_from_path(bytes.fromhex("13a7c3d4"), (1 << 6) | 2) == ["13a7", "c3d4"]
    assert hops_from_path(b"", 0) == [] and hops_from_path(None, None) == []


def test_adverts_do_not_set_routes_but_discovery_does(tmp_path, monkeypatch) -> None:
    from meshcore_console.meshcore.contact_book import ContactBook

    client = _client(tmp_path, monkeypatch)
    monkeypatch.setattr(client._session, "contact_book", ContactBook(), raising=False)
    client._connected = True

    client._process_event_for_peers(verified("Bergblick", 100))  # heard via hop 13
    contact = client._session.contact_book.get_by_name("Bergblick")
    assert contact.out_path_len == -1  # unknown: DMs flood, openhop learns the route

    client._process_event_for_peers(
        {
            "type": EventType.MESH_NODE_DISCOVERED,
            "data": {"name": "Bergblick", "public_key": KEY, "path_hops": [], "direct_route": True},
        }
    )
    assert contact.out_path_len == 0  # it answered our zero-hop discover


def test_hop_count_from_the_encoded_path_length() -> None:
    from meshcore_console.meshcore.client import hop_count

    assert hop_count(0xFF) == 0  # direct DM: "unknown", was shown as 255 hops
    assert hop_count(3) == 3
    assert hop_count((1 << 6) | 2) == 2  # 2-byte hashes, 2 hops
    assert hop_count(None) == 0 and hop_count(0) == 0


def test_peer_list_is_bounded_and_keeps_what_matters(tmp_path, monkeypatch) -> None:
    from meshcore_console.meshcore import client as client_mod

    monkeypatch.setattr(client_mod, "MAX_PEERS", 3)
    client = _client(tmp_path, monkeypatch)

    def advert(name: str, key: str, ts: int) -> dict:
        event = verified(name, ts)
        event["data"]["public_key"] = key
        return event

    keys = {name: (name[0] * 2) * 32 for name in ("aa", "bb", "cc", "dd", "ee")}
    client._process_event_for_peers(advert("Alpha", keys["aa"], 1))
    client._process_event_for_peers(advert("Bravo", keys["bb"], 2))
    client._process_event_for_peers(advert("Charlie", keys["cc"], 3))
    client.set_favorite(keys["aa"][:16], True)  # oldest, but a favourite
    client.ensure_dm_channel("Bravo")  # has a DM thread

    client._process_event_for_peers(advert("Delta", keys["dd"], 4))
    assert sorted(p.display_name for p in client.list_peers()) == ["Alpha", "Bravo", "Delta"]
    assert client._db.execute("SELECT COUNT(*) FROM peers").fetchone()[0] == 3


def test_routes_are_not_restored_from_stored_advert_paths(tmp_path, monkeypatch) -> None:
    from meshcore_console.meshcore.contact_book import ContactBook

    client = _client(tmp_path, monkeypatch)
    client._process_event_for_peers(verified("Bergblick", 100))  # stored last_path ["13"]
    book = ContactBook()
    monkeypatch.setattr(client._session, "contact_book", book, raising=False)

    client._seed_contact_book()  # what connect() does

    assert book.get_by_name("Bergblick").out_path_len == -1
