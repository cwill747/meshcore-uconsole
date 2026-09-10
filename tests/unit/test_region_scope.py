"""Tests for region scoping helpers (issue #90)."""

from __future__ import annotations

import hashlib

import pytest

from meshcore_console.meshcore.region import (
    apply_region_scope,
    normalize_region_scope,
    region_transport_key,
)


def test_normalize_region_scope_adds_hash_prefix() -> None:
    assert normalize_region_scope("germany") == "#germany"
    assert normalize_region_scope("#germany") == "#germany"
    assert normalize_region_scope("  usa  ") == "#usa"


def test_normalize_region_scope_empty_means_no_scope() -> None:
    assert normalize_region_scope(None) is None
    assert normalize_region_scope("") is None
    assert normalize_region_scope("   ") is None
    assert normalize_region_scope("#") is None


def test_region_transport_key_matches_meshcore_derivation() -> None:
    # MeshCore transport keys are sha256(name)[:16], name includes '#'.
    expected = hashlib.sha256(b"#germany").digest()[:16]
    assert region_transport_key("germany") == expected
    assert region_transport_key("#germany") == expected


def test_region_transport_key_rejects_empty_name() -> None:
    with pytest.raises(ValueError):
        region_transport_key("   ")


def _make_advert(route_type: str):
    from openhop_core.protocol.identity import LocalIdentity
    from openhop_core.protocol.packet_builder import PacketBuilder

    return PacketBuilder.create_self_advert(
        local_identity=LocalIdentity(),
        name="test-node",
        route_type=route_type,
    )


def test_apply_region_scope_converts_flood_to_transport_flood() -> None:
    from openhop_core.protocol.constants import ROUTE_TYPE_TRANSPORT_FLOOD
    from openhop_core.protocol.transport_keys import calc_transport_code, get_auto_key_for

    pkt = _make_advert("flood")
    apply_region_scope(pkt, "#germany")

    assert pkt.get_route_type() == ROUTE_TYPE_TRANSPORT_FLOOD
    assert pkt.has_transport_codes()
    expected_code = calc_transport_code(get_auto_key_for("#germany"), pkt)
    assert pkt.transport_codes[0] == expected_code
    assert pkt.transport_codes[1] == 0
    # The marker stops the dispatcher default scope from re-scoping the packet.
    assert pkt._flood_scope_applied is True


def test_apply_region_scope_leaves_direct_packets_alone() -> None:
    from openhop_core.protocol.constants import ROUTE_TYPE_DIRECT

    pkt = _make_advert("direct")
    apply_region_scope(pkt, "#germany")

    assert pkt.get_route_type() == ROUTE_TYPE_DIRECT
    assert not pkt.has_transport_codes()
    assert pkt.transport_codes == [0, 0]


def test_scoped_packet_survives_serialization_round_trip() -> None:
    from openhop_core.protocol.packet import Packet

    pkt = _make_advert("flood")
    apply_region_scope(pkt, "#germany")

    wire = pkt.write_to()
    parsed = Packet()
    parsed.read_from(bytes(wire))
    assert parsed.has_transport_codes()
    assert parsed.transport_codes == pkt.transport_codes
