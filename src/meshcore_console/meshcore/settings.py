from __future__ import annotations

from dataclasses import dataclass, replace


@dataclass(slots=True)
class MeshcoreSettings:
    # Public Info
    node_name: str = "uconsole-node"
    latitude: float = 0.0
    longitude: float = 0.0
    share_position: bool = False  # Share GPS position in adverts
    allow_telemetry: bool = True  # Allow telemetry requests from other nodes

    # Radio
    radio_preset: str = "meshcore-us"
    frequency: int = 910_525_000
    bandwidth: int = 62_500
    spreading_factor: int = 7
    coding_rate: int = 5
    tx_power: int = 22
    preamble_length: int = 17

    # Hardware (SPI/GPIO)
    bus_id: int = 1
    cs_id: int = 0
    cs_pin: int = -1
    reset_pin: int = 25
    busy_pin: int = 24
    irq_pin: int = 26
    txen_pin: int = -1
    rxen_pin: int = -1
    is_waveshare: bool = False
    use_dio2_rf: bool = True
    use_dio3_tcxo: bool = True

    def clone(self) -> "MeshcoreSettings":
        return replace(self)


RADIO_PRESETS: dict[str, dict[str, int]] = {
    "meshcore-us": {
        "frequency": 910_525_000,
        "bandwidth": 62_500,
        "spreading_factor": 7,
        "coding_rate": 5,
        "preamble_length": 17,
        "tx_power": 22,
    },
    "meshcore-eu": {
        "frequency": 869_525_000,
        "bandwidth": 250_000,
        "spreading_factor": 11,
        "coding_rate": 5,
        "preamble_length": 17,
        "tx_power": 22,
    },
}


def apply_preset(settings: MeshcoreSettings, preset: str) -> MeshcoreSettings:
    values = RADIO_PRESETS.get(preset)
    updated = settings.clone()
    updated.radio_preset = preset
    if values is None:
        return updated
    for key, value in values.items():
        setattr(updated, key, value)
    return updated
