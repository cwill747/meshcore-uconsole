"""Which inbound requests the node answers, checked on a real openhop MeshNode."""

from __future__ import annotations

from openhop_core.node.node import MeshNode
from openhop_core.protocol.constants import PAYLOAD_TYPE_ANON_REQ, PAYLOAD_TYPE_REQ
from openhop_core.protocol.identity import LocalIdentity

from meshcore_console.meshcore.config import runtime_config_from_settings
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


def session_on_real_node() -> tuple[OpenHopCoreSession, MeshNode]:
    session = OpenHopCoreSession(runtime_config_from_settings(MeshcoreSettings()))
    identity = LocalIdentity()
    node = MeshNode(
        FakeRadio(),
        identity,
        config={"node": {"name": "me"}},
        contacts=session.contact_book,
        channel_db=None,
    )
    session._node, session._identity = node, identity
    return session, node


def test_anonymous_requests_stay_with_openhop() -> None:
    """In an ANON_REQ, 0x03 is ANON_REQ_TYPE_BASIC (clock), not telemetry; the
    app used to replace openhop's rate-limited handler and answer it with
    battery and position to any stranger."""
    session, node = session_on_real_node()
    anon_before = node.dispatcher._handler_instances.get(PAYLOAD_TYPE_ANON_REQ)

    session._register_req_handler()

    assert node.dispatcher._handler_instances.get(PAYLOAD_TYPE_ANON_REQ) is anon_before
    assert node.dispatcher._handler_instances.get(PAYLOAD_TYPE_REQ) is not None


def test_discovery_answers_are_filtered_and_rate_limited(monkeypatch) -> None:
    import asyncio
    import random

    from meshcore_console.meshcore.discovery import NODE_TYPE_CHAT, NODE_TYPE_REPEATER

    monkeypatch.setattr(random, "uniform", lambda _a, _b: 0.0)
    session, node = session_on_real_node()
    sent: list = []

    async def send_packet(packet, wait_for_ack=False, **_kw) -> bool:
        sent.append(packet)
        return True

    monkeypatch.setattr(node.dispatcher, "send_packet", send_packet)
    session._register_discovery_handler()
    on_request = node.dispatcher.control_handler._request_callbacks[0]

    async def burst() -> None:
        on_request({"tag": 1, "filter": 1 << NODE_TYPE_REPEATER, "snr": 5.0})  # not for us
        for tag in range(6):
            on_request({"tag": tag, "filter": 1 << NODE_TYPE_CHAT, "snr": 5.0})
        await asyncio.sleep(0.05)

    asyncio.run(burst())
    assert len(sent) == 4  # firmware's 4 answers per 2 minutes
    assert not session._background_tasks


def test_answer_limiter_window() -> None:
    from meshcore_console.meshcore.discovery import AnswerLimiter

    now = [0.0]
    limiter = AnswerLimiter(limit=2, window_s=10.0, clock=lambda: now[0])
    assert [limiter.allow() for _ in range(3)] == [True, True, False]
    now[0] = 10.5
    assert limiter.allow() is True


def _telemetry_request_from(sender, us_key: bytes):
    from openhop_core.protocol.packet_builder import PacketBuilder

    from meshcore_console.meshcore.contact_book import Contact

    us = Contact(name="us", public_key=us_key.hex(), out_path=[], out_path_len=0)
    pkt, _ts = PacketBuilder.create_protocol_request(us, sender, protocol_code=0x03, data=b"\x00")
    return pkt


def _answers(session, node, monkeypatch) -> list:
    sent: list = []

    async def send_packet(packet, wait_for_ack=False, **_kw) -> bool:
        sent.append(packet)
        return True

    monkeypatch.setattr(node.dispatcher, "send_packet", send_packet)
    session.set_telemetry_data_fn(
        lambda: {"allow": True, "favorites_only": False, "lat": None, "lon": None, "voltage": 4.1}
    )
    session._register_req_handler()
    return sent


def test_a_broken_contact_key_does_not_block_others_with_the_same_hash(monkeypatch) -> None:
    import asyncio

    from meshcore_console.meshcore.contact_book import Contact

    session, node = session_on_real_node()
    good = LocalIdentity()
    good_key = good.get_public_key()
    broken = Contact(name="broken", public_key=(good_key[:1] + b"\xff" * 31).hex())
    session.contact_book.add_contact(broken)
    session.contact_book.add_contact(Contact(name="good", public_key=good_key.hex()))
    sent = _answers(session, node, monkeypatch)
    handler = node.dispatcher._handler_instances[PAYLOAD_TYPE_REQ]

    asyncio.run(handler(_telemetry_request_from(good, session._identity.get_public_key())))

    assert len(sent) == 1  # answered despite the broken contact tried first


def test_a_replayed_request_is_not_answered_again(monkeypatch) -> None:
    import asyncio

    from meshcore_console.meshcore.contact_book import Contact

    session, node = session_on_real_node()
    sender = LocalIdentity()
    session.contact_book.add_contact(
        Contact(name="sender", public_key=sender.get_public_key().hex())
    )
    sent = _answers(session, node, monkeypatch)
    handler = node.dispatcher._handler_instances[PAYLOAD_TYPE_REQ]
    request = _telemetry_request_from(sender, session._identity.get_public_key())

    asyncio.run(handler(request))
    asyncio.run(handler(request))  # captured and replayed

    assert len(sent) == 1
