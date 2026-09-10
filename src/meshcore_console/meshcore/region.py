"""Region scope helpers for transport-code flood scoping (issue #90).

MeshCore repeaters with firmware 1.10+ can filter flood traffic by region.
A scoped flood packet uses ROUTE_TYPE_TRANSPORT_FLOOD and carries a 16-bit
transport code derived from the region name. Repeaters forward the packet
only if the code matches a region they serve.

The dispatcher applies the app-wide default scope (see
``dispatcher.default_flood_transport_key`` in openhop_core). The helpers here
apply a per-channel scope to one packet before dispatch; the
``_flood_scope_applied`` marker then stops the dispatcher from re-scoping it.
"""

from __future__ import annotations

from typing import Any

# openhop_core hashes the canonical name as ASCII and rejects more than 64
# characters, the '#' included. Mirror both limits here.
MAX_REGION_SCOPE_LEN = 64


def normalize_region_scope(name: str | None) -> str | None:
    """Return the canonical ``#name`` form of a region scope.

    Returns None for an empty or blank name, which means "no scope".

    Every path that persists a scope goes through this function, so it also
    validates: a name openhop_core cannot hash must never reach the database.
    Otherwise a saved default is dropped on the next connection, and a saved
    channel scope breaks every send on that channel.

    Raises:
        ValueError: if the name is not ASCII, or is too long.
    """
    if not name:
        return None
    clean = name.strip()
    if not clean or clean == "#":
        return None
    if not clean.startswith("#"):
        clean = f"#{clean}"
    if not clean.isascii():
        raise ValueError(f"region scope must use ASCII characters only: {clean!r}")
    if len(clean) > MAX_REGION_SCOPE_LEN:
        raise ValueError(
            f"region scope is too long: {len(clean)} characters "
            f"(maximum {MAX_REGION_SCOPE_LEN}, including the '#')"
        )
    return clean


def region_transport_key(name: str) -> bytes:
    """Derive the 16-byte transport key for a region scope name.

    Raises ValueError for an empty, non-ASCII, or too-long name.
    """
    from openhop_core.protocol.transport_keys import get_auto_key_for

    normalized = normalize_region_scope(name)
    if normalized is None:
        raise ValueError("region scope name is empty")
    return get_auto_key_for(normalized)


def apply_region_scope(pkt: Any, region_name: str) -> None:
    """Scope one outgoing flood packet to a region, in place.

    Delegates to openhop_core's shared ``scope_packet`` primitive, which sets
    the transport codes and switches the route type to
    ROUTE_TYPE_TRANSPORT_FLOOD. Packets that do not use plain flood routing
    are left unchanged.
    """
    from openhop_core.protocol.constants import ROUTE_TYPE_FLOOD
    from openhop_core.protocol.transport_keys import scope_packet

    if pkt.get_route_type() != ROUTE_TYPE_FLOOD:
        return
    scope_packet(pkt, region_transport_key(region_name))
    # A per-channel decision is authoritative: the marker stops the
    # dispatcher's default scope from re-scoping the packet at TX time.
    pkt._flood_scope_applied = True
