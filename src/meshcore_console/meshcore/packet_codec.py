from __future__ import annotations

import unicodedata
from typing import Any

from meshcore_console.core.packets import is_encrypted_type
from meshcore_console.core.types import PacketDataDict

try:
    from pymc_core.protocol.utils import PAYLOAD_TYPES, ROUTE_TYPES
except ImportError:
    PAYLOAD_TYPES = {}
    ROUTE_TYPES = {}

try:
    from pymc_core.protocol.utils import (
        decode_appdata as _pymc_decode_appdata,
        parse_advert_payload as _pymc_parse_advert,
    )

    _HAS_PYMC_PARSER = True
except ImportError:
    _HAS_PYMC_PARSER = False


def _extract_sender_name(packet: Any) -> str | None:
    """Try to extract sender/peer name from packet via various attributes."""
    # First check decrypted data (for GRP_TXT, TXT_MSG, etc.)
    decrypted = getattr(packet, "decrypted", None)
    if decrypted and isinstance(decrypted, dict):
        # Check group_text_data for GRP_TXT
        group_data = decrypted.get("group_text_data", {})
        if group_data.get("sender_name"):
            return str(group_data["sender_name"])
        # Check text_data for TXT_MSG
        text_data = decrypted.get("text_data", {})
        if text_data.get("sender_name"):
            return str(text_data["sender_name"])

    # Try common sender attribute names
    for attr in ("sender_name", "peer_name", "from_name", "source_name", "name", "contact_name"):
        val = getattr(packet, attr, None)
        if val is not None and str(val).strip():
            return str(val)

    # Try header-based sender info
    header = getattr(packet, "header", None)
    if header is not None:
        for attr in ("sender_name", "from_name", "source_name", "name"):
            val = getattr(header, attr, None)
            if val is not None and str(val).strip():
                return str(val)

    # Try contact object if present
    contact = getattr(packet, "contact", None) or getattr(packet, "sender", None)
    if contact is not None:
        for attr in ("name", "display_name", "node_name"):
            val = getattr(contact, attr, None)
            if val is not None and str(val).strip():
                return str(val)

    return None


def _extract_sender_id(packet: Any) -> str | None:
    """Try to extract sender ID from packet."""
    for attr in ("sender_id", "peer_id", "from_id", "source_id", "sender"):
        val = getattr(packet, attr, None)
        if val is not None:
            if isinstance(val, bytes):
                return val.hex()[:16]
            return str(val)[:16]

    header = getattr(packet, "header", None)
    if header is not None:
        for attr in ("sender_id", "from_id", "source_id", "sender"):
            val = getattr(header, attr, None)
            if val is not None:
                if isinstance(val, bytes):
                    return val.hex()[:16]
                return str(val)[:16]

    return None


def _parse_advert_payload(payload_bytes: bytes) -> dict[str, Any]:
    """Parse an ADVERT payload to extract name and location.

    Delegates to pyMC_core's canonical parser when available, with a
    built-in fallback for environments where pyMC_core is not installed.
    """
    # --- Primary path: use pyMC_core's parser (authoritative) ----------
    if _HAS_PYMC_PARSER:
        try:
            parsed = _pymc_parse_advert(payload_bytes)
            decoded = _pymc_decode_appdata(parsed["appdata"])
            result: dict[str, Any] = {"sender_pubkey": parsed["pubkey"]}
            flags = decoded.get("flags", 0)
            result["advert_type"] = flags & 0x0F
            lat = decoded.get("latitude")
            lon = decoded.get("longitude")
            if lat is not None and lon is not None:
                result["advert_lat"] = lat
                result["advert_lon"] = lon
            name = decoded.get("node_name")
            if name:
                result["advert_name"] = name
            return result
        except Exception:
            pass  # fall through to manual parser

    # --- Fallback: manual parser (for mock / no pyMC_core) -------------
    import struct

    result = {}

    PUB_KEY_SIZE = 32
    TIMESTAMP_SIZE = 4
    SIGNATURE_SIZE = 64
    HEADER_SIZE = PUB_KEY_SIZE + TIMESTAMP_SIZE + SIGNATURE_SIZE  # 100

    try:
        if len(payload_bytes) < HEADER_SIZE + 1:
            return result

        pubkey = payload_bytes[:PUB_KEY_SIZE]
        result["sender_pubkey"] = pubkey.hex()

        appdata = payload_bytes[HEADER_SIZE:]
        if not appdata:
            return result

        flags = appdata[0]
        offset = 1
        result["advert_type"] = flags & 0x0F

        if flags & 0x10:  # HAS_LOCATION
            if len(appdata) >= offset + 8:
                lat_int = struct.unpack("<i", appdata[offset : offset + 4])[0]
                lon_int = struct.unpack("<i", appdata[offset + 4 : offset + 8])[0]
                result["advert_lat"] = lat_int / 1000000.0
                result["advert_lon"] = lon_int / 1000000.0
                offset += 8

        if flags & 0x20:  # HAS_FEATURE1
            if len(appdata) >= offset + 2:
                offset += 2

        if flags & 0x40:  # HAS_FEATURE2
            if len(appdata) >= offset + 2:
                offset += 2

        if flags & 0x80:  # HAS_NAME
            name_bytes = appdata[offset:]
            if name_bytes:
                try:
                    name = name_bytes.decode("utf-8").rstrip("\x00").strip()
                    if name and not any(unicodedata.category(c) == "Cc" for c in name):
                        result["advert_name"] = name
                except Exception:
                    pass
    except Exception:
        pass

    return result


