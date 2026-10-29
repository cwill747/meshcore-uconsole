"""Zero-hop node discovery: request/answer matching and placeholder peers."""

from __future__ import annotations

import asyncio

from meshcore_console.core.enums import EventType
from meshcore_console.meshcore.db import open_db
from meshcore_console.meshcore.discovery import (
    DISCOVER_FILTER,
    NODE_TYPE_CHAT,
    NODE_TYPE_REPEATER,
    accept_response,
    discover,
    is_placeholder_for,
    placeholder_name,
    summarize,
    wants_node_type,
)

KEY = "13ab5c7e" + "11" * 28
OTHER = "77cc0011" + "22" * 28
SELF = "46a5a5ad" + "33" * 28


def answer(key: str, node_type: int = NODE_TYPE_REPEATER, tag: int = 0) -> dict:
    return {
        "tag": tag,
        "node_type": node_type,
        "inbound_snr": 12.0,
        "response_snr": 11.5,
        "rssi": -30,
        "pub_key": key,
    }


class FakeControlHandler:
    def __init__(self) -> None:
        self.callbacks: dict[int, object] = {}
        self.cleared: list[int] = []

    def set_response_callback(self, tag, callback) -> None:
        self.callbacks[tag] = callback

    def clear_response_callback(self, tag) -> None:
        self.callbacks.pop(tag, None)
        self.cleared.append(tag)


def run_discover(answers: list[dict], **kwargs) -> tuple[list[dict], FakeControlHandler, list]:
    """Each answer is delivered to the callback for the request's tag, as the
    ControlHandler would when the radio hears it."""
    handler = FakeControlHandler()
    built: list[dict] = []

    def build_request(*, tag: int, filter_mask: int) -> dict:
        built.append({"tag": tag, "filter_mask": filter_mask})
        return built[-1]

    async def send(packet: dict) -> None:
        callback = handler.callbacks[packet["tag"]]
        for a in answers:
            callback({**a, "tag": packet["tag"]})

    results = asyncio.run(discover(send, handler, build_request, window=0.01, **kwargs))
    return results, handler, built


def test_filter_bits_follow_firmware() -> None:
    # firmware tests `filter & (1 << ADV_TYPE_REPEATER)`, ADV_TYPE_REPEATER = 2
    assert wants_node_type(0x04, NODE_TYPE_REPEATER)
    assert wants_node_type(DISCOVER_FILTER, NODE_TYPE_REPEATER)
    assert not wants_node_type(DISCOVER_FILTER, NODE_TYPE_CHAT)


def test_placeholder_names_round_trip() -> None:
    assert placeholder_name(NODE_TYPE_REPEATER, KEY) == "Repeater 13ab5c7e"
    assert placeholder_name(4, KEY.upper()) == "Sensor 13ab5c7e"
    assert placeholder_name(9, KEY) == "Node 13ab5c7e"
    assert is_placeholder_for("Repeater 13ab5c7e", KEY)
    assert not is_placeholder_for("Repeater 13ab5c7e", OTHER)
    assert not is_placeholder_for("Bergblick", KEY)


def test_accept_needs_full_key_and_asked_type() -> None:
    assert accept_response(answer(KEY), DISCOVER_FILTER)
    assert not accept_response(answer(KEY[:16]), DISCOVER_FILTER)  # prefix-only answer
    assert not accept_response(answer(KEY, NODE_TYPE_CHAT), DISCOVER_FILTER)


def test_discover_collects_unique_answers_and_clears_callback() -> None:
    results, handler, built = run_discover(
        [answer(KEY), answer(KEY), answer(OTHER, 4), answer(SELF), answer(KEY[:16])],
        self_pub_key=SELF,
    )
    assert [r["pub_key"] for r in results] == [KEY, OTHER]
    assert results[0] == {
        "pub_key": KEY,
        "node_type": NODE_TYPE_REPEATER,
        "snr_there": 12.0,
        "snr_here": 11.5,
        "rssi": -30,
    }
    assert built[0]["filter_mask"] == DISCOVER_FILTER
    assert handler.callbacks == {} and handler.cleared == [built[0]["tag"]]


def test_discover_clears_callback_when_send_fails() -> None:
    handler = FakeControlHandler()

    async def send(_packet) -> None:
        raise OSError("radio gone")

    try:
        asyncio.run(discover(send, handler, lambda **kw: kw, window=0.01))
    except OSError:
        pass
    assert handler.callbacks == {} and len(handler.cleared) == 1


def test_summarize() -> None:
    assert summarize([], {}).startswith("Discover: no answers")
    one = [{"pub_key": KEY, "node_type": 2, "snr_here": 11.5, "snr_there": 12.0}]
    assert summarize(one, {KEY: "Bergblick"}) == (
        "Discovered 1 node: Bergblick (SNR 11.5 here, 12.0 there)"
    )
    assert summarize(one, {}).startswith("Discovered 1 node: Repeater 13ab5c7e")


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


def discovered_event(name: str, key: str = KEY) -> dict:
    return {
        "type": EventType.MESH_NODE_DISCOVERED,
        "data": {
            "name": name,
            "peer_id": key[:16],
            "public_key": key,
            "contact_type": NODE_TYPE_REPEATER,
            "snr": 11.5,
            "rssi": -30,
            "path_hops": [],
        },
    }


_advert_clock = [1_700_000_000]


