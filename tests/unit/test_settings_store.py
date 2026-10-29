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


def test_log_level_round_trip(tmp_path) -> None:
    conn = open_db(str(tmp_path / "test.db"))
    store = SettingsStore(conn)
    settings = MeshcoreSettings(log_level="DEBUG")
    store.save(settings)

    loaded = store.load()
    assert loaded.log_level == "DEBUG"
    conn.close()


def test_radio_error_toasts_default_to_disabled_and_round_trip(tmp_path) -> None:
    conn = open_db(str(tmp_path / "test.db"))
    store = SettingsStore(conn)

    assert MeshcoreSettings().show_radio_error_toasts is False

    # Persisted False must survive the store's string round-trip, not just the
    # dataclass default that load() falls back to on an empty table.
    store.save(MeshcoreSettings())
    assert store.load().show_radio_error_toasts is False

    store.save(MeshcoreSettings(show_radio_error_toasts=True))
    assert store.load().show_radio_error_toasts is True
    conn.close()


def test_flood_region_defaults_empty_and_round_trips(tmp_path) -> None:
    conn = open_db(str(tmp_path / "test.db"))
    store = SettingsStore(conn)

    assert MeshcoreSettings().flood_region == ""

    store.save(MeshcoreSettings(flood_region="#germany"))
    assert store.load().flood_region == "#germany"
    conn.close()


def test_a_bad_row_falls_back_to_its_default(tmp_path) -> None:
    from meshcore_console.meshcore.db import open_db
    from meshcore_console.meshcore.settings import MeshcoreSettings
    from meshcore_console.meshcore.settings_store import SettingsStore

    conn = open_db(str(tmp_path / "s.db"))
    store = SettingsStore(conn)
    store.save(MeshcoreSettings(node_name="DF9ES"))
    conn.execute("UPDATE settings SET value = 'abc' WHERE key = 'frequency'")
    conn.commit()

    settings = store.load()
    assert settings.frequency == MeshcoreSettings().frequency
    assert settings.node_name == "DF9ES"


def test_map_tile_downloads_default_on_and_persist(tmp_path) -> None:
    from meshcore_console.meshcore.db import open_db
    from meshcore_console.meshcore.settings import MeshcoreSettings
    from meshcore_console.meshcore.settings_store import SettingsStore

    assert MeshcoreSettings().online_map_tiles is True  # unchanged default behaviour
    store = SettingsStore(open_db(str(tmp_path / "t.db")))
    store.save(MeshcoreSettings(online_map_tiles=False))
    assert store.load().online_map_tiles is False
