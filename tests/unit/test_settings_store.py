from meshcore_console.meshcore.db import open_db
from meshcore_console.meshcore.settings import MeshcoreSettings
from meshcore_console.meshcore.settings_store import SettingsStore


def test_settings_store_round_trip(tmp_path) -> None:
    conn = open_db(str(tmp_path / "test.db"))
    store = SettingsStore(conn)
    settings = MeshcoreSettings(
        node_name="unit-node", latitude=45.5, longitude=-122.6, frequency=915_000_000
    )
    store.save(settings)

    loaded = store.load()
    assert loaded.node_name == "unit-node"
    assert loaded.latitude == 45.5
    assert loaded.longitude == -122.6
    assert loaded.frequency == 915_000_000
    conn.close()
