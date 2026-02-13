from __future__ import annotations

import inspect
from typing import Any

from meshcore_console.core.types import (
    EventServiceProtocol,
    EventSubscriberProtocol,
    LocalIdentityProtocol,
    LoggerCallback,
    MeshNodeProtocol,
    SX1262RadioProtocol,
)

from .config import HardwareRadioConfig
from .paths import identity_key_path


def import_pymc_core() -> tuple[
    type[SX1262RadioProtocol],
    type[EventServiceProtocol],
    type[EventSubscriberProtocol],
    type[MeshNodeProtocol],
    type[LocalIdentityProtocol],
]:
    try:
        from pymc_core.hardware.sx1262_wrapper import SX1262Radio
        from pymc_core.node.events import EventService, EventSubscriber
        from pymc_core.node.node import MeshNode
        from pymc_core.protocol.identity import LocalIdentity
    except ImportError as exc:
        raise RuntimeError("pymc_core is not available. Run `uv sync` in this project.") from exc
    return SX1262Radio, EventService, EventSubscriber, MeshNode, LocalIdentity  # type: ignore[return-value]


def create_radio(
    sx1262_radio_type: type[SX1262RadioProtocol],
    config: HardwareRadioConfig,
    logger: LoggerCallback,
) -> SX1262RadioProtocol:
    radio_kwargs: dict[str, Any] = {
        "bus_id": config.bus_id,
        "cs_id": config.cs_id,
        "cs_pin": config.cs_pin,
        "reset_pin": config.reset_pin,
        "busy_pin": config.busy_pin,
        "irq_pin": config.irq_pin,
        "txen_pin": config.txen_pin,
        "rxen_pin": config.rxen_pin,
        "frequency": config.frequency,
        "tx_power": config.tx_power,
        "spreading_factor": config.spreading_factor,
        "bandwidth": config.bandwidth,
        "coding_rate": config.coding_rate,
        "preamble_length": config.preamble_length,
        "is_waveshare": config.is_waveshare,
    }

    signature = inspect.signature(sx1262_radio_type)
    if "use_dio2_rf" in signature.parameters:
        radio_kwargs["use_dio2_rf"] = config.use_dio2_rf
    else:
        logger("SX1262Radio does not support use_dio2_rf; skipping")
    if "use_dio3_tcxo" in signature.parameters:
        radio_kwargs["use_dio3_tcxo"] = config.use_dio3_tcxo
    else:
        logger("SX1262Radio does not support use_dio3_tcxo; skipping")

    radio = sx1262_radio_type(**radio_kwargs)
    return radio


def create_mesh_node(
    mesh_node_type: type[MeshNodeProtocol],
    local_identity_type: type[LocalIdentityProtocol],
    *,
    radio: SX1262RadioProtocol,
    event_service: EventServiceProtocol,
    node_name: str,
    node_config: dict[str, Any] | None = None,
    channel_db: object | None = None,
    contacts: object | None = None,
) -> tuple[LocalIdentityProtocol, MeshNodeProtocol]:
    key_path = identity_key_path()
    if key_path.exists():
        seed = key_path.read_bytes()
    else:
        # Generate a new identity and persist the seed for future sessions
        tmp = local_identity_type()
        seed = tmp.get_signing_key_bytes()
        key_path.parent.mkdir(parents=True, exist_ok=True)
        key_path.write_bytes(seed)
    identity = local_identity_type(seed)
    config_payload = {"node": {"name": node_name}}
    if node_config:
        config_payload["node"].update(node_config)
    # pyMC_core constructor kwargs not in Protocol (which only defines methods)
    node = mesh_node_type(  # type: ignore[call-arg]
        radio=radio,
        local_identity=identity,
        config=config_payload,
        event_service=event_service,
        channel_db=channel_db,
        contacts=contacts,
    )
    return identity, node
