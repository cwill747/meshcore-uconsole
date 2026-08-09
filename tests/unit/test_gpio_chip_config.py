"""GPIO chip / backend / enable-pin plumbing (#85).

The 40-pin header is on /dev/gpiochip0 for CM4, but /dev/gpiochip15 on CM5 and
Pi 5 kernels, so the chip number has to be configurable end to end.
"""

from __future__ import annotations

from meshcore_console.meshcore.config import (
    HardwareRadioConfig,
    load_hardware_config_from_env,
    parse_pin_list,
    runtime_config_from_settings,
)
from meshcore_console.meshcore.runtime import create_radio
from meshcore_console.meshcore.settings import MeshcoreSettings, apply_hardware_preset
from meshcore_console.meshcore.settings_store import SettingsStore


class _FakeRadio:
    """Stands in for SX1262Radio, capturing constructor kwargs."""

    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs


def _make_radio(config: HardwareRadioConfig) -> _FakeRadio:
    return create_radio(_FakeRadio, config, lambda _msg: None)  # type: ignore[arg-type]


# --- parse_pin_list --------------------------------------------------------


def test_parse_pin_list_handles_spacing_blanks_and_dupes() -> None:
    assert parse_pin_list("16, 17") == (16, 17)
    assert parse_pin_list("16,,17,") == (16, 17)
    assert parse_pin_list("17,16,17") == (17, 16)
    assert parse_pin_list("") == ()
    assert parse_pin_list("   ") == ()


def test_parse_pin_list_drops_junk_and_negatives() -> None:
    assert parse_pin_list("16,abc,17") == (16, 17)
    assert parse_pin_list("-1,16") == (16,)


# --- defaults preserve existing behaviour ----------------------------------


def test_defaults_are_unchanged_for_cm4() -> None:
    config = HardwareRadioConfig()
    assert config.gpio_chip == 0
    assert config.use_gpiod_backend is False
    assert config.en_pins == ()


def test_hardware_presets_do_not_clobber_gpio_chip() -> None:
    """gpio_chip is a property of the SoC/kernel, not of the radio board."""
    settings = MeshcoreSettings()
    settings.gpio_chip = 15
    settings.use_gpiod_backend = True
    settings.en_pins = "16"

    for preset in ("uconsole", "waveshare", "meshadv-mini"):
        updated = apply_hardware_preset(settings, preset)
        assert updated.gpio_chip == 15, preset
        assert updated.use_gpiod_backend is True, preset
        assert updated.en_pins == "16", preset


# --- settings -> radio kwargs ----------------------------------------------


def test_create_radio_passes_gpio_chip_through() -> None:
    radio = _make_radio(HardwareRadioConfig(gpio_chip=15))
    assert radio.kwargs["gpio_chip"] == 15


def test_create_radio_passes_backend_and_en_pins() -> None:
    radio = _make_radio(
        HardwareRadioConfig(use_gpiod_backend=True, en_pins=(16, 17)),
    )
    assert radio.kwargs["use_gpiod_backend"] is True
    assert radio.kwargs["en_pins"] == [16, 17]


def test_create_radio_sends_empty_en_pins_when_unset() -> None:
    radio = _make_radio(HardwareRadioConfig())
    assert radio.kwargs["en_pins"] == []


def test_settings_reach_the_radio_config() -> None:
    settings = MeshcoreSettings()
    settings.gpio_chip = 15
    settings.use_gpiod_backend = True
    settings.en_pins = "16, 17"

    hardware = runtime_config_from_settings(settings).hardware
    assert hardware is not None
    assert hardware.gpio_chip == 15
    assert hardware.use_gpiod_backend is True
    assert hardware.en_pins == (16, 17)

    radio = _make_radio(hardware)
    assert radio.kwargs["gpio_chip"] == 15
    assert radio.kwargs["en_pins"] == [16, 17]


def test_gpio_chip_appears_in_log_string() -> None:
    line = HardwareRadioConfig(gpio_chip=15, en_pins=(16,)).to_log_string()
    assert "gpio_chip=15" in line
    assert "en_pins=[16]" in line


# --- env overrides ---------------------------------------------------------


def test_env_overrides(monkeypatch) -> None:
    monkeypatch.setenv("MESHCORE_GPIO_CHIP", "15")
    monkeypatch.setenv("MESHCORE_USE_GPIOD_BACKEND", "1")
    monkeypatch.setenv("MESHCORE_EN_PINS", "16,17")

    config = load_hardware_config_from_env()
    assert config.gpio_chip == 15
    assert config.use_gpiod_backend is True
    assert config.en_pins == (16, 17)


def test_env_defaults_when_unset(monkeypatch) -> None:
    monkeypatch.delenv("MESHCORE_GPIO_CHIP", raising=False)
    monkeypatch.delenv("MESHCORE_USE_GPIOD_BACKEND", raising=False)
    monkeypatch.delenv("MESHCORE_EN_PINS", raising=False)

    config = load_hardware_config_from_env()
    assert config.gpio_chip == 0
    assert config.use_gpiod_backend is False
    assert config.en_pins == ()


# --- persistence -----------------------------------------------------------


def test_settings_round_trip_through_store(tmp_path) -> None:
    from meshcore_console.meshcore.db import open_db

    conn = open_db(str(tmp_path / "test.db"))
    store = SettingsStore(conn)

    settings = MeshcoreSettings()
    settings.gpio_chip = 15
    settings.use_gpiod_backend = True
    settings.en_pins = "16,17"
    store.save(settings)

    loaded = store.load()
    assert loaded.gpio_chip == 15
    assert loaded.use_gpiod_backend is True
    assert loaded.en_pins == "16,17"
    conn.close()
