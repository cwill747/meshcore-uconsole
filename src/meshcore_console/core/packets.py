"""Polymorphic packet type handlers.

Each PayloadType gets a handler subclass that encapsulates display logic:
CSS class, encryption flag, content summary, and content formatting.
"""

from __future__ import annotations

from meshcore_console.core.enums import PayloadType


class PacketTypeHandler:
    """Base handler — subclasses override per-type behaviour."""

    name: PayloadType = PayloadType.UNKNOWN
    css_class: str = "type-other"
    encrypted: bool = False

    def content_summary(self, data: dict) -> str:
        """Fallback content when no decrypted text is available."""
        return ""

    def format_content(self, payload_text: str, data: dict) -> str:
        """Format decrypted text for display. Default: return as-is."""
        return payload_text


# ------------------------------------------------------------------
# Concrete handlers
# ------------------------------------------------------------------


class AdvertHandler(PacketTypeHandler):
    name = PayloadType.ADVERT
    css_class = "type-advert"

    _ADV_TYPE_NAMES: dict[int, str] = {
        0: "",
        1: "chat",
        2: "repeater",
        3: "room",
        4: "sensor",
    }

    def content_summary(self, data: dict) -> str:
        advert_name = data.get("advert_name")
        advert_type = data.get("advert_type")
        type_label = self._ADV_TYPE_NAMES.get(advert_type, "") if advert_type else ""
        if advert_name:
            lat = data.get("advert_lat")
            lon = data.get("advert_lon")
            suffix = f" ({type_label})" if type_label else ""
            if lat is not None and lon is not None:
                return f"{advert_name}{suffix} @ {lat:.4f}, {lon:.4f}"
            return f"Advert: {advert_name}{suffix}"
        return "Advert"


class AckHandler(PacketTypeHandler):
    name = PayloadType.ACK
    css_class = "type-ack"

    def content_summary(self, data: dict) -> str:
        ack_hash = data.get("ack_hash") or data.get("packet_hash")
        if ack_hash:
            return f"ACK {ack_hash[:12]}"
        return "ACK"


class PathHandler(PacketTypeHandler):
    name = PayloadType.PATH
    css_class = "type-path"

    def content_summary(self, data: dict) -> str:
        path_hops = data.get("path_hops") or []
        if path_hops:
            return f"Path: {' → '.join(str(h)[:8] for h in path_hops[:5])}"
        return "Path discovery"


class TraceHandler(PacketTypeHandler):
    name = PayloadType.TRACE
    css_class = "type-path"

    def content_summary(self, data: dict) -> str:
        snr_values = data.get("trace_snr_values")
        if snr_values:
            snr_chain = " → ".join(f"{s:.1f}dB" for s in snr_values[:6])
            return f"Trace SNR: {snr_chain}"
        return "Trace"


class GrpTxtHandler(PacketTypeHandler):
    name = PayloadType.GRP_TXT
    css_class = "type-grp"
    encrypted = True

    def content_summary(self, data: dict) -> str:
        channel = data.get("channel_name")
        if channel:
            return f"#{channel} (encrypted)"
        return "(encrypted)"

    def format_content(self, payload_text: str, data: dict) -> str:
        channel = data.get("channel_name")
        if channel:
            return f"#{channel}: {payload_text}"
        return payload_text


class GrpDataHandler(PacketTypeHandler):
    name = PayloadType.GRP_DATA
    css_class = "type-grp"
    encrypted = True

    def content_summary(self, data: dict) -> str:
        channel = data.get("channel_name")
        if channel:
            return f"#{channel} (data)"
        return "(data)"

    def format_content(self, payload_text: str, data: dict) -> str:
        channel = data.get("channel_name")
        if channel:
            return f"#{channel}: {payload_text}"
        return payload_text


class TxtMsgHandler(PacketTypeHandler):
    name = PayloadType.TXT_MSG
    css_class = "type-txt"
    encrypted = True

    def content_summary(self, data: dict) -> str:
        return "(encrypted)"


class MultipartHandler(PacketTypeHandler):
    name = PayloadType.MULTIPART
    css_class = "type-multi"

    def content_summary(self, data: dict) -> str:
        inner_name = data.get("multipart_inner_type_name")
        remaining = data.get("multipart_remaining")
        if inner_name is not None and remaining is not None:
            return f"Multi-{inner_name} ({remaining} remaining)"
        # Fallback for legacy/mock data
        part = data.get("part_num", data.get("fragment_num", "?"))
        total = data.get("total_parts", data.get("fragment_count", "?"))
        return f"Part {part}/{total}"


class ResponseHandler(PacketTypeHandler):
    name = PayloadType.RESPONSE
    css_class = "type-response"

    def content_summary(self, data: dict) -> str:
        text = data.get("payload_text")
        if text:
            return f"Response: {text[:60]}"
        return "Response"