def packet_to_dict(packet: Any) -> PacketDataDict:
    payload_type_name = None
    route_type = None
    payload_type = None
    try:
        payload_type = packet.get_payload_type()
    except Exception:
        payload_type = None

    try:
        route_type = packet.get_route_type()
    except Exception:
        route_type = None

    payload_type_name = PAYLOAD_TYPES.get(payload_type, None) if payload_type is not None else None
    route_type_name = ROUTE_TYPES.get(route_type, None) if route_type is not None else None

    payload_text = None
    payload_hex = None
    payload_bytes_val: bytes | None = None
    # Prefer get_payload() which respects payload_len (matches pyMC_core handlers)
    get_payload = getattr(packet, "get_payload", None)
    if callable(get_payload):
        try:
            payload_bytes_val = get_payload()
        except Exception:
            payload_bytes_val = None
    if payload_bytes_val is None:
        raw = getattr(packet, "payload", None)
        if raw is not None:
            try:
                payload_bytes_val = bytes(raw)
            except Exception:
                pass
    if payload_bytes_val is not None:
        try:
            payload_hex = payload_bytes_val.hex()
            try:
                payload_text = payload_bytes_val.decode("utf-8")
            except Exception:
                payload_text = None
        except Exception:
            payload_hex = None

    # Check for decrypted data (GRP_TXT, TXT_MSG have encrypted payloads)
    decrypted = getattr(packet, "decrypted", None) or {}
    decrypted_text = None
    channel_name = None

    # GRP_TXT: decrypted content in group_text_data
    group_data = decrypted.get("group_text_data", {})
    if group_data:
        decrypted_text = group_data.get("text") or group_data.get("full_content")
        channel_name = group_data.get("channel_name")

    # TXT_MSG: decrypted content in text_data
    text_data = decrypted.get("text_data", {})
    if text_data and not decrypted_text:
        decrypted_text = text_data.get("text") or text_data.get("message")

    # Use decrypted_text as payload_text if available
    if decrypted_text:
        payload_text = decrypted_text
    elif payload_type is not None and is_encrypted_type(payload_type):
        # Encrypted payload types — raw UTF-8 decode is garbage, clear it.
        payload_text = None

    # Extract sender info
    sender_name = _extract_sender_name(packet)
    sender_id = _extract_sender_id(packet)

    # For ADVERT packets, try to parse the payload for name/location
    # Check both name and numeric type (ADVERT is type 4 in pymc_core)
    advert_info: dict[str, Any] = {}
    is_advert = payload_type_name == "ADVERT" or payload_type == 4
    if is_advert and payload_bytes_val:
        advert_info = _parse_advert_payload(payload_bytes_val)
        # Use advert name as sender if we found one and don't have a sender_name
        if not sender_name and advert_info.get("advert_name"):
            sender_name = advert_info["advert_name"]
        # Use sender_pubkey (truncated) as sender_id if we don't have one
        if not sender_id and advert_info.get("sender_pubkey"):
            sender_id = advert_info["sender_pubkey"][:16]

    # Extract routing path information
    path_len = getattr(packet, "path_len", 0) or 0
    path_bytes = getattr(packet, "path", None)
    path_hops: list[str] = []
    if path_bytes and path_len > 0:
        for i in range(min(path_len, len(path_bytes))):
            path_hops.append(f"{path_bytes[i]:02X}")

    # Get packet hash for deduplication
    packet_hash = None
    try:
        packet_hash = packet.get_packet_hash_hex(16)  # First 16 hex chars
    except Exception:
        pass

    return {
        "payload_type": payload_type,
        "payload_type_name": payload_type_name,
        "route_type": route_type,
        "route_type_name": route_type_name,
        "payload_len": getattr(packet, "payload_len", None),
        "header": getattr(packet, "header", None),
        "snr": getattr(packet, "snr", None),
        "rssi": getattr(packet, "rssi", None),
        "payload_text": payload_text,
        "payload_hex": payload_hex,
        "sender_name": sender_name,
        "sender_id": sender_id,
        "sender_pubkey": advert_info.get("sender_pubkey"),
        "channel_name": channel_name,
        "advert_name": advert_info.get("advert_name"),
        "advert_type": advert_info.get("advert_type"),
        "advert_lat": advert_info.get("advert_lat"),
        "advert_lon": advert_info.get("advert_lon"),
        "path_len": path_len,
        "path_hops": path_hops,
        "packet_hash": packet_hash,
        "raw": None,
    }
