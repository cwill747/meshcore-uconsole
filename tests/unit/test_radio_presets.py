"""Radio presets match current MeshCore firmware."""

from __future__ import annotations

from meshcore_console.meshcore.settings import MeshcoreSettings, apply_preset


def test_eu_uk_narrow_preset() -> None:
    s = apply_preset(MeshcoreSettings(), "meshcore-eu-narrow")
    assert (s.frequency, s.bandwidth, s.spreading_factor, s.coding_rate) == (
        869_618_000,
        62_500,
        8,
        8,
    )


def test_preamble_follows_firmware_1_16() -> None:
    """32 symbols at SF <= 8, 16 above."""
    for preset in ("meshcore-us", "meshcore-eu-narrow", "meshcore-eu"):
        s = apply_preset(MeshcoreSettings(), preset)
        assert s.preamble_length == (32 if s.spreading_factor <= 8 else 16), preset
    assert MeshcoreSettings().preamble_length == 32


def test_radio_gets_meshcores_sync_word() -> None:
    """0x12, MeshCore's private sync word, in SX126x register form; openhop_core's
    own default is the public LoRaWAN 0x3444."""
    from meshcore_console.meshcore.config import HardwareRadioConfig
    from meshcore_console.meshcore.runtime import create_radio

    captured: dict = {}

    class FakeRadio:
        def __init__(self, sync_word: int = 0x3444, **kwargs) -> None:
            captured["sync_word"] = sync_word

    create_radio(FakeRadio, HardwareRadioConfig(), lambda _msg: None)
    assert captured["sync_word"] == 0x1424