class ReqHandler(PacketTypeHandler):
    name = PayloadType.REQ
    css_class = "type-req"

    # Numeric request type codes → human-readable names
    _REQ_TYPE_NAMES: dict[int, str] = {
        0x01: "STATUS",
        0x02: "KEEP_ALIVE",
        0x03: "TELEMETRY",
        0x05: "ACCESS_LIST",
        0x06: "NEIGHBOURS",
    }

    def content_summary(self, data: dict) -> str:
        req_type = data.get("request_type") or data.get("req_type")
        if req_type is not None:
            if isinstance(req_type, int):
                name = self._REQ_TYPE_NAMES.get(req_type, f"0x{req_type:02X}")
                return f"Request: {name}"
            return f"Request: {req_type}"
        return "Request"


class AnonReqHandler(PacketTypeHandler):
    name = PayloadType.ANON_REQ
    css_class = "type-req"

    # ANON_REQ sub-types are different from REQ sub-types
    _ANON_TYPE_NAMES: dict[int, str] = {
        0x01: "REGIONS",
        0x02: "OWNER_INFO",
        0x03: "BASIC_INFO",
    }

    def content_summary(self, data: dict) -> str:
        pubkey = data.get("anon_sender_pubkey", "")
        prefix = pubkey[:8] if pubkey else ""
        # ANON_REQ sub-type is encrypted, so we usually can't see it.
        # Show the sender pubkey prefix instead (the key feature of ANON_REQ).
        if prefix:
            return f"Anon REQ from {prefix}"
        return "Anon REQ"


class ControlHandler(PacketTypeHandler):
    name = PayloadType.CONTROL
    css_class = "type-req"

    def content_summary(self, data: dict) -> str:
        control_type = data.get("control_type")
        control_data = data.get("control_data") or {}
        if control_type == "DISCOVER_REQ":
            filt = control_data.get("filter", 0)
            return f"Discovery REQ (filter=0x{filt:02X})"
        if control_type == "DISCOVER_RESP":
            pub_key = control_data.get("pub_key", "")
            prefix = pub_key[:8] if pub_key else "?"
            return f"Discovery RESP from {prefix}"
        # Fallback: try to parse from payload_hex
        payload_hex = data.get("payload_hex", "")
        if payload_hex and len(payload_hex) >= 2:
            first_byte = int(payload_hex[:2], 16)
            ctl = first_byte & 0xF0
            if ctl == 0x80:
                return "Discovery REQ"
            if ctl == 0x90:
                return "Discovery RESP"
        return "Control"


class RawHandler(PacketTypeHandler):
    name = PayloadType.RAW
    css_class = "type-raw"


class UnknownHandler(PacketTypeHandler):
    name = PayloadType.UNKNOWN
    css_class = "type-other"


# ------------------------------------------------------------------
# Registry
# ------------------------------------------------------------------

_ALL_HANDLERS: list[PacketTypeHandler] = [
    AdvertHandler(),
    AckHandler(),
    PathHandler(),
    TraceHandler(),
    GrpTxtHandler(),
    GrpDataHandler(),
    TxtMsgHandler(),
    MultipartHandler(),
    ResponseHandler(),
    ReqHandler(),
    AnonReqHandler(),
    ControlHandler(),
    RawHandler(),
    UnknownHandler(),
]

_BY_NAME: dict[str, PacketTypeHandler] = {h.name.value: h for h in _ALL_HANDLERS}

# Numeric payload type -> handler (from pyMC_core protocol)
_NUMERIC_MAP: dict[int, PacketTypeHandler] = {
    0: _BY_NAME["REQ"],
    1: _BY_NAME["RESPONSE"],
    2: _BY_NAME["TXT_MSG"],
    3: _BY_NAME["ACK"],
    4: _BY_NAME["ADVERT"],
    5: _BY_NAME["GRP_TXT"],
    6: _BY_NAME["GRP_DATA"],
    7: _BY_NAME["ANON_REQ"],
    8: _BY_NAME["PATH"],
    9: _BY_NAME["TRACE"],
    10: _BY_NAME["MULTIPART"],
    11: _BY_NAME["CONTROL"],
    15: _BY_NAME["RAW"],
}

_UNKNOWN = _BY_NAME["UNKNOWN"]


def get_handler(name: str) -> PacketTypeHandler:
    """Look up handler by PayloadType name or prefix (e.g. ``"GRP_TXT"`` or ``"GRP"``)."""
    key = name.upper()
    handler = _BY_NAME.get(key)
    if handler:
        return handler
    # Prefix fallback — "GRP" matches GRP_TXT, "RESP" matches RESPONSE, etc.
    for h in _ALL_HANDLERS:
        if h.name.value.startswith(key):
            return h
    return _UNKNOWN


def get_handler_by_numeric(payload_type: int) -> PacketTypeHandler:
    """Look up handler by numeric payload type from the wire protocol."""
    return _NUMERIC_MAP.get(payload_type, _UNKNOWN)


def is_encrypted_type(payload_type: int) -> bool:
    """Return True if the numeric payload type has an encrypted payload."""
    return get_handler_by_numeric(payload_type).encrypted
