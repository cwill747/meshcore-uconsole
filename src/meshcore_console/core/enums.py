"""Enums for event types, packet types, and filter types."""

from enum import StrEnum


class PayloadType(StrEnum):
    """Payload types for mesh packets.

    From pyMC_core protocol:
        0: REQ        - Request
        1: RESPONSE   - Response to REQ or ANON_REQ
        2: TXT_MSG    - Plain text message (encrypted)
        3: ACK        - Simple acknowledgment
        4: ADVERT     - Node advertising its identity
        5: GRP_TXT    - Unverified group text message
        6: GRP_DATA   - Unverified group datagram
        7: ANON_REQ   - Anonymous request
        8: PATH       - Path discovery
        9: TRACE      - Network trace
        10: MULTIPART - Multi-part message fragment
        15: RAW_CUSTOM - Raw custom packet
    """

    REQ = "REQ"
    RESPONSE = "RESPONSE"
    TXT_MSG = "TXT_MSG"
    ACK = "ACK"
    ADVERT = "ADVERT"
    GRP_TXT = "GRP_TXT"
    GRP_DATA = "GRP_DATA"
    ANON_REQ = "ANON_REQ"
    PATH = "PATH"
    TRACE = "TRACE"
    MULTIPART = "MULTIPART"
    CONTROL = "CONTROL"
    RAW = "RAW"
    UNKNOWN = "UNKNOWN"


class EventType(StrEnum):
    """Event types emitted by the meshcore service."""

    # Session lifecycle
    SESSION_CONNECTED = "session_connected"
    SESSION_DISCONNECTED = "session_disconnected"

    # Packet events
    PACKET = "packet"
    RAW_PACKET = "raw_packet"

    # Message events
    MESSAGE_SENT = "message_sent"
    ADVERT_SENT = "advert_sent"

    # Contact/peer events
    CONTACT_RECEIVED = "contact_received"
    ADVERT_RECEIVED = "advert_received"
    PEER_SEEN = "peer_seen"

    # Settings
    SETTINGS_UPDATED = "settings_updated"

    # EventService events (mesh.* naming)
    MESH_CONTACT_NEW = "mesh.contact.new"
    MESH_CHANNEL_MESSAGE_NEW = "mesh.channel.message.new"
    MESH_MESSAGE_NEW = "mesh.message.new"


class AnalyzerFilter(StrEnum):
    """Filter options for the analyzer view.

    Filters match packet_type using substring matching, so:
    - GRP matches both GRP_TXT and GRP_DATA
    - TXT matches TXT_MSG
    - REQ matches both REQ and ANON_REQ
    """

    ALL = "ALL"
    TXT_MSG = "TXT"  # Matches TXT_MSG
    GRP = "GRP"  # Matches GRP_TXT and GRP_DATA
    ADVERT = "ADVERT"
    ACK = "ACK"  # Simple acknowledgments
    REQ = "REQ"  # Matches REQ and ANON_REQ
    RESPONSE = "RESP"  # Matches RESPONSE
    CONTROL = "CONTROL"  # Matches CONTROL (discovery)
    PATH = "PATH"  # Path discovery and TRACE
