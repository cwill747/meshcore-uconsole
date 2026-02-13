from __future__ import annotations

import os
from dataclasses import dataclass

from .settings import MeshcoreSettings


@dataclass(slots=True)
class RuntimeRadioConfig:
    node_name: str
    share_public_key: bool = True
    mesh_mode: bool = True
    encryption_enabled: bool = True
    hardware: "HardwareRadioConfig | None" = None


@dataclass(slots=True)
class HardwareRadioConfig:
    bus_id: int = 1
    cs_id: int = 0
    cs_pin: int = -1
    reset_pin: int = 25
    busy_pin: int = 24
    irq_pin: int = 26
    txen_pin: int = -1
    rxen_pin: int = -1
    frequency: int = 910_525_000
    tx_power: int = 22
    spreading_factor: int = 7
    bandwidth: int = 62_500
    coding_rate: int = 5
    preamble_length: int = 17
    is_waveshare: bool = False
    use_dio2_rf: bool = True
    use_dio3_tcxo: bool = True

    def to_log_string(self) -> str:
        return (
            f"bus_id={self.bus_id} cs_id={self.cs_id} cs_pin={self.cs_pin} "
            f"reset_pin={self.reset_pin} busy_pin={self.busy_pin} irq_pin={self.irq_pin} "
            f"txen_pin={self.txen_pin} rxen_pin={self.rxen_pin} "
            f"is_waveshare={self.is_waveshare} "
            f"use_dio2_rf={self.use_dio2_rf} use_dio3_tcxo={self.use_dio3_tcxo}"
        )


def load_runtime_config(node_name: str) -> RuntimeRadioConfig:
    return RuntimeRadioConfig(
        node_name=node_name,
        share_public_key=_env_bool("MESHCORE_SHARE_PUBLIC_KEY", True),
        mesh_mode=_env_bool("MESHCORE_MESH_MODE", True),
        encryption_enabled=_env_bool("MESHCORE_ENABLE_ENCRYPTION", True),
        hardware=load_hardware_config_from_env(),
    )


def _env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def load_hardware_config_from_env() -> HardwareRadioConfig:
    return HardwareRadioConfig(
        bus_id=_env_int("MESHCORE_BUS_ID", 1),
        cs_id=_env_int("MESHCORE_CS_ID", 0),
        cs_pin=_env_int("MESHCORE_CS_PIN", -1),
        reset_pin=_env_int("MESHCORE_RESET_PIN", 25),
        busy_pin=_env_int("MESHCORE_BUSY_PIN", 24),
        irq_pin=_env_int("MESHCORE_IRQ_PIN", 26),
        txen_pin=_env_int("MESHCORE_TXEN_PIN", -1),
        rxen_pin=_env_int("MESHCORE_RXEN_PIN", -1),
        frequency=_env_int("MESHCORE_FREQUENCY", 910_525_000),
        tx_power=_env_int("MESHCORE_TX_POWER", 22),
        spreading_factor=_env_int("MESHCORE_SPREADING_FACTOR", 7),
        bandwidth=_env_int("MESHCORE_BANDWIDTH", 62_500),
        coding_rate=_env_int("MESHCORE_CODING_RATE", 5),
        preamble_length=_env_int("MESHCORE_PREAMBLE_LENGTH", 17),
        is_waveshare=_env_bool("MESHCORE_IS_WAVESHARE", False),
        use_dio2_rf=_env_bool("MESHCORE_USE_DIO2_RF", True),
        use_dio3_tcxo=_env_bool("MESHCORE_USE_DIO3_TCXO", True),
    )


def runtime_config_from_settings(settings: MeshcoreSettings) -> RuntimeRadioConfig:
    hardware = HardwareRadioConfig(
        bus_id=settings.bus_id,
        cs_id=settings.cs_id,
        cs_pin=settings.cs_pin,
        reset_pin=settings.reset_pin,
        busy_pin=settings.busy_pin,
        irq_pin=settings.irq_pin,
        txen_pin=settings.txen_pin,
        rxen_pin=settings.rxen_pin,
        frequency=settings.frequency,
        tx_power=settings.tx_power,
        spreading_factor=settings.spreading_factor,
        bandwidth=settings.bandwidth,
        coding_rate=settings.coding_rate,
        preamble_length=settings.preamble_length,
        is_waveshare=settings.is_waveshare,
        use_dio2_rf=settings.use_dio2_rf,
        use_dio3_tcxo=settings.use_dio3_tcxo,
    )
    return RuntimeRadioConfig(
        node_name=settings.node_name,
        # These are always enabled (not user-configurable)
        share_public_key=True,
        mesh_mode=True,
        encryption_enabled=True,
        hardware=hardware,
    )
