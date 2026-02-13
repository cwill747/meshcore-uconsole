from pathlib import Path

from meshcore_console.meshcore.client import MeshcoreClient
from meshcore_console.meshcore.config import runtime_config_from_settings
from meshcore_console.meshcore.settings import MeshcoreSettings
from meshcore_console.meshcore.settings_store import SettingsStore
from meshcore_console.mock import MockPyMCCoreSession


def test_client_updates_and_persists_settings(tmp_path: Path) -> None:
    store = SettingsStore(path=tmp_path / "settings.json")
    base_settings = MeshcoreSettings()
    client = MeshcoreClient(
        session=MockPyMCCoreSession(runtime_config_from_settings(base_settings)),
        require_pymc=False,
        settings_store=store,
    )

    settings = client.get_settings()
    settings.node_name = "applied-node"
    settings.share_position = True
    settings.latitude = 47.6
    settings.radio_preset = "meshcore-us"
    settings.frequency = 1

    client.update_settings(settings)
    updated = client.get_settings()
    assert updated.node_name == "applied-node"
    assert updated.share_position is True
    assert updated.latitude == 47.6
    # preset application should override custom frequency with preset value
    assert updated.frequency == 910_525_000

    reloaded = store.load()
    assert reloaded.node_name == "applied-node"
