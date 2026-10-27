"""Node discovery: MeshCore's zero-hop CONTROL discover request/response.

A discover request (CTL_TYPE_NODE_DISCOVER_REQ) carries a node-type filter and
a random tag.  Firmware repeaters and sensors that match the filter answer
with a zero-hop DISCOVER_RESP: their node type, the SNR they heard the request
at, the tag, and their full public key.  Repeaters rate-limit this to 4
answers per 2 minutes and delay each answer randomly, so answers trickle in
over a few seconds.

The answer carries no name.  A discovered node therefore enters the peer list
under a placeholder name (``Repeater 13ab5c7e``) until its next advert
replaces it with the real one (see ``is_placeholder_for``).
"""

from __future__ import annotations

import asyncio
import secrets
import time
from typing import Any, Callable

# ADV_TYPE_* node types, as carried in the low nibble of a DISCOVER_RESP.
NODE_TYPE_LABELS: dict[int, str] = {1: "Chat", 2: "Repeater", 3: "Room", 4: "Sensor"}
NODE_TYPE_CHAT = 1
NODE_TYPE_REPEATER = 2

# Ask repeaters, room servers and sensors (bit = 1 << node type).  Chat nodes
# are left out: firmware companions don't answer, and people's handhelds
# aren't infrastructure worth listing.
DISCOVER_FILTER = (1 << 2) | (1 << 3) | (1 << 4)

# Long enough for the firmware's widened random answer delay at SF8/62.5 kHz.
DISCOVER_WINDOW_S = 12.0

PUB_KEY_HEX_LEN = 64


class AnswerLimiter:
    """At most ``limit`` discovery answers per ``window_s`` seconds.

    Mirrors the firmware repeater's ``discover_limiter(4, 120)``: anyone in
    radio range can send discover requests, and each answer costs airtime
    (EU868: 10 % duty cycle) and battery.
    """

    def __init__(
        self, limit: int = 4, window_s: float = 120.0, clock: Callable[[], float] | None = None
    ) -> None:
        self._limit = limit
        self._window_s = window_s
        self._clock = clock or time.monotonic
        self._sent: list[float] = []

    def allow(self) -> bool:
        now = self._clock()
        self._sent = [t for t in self._sent if now - t < self._window_s]
        if len(self._sent) >= self._limit:
            return False
        self._sent.append(now)
        return True


def wants_node_type(filter_mask: int, node_type: int) -> bool:
    """True if a discover request with ``filter_mask`` asks for ``node_type``."""
    return bool(filter_mask & (1 << node_type))


def placeholder_name(node_type: int, pub_key_hex: str) -> str:
    """Name for a discovered node until its advert names it: ``Repeater 13ab5c7e``."""
    return f"{NODE_TYPE_LABELS.get(node_type, 'Node')} {pub_key_hex[:8].lower()}"


def is_placeholder_for(name: str, pub_key_hex: str) -> bool:
    """True if ``name`` is the placeholder discovery gave the node with this key."""
    return any(
        name == placeholder_name(node_type, pub_key_hex) for node_type in (*NODE_TYPE_LABELS, 0)
    )


def accept_response(response: dict[str, Any], filter_mask: int) -> bool:
    """Keep answers with a full public key from a node type we asked for."""
    key = str(response.get("pub_key") or "")
    node_type = int(response.get("node_type", 0))
    return len(key) == PUB_KEY_HEX_LEN and wants_node_type(filter_mask, node_type)


async def discover(
    send_packet: Callable[[Any], Any],
    control_handler: Any,
    build_request: Callable[..., Any],
    *,
    self_pub_key: str | None = None,
    filter_mask: int = DISCOVER_FILTER,
    window: float = DISCOVER_WINDOW_S,
) -> list[dict[str, Any]]:
    """Send one discover request and collect the answers for ``window`` seconds.

    ``send_packet`` is the dispatcher's async send, ``control_handler`` its
    ControlHandler (answers are matched by tag there), ``build_request`` is
    ``PacketBuilder.create_discovery_request``.  Returns one dict per node,
    first answer wins, in arrival order.
    """
    tag = secrets.randbits(32)
    found: dict[str, dict[str, Any]] = {}

    def on_response(response: dict[str, Any]) -> None:
        if not accept_response(response, filter_mask):
            return
        key = str(response["pub_key"]).lower()
        if key == (self_pub_key or "").lower() or key in found:
            return
        found[key] = {
            "pub_key": key,
            "node_type": int(response["node_type"]),
            "snr_there": response.get("inbound_snr"),
            "snr_here": response.get("response_snr"),
            "rssi": response.get("rssi"),
        }

    control_handler.set_response_callback(tag, on_response)
    try:
        await send_packet(build_request(tag=tag, filter_mask=filter_mask))
        await asyncio.sleep(window)
    finally:
        control_handler.clear_response_callback(tag)
    return list(found.values())


def decode_owner_reply(parsed: dict[str, Any]) -> tuple[str, str] | None:
    """(name, owner) from an ANON_REQ_TYPE_OWNER reply, or None.

    Firmware replies tag(4) + clock(4) + "name\nowner".  openhop_core guesses a
    type for every reply, so the bytes sit wherever its guess put them: "hex"
    for binary (the usual case: the clock bytes aren't text), "raw_bytes" after
    the tag for telemetry/stats lookalikes, "content" after the tag for text.
    """
    body: bytes | None = None
    if parsed.get("hex"):
        body = bytes.fromhex(parsed["hex"])[8:]
    elif isinstance(parsed.get("raw_bytes"), (bytes, bytearray)):
        body = bytes(parsed["raw_bytes"])[4:]
    elif isinstance(parsed.get("content"), str):
        body = parsed["content"].encode("utf-8", "replace")[4:]
    if not body:
        return None
    name, _, owner = body.rstrip(b"\0").decode("utf-8", "replace").partition("\n")
    name = name.strip()
    if not name or len(name) > 32 or not name.isprintable():
        return None
    return name, owner.strip()


def summarize(results: list[dict[str, Any]], names: dict[str, str]) -> str:
    """One-line toast text: ``Discovered 1 node: Bergblick (SNR 11.5 here, 12.0 there)``."""
    if not results:
        return "Discover: no answers (repeaters answer at most 4 times per 2 min)"
    parts = []
    for result in results:
        name = names.get(result["pub_key"]) or placeholder_name(
            result["node_type"], result["pub_key"]
        )
        here, there = result.get("snr_here"), result.get("snr_there")
        snr = (
            f" (SNR {here:.1f} here, {there:.1f} there)"
            if here is not None and there is not None
            else ""
        )
        parts.append(f"{name}{snr}")
    count = len(results)
    return f"Discovered {count} node{'' if count == 1 else 's'}: " + ", ".join(parts)