def advert_event(name: str, key: str = KEY) -> dict:
    """A verified advert, as openhop_core's AdvertHandler publishes it."""
    _advert_clock[0] += 1
    return {
        "type": EventType.MESH_NODE_DISCOVERED,
        "data": {
            "name": name,
            "public_key": key,
            "contact_type": NODE_TYPE_REPEATER,
            "lat": 0.0,
            "lon": 0.0,
            "advert_timestamp": _advert_clock[0],
            "inbound_path": bytes.fromhex("13"),
            "path_len_encoded": 1,
            "rssi": -40,
            "snr": 9.0,
        },
    }


def test_advert_renames_discovered_placeholder(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    client._process_event_for_peers(discovered_event("Repeater 13ab5c7e"))
    client._repeater_password_store.save_password("Repeater 13ab5c7e", "pw")
    client.set_favorite(KEY[:16], True)

    client._process_event_for_peers(advert_event("Bergblick"))

    peers = {p.display_name: p for p in client.list_peers()}
    assert list(peers) == ["Bergblick"]
    assert peers["Bergblick"].peer_id == KEY[:16]
    assert peers["Bergblick"].is_favorite and peers["Bergblick"].is_repeater
    assert client.get_saved_repeater_password("Bergblick") == "pw"
    assert client.get_saved_repeater_password("Repeater 13ab5c7e") is None


def test_a_renamed_node_stays_one_peer(tmp_path, monkeypatch) -> None:
    """A verified advert with a new name from a known key renames that peer in
    place, keeping its favourite and saved password."""
    client = _client(tmp_path, monkeypatch)
    client._process_event_for_peers(advert_event("Bergblick"))
    client.set_favorite(KEY[:16], True)
    client._repeater_password_store.save_password("Bergblick", "pw")

    client._process_event_for_peers(advert_event("Talblick"))

    (peer,) = client.list_peers()
    assert peer.display_name == "Talblick" and peer.is_favorite
    assert client.get_saved_repeater_password("Talblick") == "pw"


def test_another_key_cannot_take_over_a_name(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    client._process_event_for_peers(advert_event("Bergblick", KEY))
    client.set_favorite(KEY[:16], True)

    client._process_event_for_peers(advert_event("Bergblick", OTHER))  # impostor

    peers = {p.display_name: p for p in client.list_peers()}
    assert peers["Bergblick"].public_key == KEY and peers["Bergblick"].is_favorite
    assert peers["Bergblick (77cc00)"].public_key == OTHER


def test_discovery_updates_a_known_peer_by_key(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    client._process_event_for_peers(advert_event("Bergblick"))
    assert client._peer_name_for_key(KEY.upper()) == "Bergblick"
    client._process_event_for_peers(discovered_event("Bergblick"))
    (peer,) = client.list_peers()
    assert peer.display_name == "Bergblick" and peer.last_path == ["13"] and peer.snr == 11.5


def _live_client(tmp_path, monkeypatch, owner_name):
    """A client whose session answers discover with KEY and the owner query with
    ``owner_name``; marked connected so the contact book is in play."""
    from meshcore_console.meshcore.contact_book import ContactBook

    client = _client(tmp_path, monkeypatch)

    async def discover_nodes():
        answer = {"pub_key": KEY, "node_type": 2, "snr_here": 11.5, "snr_there": 12.0}
        return [{**answer, "rssi": -30}]

    async def request_owner_name(_key):
        return owner_name

    monkeypatch.setattr(client._session, "discover_nodes", discover_nodes, raising=False)
    monkeypatch.setattr(client._session, "request_owner_name", request_owner_name, raising=False)
    monkeypatch.setattr(client._session, "contact_book", ContactBook(), raising=False)
    # only our own events: the mock session's canned packets carry non-hex path hops
    monkeypatch.setattr(client._session, "drain_events", lambda max_items=50: [], raising=False)
    client._connected = True
    return client


def test_discover_asks_a_placeholder_for_its_name(tmp_path, monkeypatch) -> None:
    from meshcore_console.core.models import RepeaterLoginState

    client = _live_client(tmp_path, monkeypatch, "Bergblick")
    client._process_event_for_peers(discovered_event("Repeater 13ab5c7e"))
    placeholder = "Repeater 13ab5c7e"
    client._repeater_sessions[placeholder] = RepeaterLoginState(peer_name=placeholder)

    results = client.discover_nodes()
    client.poll_events()

    assert results[0]["name"] == "Bergblick"
    assert [p.display_name for p in client.list_peers()] == ["Bergblick"]
    assert list(client._repeater_sessions) == ["Bergblick"]
    assert client._session.contact_book.get_by_name("Bergblick").public_key == KEY


def test_discover_never_takes_another_keys_name(tmp_path, monkeypatch) -> None:
    client = _live_client(tmp_path, monkeypatch, "Bergblick")
    client._process_event_for_peers(advert_event("Bergblick", OTHER))

    results = client.discover_nodes()
    client.poll_events()

    assert results[0]["name"] == "Repeater 13ab5c7e"
    peers = {p.display_name: p.public_key for p in client.list_peers()}
    assert peers == {"Bergblick": OTHER, "Repeater 13ab5c7e": KEY}


def test_discover_keeps_the_placeholder_when_nobody_answers(tmp_path, monkeypatch) -> None:
    client = _live_client(tmp_path, monkeypatch, None)
    assert client.discover_nodes()[0]["name"] == "Repeater 13ab5c7e"
