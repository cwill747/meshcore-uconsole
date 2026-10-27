"""The node's own public key is its Ed25519 identity key."""

from __future__ import annotations

from openhop_core.protocol.identity import LocalIdentity

from meshcore_console.meshcore.config import runtime_config_from_settings
from meshcore_console.meshcore.session import OpenHopCoreSession
from meshcore_console.meshcore.settings import MeshcoreSettings


def test_own_public_key_is_the_identity_key_not_the_ecdh_key(tmp_path, monkeypatch) -> None:
    """Adverts, path hashes and every MeshCore address use the Ed25519 key;
    get_shared_public_key() is the X25519 key used only for ECDH."""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    session = OpenHopCoreSession(runtime_config_from_settings(MeshcoreSettings()))
    identity = LocalIdentity()
    session._identity = identity

    assert session.get_public_key() == identity.get_public_key().hex()
    assert session.get_public_key() != identity.get_shared_public_key().hex()
