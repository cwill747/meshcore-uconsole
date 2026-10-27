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
