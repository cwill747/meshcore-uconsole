"""Mock data constants and factory helpers for testing and development."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from meshcore_console.core.models import Channel, Message, Peer

# Mock peer locations (spread around San Francisco Bay Area)
# Format: (name, peer_id, lat, lon, is_repeater)
MOCK_PEER_LOCATIONS: list[tuple[str, str, float, float, bool]] = [
    ("Relay Alpha", "relay-001", 37.7749, -122.4194, True),  # SF downtown
    ("Relay Beta", "relay-002", 37.8044, -122.2712, True),  # Oakland
    ("Node Gateway", "gateway-001", 37.5485, -122.0590, True),  # Fremont
    ("Alice", "peer-alice", 37.7849, -122.4094, False),  # SF north
    ("Bob", "peer-bob", 37.7649, -122.4294, False),  # SF south
    ("Charlie", "peer-charlie", 37.8716, -122.2727, False),  # Berkeley
    ("Diana", "peer-diana", 37.4419, -122.1430, False),  # Palo Alto
]

# Mock GPS waypoints: walking around SF Financial District
MOCK_GPS_WAYPOINTS: list[tuple[float, float]] = [
    (37.7749, -122.4194),  # SF Downtown (Market St)
    (37.7760, -122.4180),  # Moving NE
    (37.7775, -122.4165),  # Embarcadero area
    (37.7790, -122.4150),  # Near Ferry Building
    (37.7810, -122.4130),  # Along waterfront
    (37.7830, -122.4100),  # Pier 7 area
    (37.7850, -122.4080),  # Telegraph Hill direction
    (37.7870, -122.4060),  # North Beach approach
    (37.7850, -122.4090),  # Heading back
    (37.7820, -122.4120),  # Return path
    (37.7790, -122.4150),  # Back near ferry
    (37.7760, -122.4175),  # Almost home
]


def create_mock_channels() -> dict[str, Channel]:
    """Create mock channels for testing."""
    return {
        "test": Channel(channel_id="test", display_name="#test", unread_count=6),
        "ops": Channel(channel_id="ops", display_name="#ops", unread_count=2),
        "public": Channel(channel_id="public", display_name="#public", unread_count=1),
    }


def create_mock_peers() -> dict[str, Peer]:
    """Create mock peers for testing (subset of MOCK_PEER_LOCATIONS with full Peer objects)."""
    now = datetime.now(UTC)
    return {
        "Relay A": Peer(
            peer_id="relay-001",
            display_name="Relay A",
            signal_quality=78,
            is_repeater=True,
            latitude=37.7749,
            longitude=-122.4194,
            location_updated=now,
        ),
        "Backhaul B": Peer(
            peer_id="relay-002",
            display_name="Backhaul B",
            signal_quality=61,
            is_repeater=True,
            latitude=37.8044,
            longitude=-122.2712,
            location_updated=now,
        ),
        "Alice": Peer(
            peer_id="peer-alice",
            display_name="Alice",
            signal_quality=85,
            is_repeater=False,
            latitude=37.7849,
            longitude=-122.4094,
            location_updated=now,
        ),
        "Bob": Peer(
            peer_id="peer-bob",
            display_name="Bob",
            signal_quality=72,
            is_repeater=False,
            latitude=37.7649,
            longitude=-122.4294,
            location_updated=now,
        ),
    }


def create_mock_messages() -> list[Message]:
    """Create mock messages for testing."""
    return [
        Message(
            message_id=str(uuid4()),
            sender_id="Relay A",
            body="Route check complete",
            channel_id="test",
        ),
        Message(
            message_id=str(uuid4()),
            sender_id="Backhaul B",
            body="Link stable at SF7",
            channel_id="public",
        ),
        Message(
            message_id=str(uuid4()),
            sender_id="Relay A",
            body="Forwarding advert burst",
            channel_id="ops",
        ),
    ]


def create_mock_boot_events() -> list[dict]:
    """Create initial mock events for boot sequence."""
    return [
        {"type": "mock_boot", "data": {"message": "Mock session initialized"}},
        {"type": "peer_seen", "data": {"peer": "Relay A", "rssi": -66}},
        {"type": "peer_seen", "data": {"peer": "Backhaul B", "rssi": -71}},
    ]


def create_mock_packet_events() -> list[dict]:
    """Create mock packet events for the analyzer view.

    Packet types from pyMC_core (numeric ID -> name):
        0: REQ       - Request
        1: RESPONSE  - Response to REQ or ANON_REQ
        2: TXT_MSG   - Plain text message (encrypted)
        3: ACK       - Simple acknowledgment
        4: ADVERT    - Node advertising its identity
        5: GRP_TXT   - Unverified group text message
        6: GRP_DATA  - Unverified group datagram
        7: ANON_REQ  - Anonymous request
        8: PATH      - Path discovery
        9: TRACE     - Network trace
        10: MULTIPART - Multi-part message fragment
        15: RAW_CUSTOM - Raw custom packet

    Route types:
        0: TRANSPORT_FLOOD - Flood with transport layer
        1: FLOOD           - Simple flood routing
        2: DIRECT          - Direct point-to-point
        3: TRANSPORT_DIRECT - Direct with transport layer
    """
    now = datetime.now(UTC)
    yesterday = now - timedelta(days=1)

    def _mock_ts(seed: str, day: datetime = now, spread_min: int = 120) -> str:
        """Deterministic mock timestamp: hash the seed to pick an offset within the day."""
        h = int(hashlib.md5(seed.encode()).hexdigest(), 16)
        offset_sec = h % (spread_min * 60)
        return (day - timedelta(seconds=offset_sec)).isoformat()

    # Number of "today" packets; the rest are stamped as yesterday
    TODAY_COUNT = 8

    packets = [
        # ADVERT packet - node advertising identity with location
        {
            "type": "packet",
            "data": {
                "payload_type": 4,
                "payload_type_name": "ADVERT",
                "route_type": 1,
                "route_type_name": "FLOOD",
                "sender_name": "NL-HILLGM-RPT-01",
                "advert_name": "NL-HILLGM-RPT-01",
                "sender_id": "a1b2c3d4e5f60001",
                "sender_pubkey": "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f60001",
                "advert_lat": 52.3676,
                "advert_lon": 4.9041,
                "rssi": -87,
                "snr": 5.25,
                "payload_hex": "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6",
                "path_len": 2,
                "path_hops": ["B7", "C2"],
                "packet_hash": "DEF456789ABC",
            },
        },
        # GRP_TXT packet - group/channel message
        {
            "type": "packet",
            "data": {
                "payload_type": 5,
                "payload_type_name": "GRP_TXT",
                "route_type": 1,
                "route_type_name": "FLOOD",
                "sender_name": "THD Observer",
                "sender_id": "f5890d41abcd1234",
                "channel_name": "public",
                "payload_text": "@[NL-UTC-CM-Echo] observed at 14:32 UTC",
                "rssi": -95,
                "snr": -2.50,
                "payload_hex": "f589d410abcd1234567890abcdef",
                "path_len": 0,
                "path_hops": [],
                "packet_hash": "1234567890AB",
            },
        },
        # TXT_MSG packet - direct encrypted text message
        {
            "type": "packet",
            "data": {
                "payload_type": 2,
                "payload_type_name": "TXT_MSG",
                "route_type": 2,
                "route_type_name": "DIRECT",
                "sender_name": "EU-BASE-01",
                "sender_id": "4070c971deadbeef",
                "payload_text": "Hello mesh! Testing direct message routing.",
                "rssi": -78,
                "snr": 8.75,
                "payload_hex": "4070c971deadbeef1234567890abcdef",
                "path_len": 1,
                "path_hops": ["A3"],
                "packet_hash": "ABC123456789",
            },
        },
        # RESPONSE packet - acknowledgment with payload
        {
            "type": "packet",
            "data": {
                "payload_type": 1,
                "payload_type_name": "RESPONSE",
                "route_type": 2,
                "route_type_name": "DIRECT",
                "sender_name": "NL-RELAY-03",
                "sender_id": "15071654a6a8442e",
                "payload_text": "ACK 5CA1AD OK",
                "rssi": -92,
                "snr": 1.25,
                "payload_hex": "15071654a6a8442e5ca1ad00",
                "path_len": 0,
                "path_hops": [],
                "packet_hash": "5CA1AD000000",
            },
        },
        # ACK packet - simple acknowledgment
        {
            "type": "packet",
            "data": {
                "payload_type": 3,
                "payload_type_name": "ACK",
                "route_type": 2,
                "route_type_name": "DIRECT",
                "sender_name": "Alice",
                "sender_id": "peer-alice-0001",
                "payload_text": "",
                "ack_hash": "ABC123456789",  # Hash of packet being acknowledged
                "rssi": -65,
                "snr": 10.50,
                "payload_hex": "0300deadbeef",
                "path_len": 0,
                "path_hops": [],
                "packet_hash": "ACK00BEEF00",
            },
        },
        # Another GRP_TXT - channel activity
        {
            "type": "packet",
            "data": {
                "payload_type": 5,
                "payload_type_name": "GRP_TXT",
                "route_type": 1,
                "route_type_name": "FLOOD",
                "sender_name": "Bob",
                "sender_id": "peer-bob-0001",
                "channel_name": "ops",
                "payload_text": "Network status: 7 nodes online, 3 repeaters active",
                "rssi": -88,
                "snr": 3.00,
                "payload_hex": "0500abcdef1234567890",
                "path_len": 1,
                "path_hops": ["relay-001"],
                "packet_hash": "BOB123456789",
            },
        },
        # ADVERT from mobile node
        {
            "type": "packet",
            "data": {
                "payload_type": 4,
                "payload_type_name": "ADVERT",
                "route_type": 1,
                "route_type_name": "FLOOD",
                "sender_name": "Mobile-Charlie",
                "advert_name": "Mobile-Charlie",
                "sender_id": "charlie-mobile-01",
                "sender_pubkey": "c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b20002",
                "advert_lat": 37.8716,
                "advert_lon": -122.2727,
                "rssi": -72,
                "snr": 7.50,
                "payload_hex": "c3d4e5f6a1b2c3d4e5f6a1b2",
                "path_len": 0,
                "path_hops": [],
                "packet_hash": "CHARLIE12345",
            },
        },
        # GRP_DATA packet - group binary data
        {
            "type": "packet",
            "data": {
                "payload_type": 6,
                "payload_type_name": "GRP_DATA",
                "route_type": 1,
                "route_type_name": "FLOOD",
                "sender_name": "Sensor Node",
                "sender_id": "sensor-001",
                "channel_name": "telemetry",
                "payload_text": "",
                "rssi": -90,
                "snr": 0.50,
                "payload_hex": "0600telemetry0sensor001data",
                "path_len": 1,
                "path_hops": ["relay-001"],
                "packet_hash": "GRPDATA12345",
            },
        },
        # --- Yesterday's packets (older traffic) ---
        # PATH packet - route discovery
        {
            "type": "packet",
            "data": {
                "payload_type": 8,
                "payload_type_name": "PATH",
                "route_type": 1,
                "route_type_name": "FLOOD",
                "sender_name": "Relay Alpha",
                "sender_id": "relay-001",
                "payload_text": "",
                "rssi": -80,
                "snr": 4.25,
                "payload_hex": "0800relay001relay002gateway",
                "path_len": 3,
                "path_hops": ["relay-001", "relay-002", "gateway-001"],
                "packet_hash": "PATH00123456",
            },
        },
        # TXT_MSG - multi-hop message
        {
            "type": "packet",
            "data": {
                "payload_type": 2,
                "payload_type_name": "TXT_MSG",
                "route_type": 0,
                "route_type_name": "TRANSPORT_FLOOD",
                "sender_name": "Diana",
                "sender_id": "peer-diana-0001",
                "payload_text": "Checking in from Palo Alto. Signal is strong!",
                "rssi": -105,
                "snr": -4.50,
                "payload_hex": "0200diana0001checkinginfrom",
                "path_len": 4,
                "path_hops": ["D1", "C2", "B3", "A4"],
                "packet_hash": "DIANA1234567",
            },
        },
        # GRP_TXT - emergency channel
        {
            "type": "packet",
            "data": {
                "payload_type": 5,
                "payload_type_name": "GRP_TXT",
                "route_type": 1,
                "route_type_name": "FLOOD",
                "sender_name": "Relay Beta",
                "sender_id": "relay-002",
                "channel_name": "emergency",
                "payload_text": "All nodes: mesh health check initiated",
                "rssi": -68,
                "snr": 9.00,
                "payload_hex": "0500emergency0relay002health",
                "path_len": 0,
                "path_hops": [],
                "packet_hash": "EMERGENCY001",
            },
        },
        # TRACE packet - network trace/diagnostic
        {
            "type": "packet",
            "data": {
                "payload_type": 9,
                "payload_type_name": "TRACE",
                "route_type": 1,
                "route_type_name": "FLOOD",
                "sender_name": "Node Gateway",
                "sender_id": "gateway-001",
                "payload_text": "",
                "rssi": -75,
                "snr": 6.00,
                "payload_hex": "0900trace0gateway001relay",
                "path_len": 2,
                "path_hops": ["gateway-001", "relay-001"],
                "packet_hash": "TRACE0012345",
            },
        },
        # REQ packet - request message
        {
            "type": "packet",
            "data": {
                "payload_type": 0,
                "payload_type_name": "REQ",
                "route_type": 2,
                "route_type_name": "DIRECT",
                "sender_name": "Charlie",
                "sender_id": "peer-charlie",
                "request_type": "STATUS",
                "payload_text": "",
                "rssi": -82,
                "snr": 2.75,
                "payload_hex": "0000req0charlie0status",
                "path_len": 0,
                "path_hops": [],
                "packet_hash": "REQ000123456",
            },
        },
    ]

    # Stamp each packet with a deterministic, spread-out timestamp
    for i, pkt in enumerate(packets):
        day = now if i < TODAY_COUNT else yesterday
        seed = pkt["data"]["packet_hash"]
        pkt["received_at"] = _mock_ts(seed, day)

    # Sort chronologically (oldest first) so arrival order matches timestamps
    packets.sort(key=lambda p: p["received_at"])
    return packets
