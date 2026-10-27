"""The headless CLI radios with the saved settings, like the GTK app."""

from __future__ import annotations

from meshcore_console.meshcore.db import open_db
from meshcore_console.meshcore.settings import MeshcoreSettings
from meshcore_console.meshcore.settings_store import SettingsStore
from meshcore_console.radio_cli import load_saved_runtime_config


def save(**values) -> None:
    conn = open_db()
    settings = MeshcoreSettings()
    for key, value in values.items():
        setattr(settings, key, value)
    SettingsStore(conn).save(settings)
    conn.close()


def test_cli_uses_saved_radio_and_name() -> None:
    save(node_name="DF9ES", frequency=869_618_000, spreading_factor=8, coding_rate=8, gpio_chip=15)

    config = load_saved_runtime_config()

    assert config.node_name == "DF9ES"
    hw = config.hardware
    assert (hw.frequency, hw.spreading_factor, hw.coding_rate, hw.gpio_chip) == (
        869_618_000,
        8,
        8,
        15,
    )


def test_cli_name_override() -> None:
    save(node_name="DF9ES")
    assert load_saved_runtime_config("bench").node_name == "bench"
