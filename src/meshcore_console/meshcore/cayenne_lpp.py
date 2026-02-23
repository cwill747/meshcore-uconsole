"""CayenneLPP encoder/decoder for telemetry payloads.

Uses the pycayennelpp library for encoding/decoding, and provides
``decode_cayenne_lpp_payload`` matching the interface pymc_core's
ProtocolResponseHandler expects from ``utils.cayenne_lpp_helpers``.
"""

from __future__ import annotations

from cayennelpp import LppFrame


def encode_gps(channel: int, lat: float, lon: float, alt: float = 0.0) -> bytes:
    """Encode a GPS location as CayenneLPP bytes."""
    frame = LppFrame()
    frame.add_gps(channel, lat, lon, alt)
    return bytes(frame)


def decode_cayenne_lpp_payload(hex_string: str) -> dict:
    """Decode a CayenneLPP hex payload into structured sensor data.

    Matches the signature expected by pymc_core's
    ``utils.cayenne_lpp_helpers.decode_cayenne_lpp_payload``.

    Returns:
        dict with ``sensor_count`` and ``sensors`` list, or ``error`` string.
    """
    try:
        data = bytes.fromhex(hex_string)
    except ValueError as e:
        return {"error": f"Invalid hex: {e}"}

    try:
        frame = LppFrame().from_bytes(data)
    except Exception as e:
        return {"error": f"LPP decode failed: {e}"}

    sensors: list[dict] = []
    for item in frame:
        value = item.value
        # GPS/Location type returns (lat, lon, alt) tuple
        if item.type == "Location":
            value = {"latitude": value[0], "longitude": value[1], "altitude": value[2]}
        elif isinstance(value, tuple) and len(value) == 1:
            value = value[0]

        sensors.append(
            {
                "channel": item.channel,
                "type": item.type,
                "type_id": item.type,
                "value": value,
                "raw_value": hex_string,
            }
        )

    if not sensors:
        return {"error": "No valid sensors found", "sensor_count": 0, "sensors": []}

    return {"sensor_count": len(sensors), "sensors": sensors}
