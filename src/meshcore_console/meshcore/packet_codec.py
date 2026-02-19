from __future__ import annotations

import unicodedata
from typing import Any

import struct

from meshcore_console.core.packets import is_encrypted_type
from meshcore_console.core.types import PacketDataDict


def repair_utf8(text: str) -> str:
    """Repair double-encoded UTF-8 strings (mojibake).

    When a UTF-8 string is misinterpreted as Latin-1 and re-encoded to UTF-8,
    emojis and other multi-byte characters appear as garbled text.  This
    function detects and reverses that double-encoding.

    Example: "ð\x9f\x98\x80" (double-encoded 😀) → "😀"
    """
    if not text:
        return text
    try:
        # If every character fits in Latin-1 AND re-interpreting the bytes as
        # UTF-8 produces a shorter string, the original was double-encoded.
        raw = text.encode("latin-1")
        recovered = raw.decode("utf-8")
        if recovered != text:
            return recovered
    except (UnicodeDecodeError, UnicodeEncodeError):
        pass
    return text


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
    """Extract sender name from packet.decrypted (the only source on pyMC_core Packet)."""
    decrypted = packet.decrypted
    if not decrypted:
        return None
    # GRP_TXT: sender_name in group_text_data
    group_data = decrypted.get("group_text_data", {})
    if group_data.get("sender_name"):
        return repair_utf8(str(group_data["sender_name"]))
    return None


def _extract_sender_id(packet: Any) -> str | None:
    """Extract sender ID from packet — not available on pyMC_core Packet directly."""
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
                result["advert_name"] = repair_utf8(name)
            return result
        except ValueError:
            pass  # malformed advert payload — fall through to manual parser

    # --- Fallback: manual parser (for mock / no pyMC_core) -------------
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
                    name = repair_utf8(name)
                    if name and not any(unicodedata.category(c) == "Cc" for c in name):
                        result["advert_name"] = name
                except Exception:
                    pass
    except Exception:
        pass

    return result


def packet_to_dict(packet: Any) -> PacketDataDict:
    payload_type = packet.get_payload_type()
    route_type = packet.get_route_type()
    payload_type_name = PAYLOAD_TYPES.get(payload_type)
    route_type_name = ROUTE_TYPES.get(route_type)

    payload_text = None
    payload_hex = None
    payload_bytes_val = packet.get_payload()
    if payload_bytes_val:
        payload_hex = payload_bytes_val.hex()
        try:
            payload_text = payload_bytes_val.decode("utf-8")
        except UnicodeDecodeError:
            payload_text = None

    # Check for decrypted data (GRP_TXT, TXT_MSG have encrypted payloads)
    decrypted = packet.decrypted or {}
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

    # For ANON_REQ packets, extract the sender's full public key from cleartext
    # Wire format: [dest_hash(1)] [sender_pub_key(32)] [MAC+encrypted...]
    anon_sender_pubkey: str | None = None
    is_anon_req = payload_type_name == "ANON_REQ" or payload_type == 7
    if is_anon_req and payload_bytes_val and len(payload_bytes_val) >= 33:
        anon_sender_pubkey = payload_bytes_val[1:33].hex()
        if not sender_id:
            sender_id = anon_sender_pubkey[:16]

    # For MULTIPART packets, parse the compound byte
    # Wire format: [compound(1)] [inner_payload...]
    # compound = (remaining << 4) | inner_type
    multipart_remaining: int | None = None
    multipart_inner_type: int | None = None
    multipart_inner_type_name: str | None = None
    is_multipart = payload_type_name == "MULTIPART" or payload_type == 10
    if is_multipart and payload_bytes_val and len(payload_bytes_val) >= 1:
        compound = payload_bytes_val[0]
        multipart_remaining = (compound >> 4) & 0x0F
        multipart_inner_type = compound & 0x0F
        multipart_inner_type_name = PAYLOAD_TYPES.get(
            multipart_inner_type, f"0x{multipart_inner_type:02X}"
        )

    # For CONTROL packets, parse discovery request/response fields
    control_type: str | None = None
    control_data: dict[str, Any] = {}
    is_control = payload_type_name == "CONTROL" or payload_type == 11
    if is_control and payload_bytes_val and len(payload_bytes_val) >= 6:
        try:
            first_byte = payload_bytes_val[0]
            ctl_kind = first_byte & 0xF0
            if ctl_kind == 0x80:  # CTL_TYPE_NODE_DISCOVER_REQ
                control_type = "DISCOVER_REQ"
                prefix_only = bool(first_byte & 0x01)
                filter_byte = payload_bytes_val[1]
                tag = struct.unpack("<I", payload_bytes_val[2:6])[0]
                control_data = {
                    "tag": tag,
                    "filter": filter_byte,
                    "prefix_only": prefix_only,
                }
            elif ctl_kind == 0x90:  # CTL_TYPE_NODE_DISCOVER_RESP
                control_type = "DISCOVER_RESP"
                node_type = first_byte & 0x0F
                tag = struct.unpack("<I", payload_bytes_val[2:6])[0]
                pub_key = payload_bytes_val[6:]
                control_data = {
                    "tag": tag,
                    "node_type": node_type,
                    "pub_key": pub_key.hex() if pub_key else "",
                }
        except Exception:
            pass

    # Extract routing path information
    path_len = packet.path_len or 0
    path_bytes = packet.path
    path_hops: list[str] = []
    if path_bytes and path_len > 0:
        for i in range(min(path_len, len(path_bytes))):
            path_hops.append(f"{path_bytes[i]:02X}")

    # For TRACE packets, path[] contains per-hop SNR values (int8_t, SNR*4),
    # NOT node ID hashes like other packet types.
    trace_snr_values: list[float] = []
    is_trace = payload_type_name == "TRACE" or payload_type == 9
    if is_trace and path_bytes and path_len > 0:
        # Re-interpret path bytes as signed SNR values
        path_hops = []  # Clear — these aren't hop IDs for TRACE
        for i in range(min(path_len, len(path_bytes))):
            raw = path_bytes[i]
            # Convert from unsigned byte to signed int8_t, then divide by 4
            signed = raw if raw < 128 else raw - 256
            trace_snr_values.append(signed / 4.0)

    raw_length = packet.get_raw_length()
    packet_hash = packet.get_packet_hash_hex(16)  # First 16 hex chars

    return {
        "payload_type": payload_type,
        "payload_type_name": payload_type_name,
        "route_type": route_type,
        "route_type_name": route_type_name,
        "payload_len": packet.payload_len,
        "header": packet.header,
        "snr": packet.snr,
        "rssi": packet.rssi,
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
        "control_type": control_type,
        "control_data": control_data,
        "anon_sender_pubkey": anon_sender_pubkey,
        "multipart_remaining": multipart_remaining,
        "multipart_inner_type": multipart_inner_type,
        "multipart_inner_type_name": multipart_inner_type_name,
        "raw_length": raw_length,
        "trace_snr_values": trace_snr_values or None,
        "raw": None,
    }
