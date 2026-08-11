from __future__ import annotations

import os
from dataclasses import dataclass, replace

from .settings import MeshcoreSettings


@dataclass(slots=True)
class RuntimeRadioConfig:
    node_name: str
    share_public_key: bool = True
    path_hash_mode: int = 0
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
    gpio_chip: int = 0
    use_gpiod_backend: bool = False
    en_pins: tuple[int, ...] = ()

    def to_log_string(self) -> str:
        return (
            f"bus_id={self.bus_id} cs_id={self.cs_id} cs_pin={self.cs_pin} "
            f"reset_pin={self.reset_pin} busy_pin={self.busy_pin} irq_pin={self.irq_pin} "
            f"txen_pin={self.txen_pin} rxen_pin={self.rxen_pin} "
            f"is_waveshare={self.is_waveshare} "
            f"use_dio2_rf={self.use_dio2_rf} use_dio3_tcxo={self.use_dio3_tcxo} "
            f"gpio_chip={self.gpio_chip} use_gpiod_backend={self.use_gpiod_backend} "
            f"en_pins={list(self.en_pins)}"
        )


def load_runtime_config(node_name: str) -> RuntimeRadioConfig:
    return RuntimeRadioConfig(
        node_name=node_name,
        share_public_key=_env_bool("MESHCORE_SHARE_PUBLIC_KEY", True),
        hardware=load_hardware_config_from_env(),
    )


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return _parse_bool(value)


def parse_pin_list(raw: str) -> tuple[int, ...]:
    """Parse a comma-separated GPIO pin list, dropping blanks and non-numbers."""
    pins: list[int] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            pin = int(part)
        except ValueError:
            continue
        if pin >= 0 and pin not in pins:
            pins.append(pin)
    return tuple(pins)


def _parse_int(raw: str) -> int | None:
    try:
        return int(raw)
    except ValueError:
        return None


def _parse_bool(raw: str) -> bool:
    return raw.strip().lower() in {"1", "true", "yes", "on"}


# Every hardware field the environment can override, with the parser for it.
# One table keeps the CLI, the GTK app and `doctor` on the same values (#85).
_HARDWARE_ENV_OVERRIDES: tuple[tuple[str, str, object], ...] = (
    ("bus_id", "MESHCORE_BUS_ID", _parse_int),
    ("cs_id", "MESHCORE_CS_ID", _parse_int),
    ("cs_pin", "MESHCORE_CS_PIN", _parse_int),
    ("reset_pin", "MESHCORE_RESET_PIN", _parse_int),
    ("busy_pin", "MESHCORE_BUSY_PIN", _parse_int),
    ("irq_pin", "MESHCORE_IRQ_PIN", _parse_int),
    ("txen_pin", "MESHCORE_TXEN_PIN", _parse_int),
    ("rxen_pin", "MESHCORE_RXEN_PIN", _parse_int),
    ("frequency", "MESHCORE_FREQUENCY", _parse_int),
    ("tx_power", "MESHCORE_TX_POWER", _parse_int),
    ("spreading_factor", "MESHCORE_SPREADING_FACTOR", _parse_int),
    ("bandwidth", "MESHCORE_BANDWIDTH", _parse_int),
    ("coding_rate", "MESHCORE_CODING_RATE", _parse_int),
    ("preamble_length", "MESHCORE_PREAMBLE_LENGTH", _parse_int),
    ("is_waveshare", "MESHCORE_IS_WAVESHARE", _parse_bool),
    ("use_dio2_rf", "MESHCORE_USE_DIO2_RF", _parse_bool),
    ("use_dio3_tcxo", "MESHCORE_USE_DIO3_TCXO", _parse_bool),
    ("gpio_chip", "MESHCORE_GPIO_CHIP", _parse_int),
    ("use_gpiod_backend", "MESHCORE_USE_GPIOD_BACKEND", _parse_bool),
    ("en_pins", "MESHCORE_EN_PINS", parse_pin_list),
)


def hardware_env_overrides() -> dict[str, str]:
    """Return the hardware env vars that are set, as {field name: env var}."""
    return {
        field: name
        for field, name, _parse in _HARDWARE_ENV_OVERRIDES
        if os.environ.get(name) is not None
    }


def apply_hardware_env_overrides(base: HardwareRadioConfig) -> HardwareRadioConfig:
    """Return *base* with the MESHCORE_* environment overrides applied.

    The environment wins over the persisted settings. A saved configuration
    that stops the radio from starting — the wrong GPIO chip, for example —
    must stay recoverable from the command line, because the settings screen
    is behind the radio connection (#85).
    """
    out = replace(base)
    for field, name, parse in _HARDWARE_ENV_OVERRIDES:
        raw = os.environ.get(name)
        if raw is None:
            continue
        value = parse(raw)  # type: ignore[operator]
        if value is None:
            continue
        setattr(out, field, value)
    return out


def load_hardware_config_from_env() -> HardwareRadioConfig:
    """Build a hardware config from the environment, over the built-in defaults."""
    return apply_hardware_env_overrides(HardwareRadioConfig())


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
        gpio_chip=settings.gpio_chip,
        use_gpiod_backend=settings.use_gpiod_backend,
        en_pins=parse_pin_list(settings.en_pins),
    )
    hardware = apply_hardware_env_overrides(hardware)
    return RuntimeRadioConfig(
        node_name=settings.node_name,
        share_public_key=True,
        path_hash_mode=settings.path_hash_mode,
        hardware=hardware,
    )
