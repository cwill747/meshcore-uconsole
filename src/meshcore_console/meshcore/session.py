from __future__ import annotations

import asyncio
import inspect
import queue
from typing import Any, AsyncIterator

from meshcore_console.core.types import (
    EventServiceProtocol,
    EventSubscriberProtocol,
    LocalIdentityProtocol,
    LoggerCallback,
    MeshEventDict,
    MeshNodeProtocol,
    SendResultDict,
    SessionStatusDict,
    SX1262RadioProtocol,
)

from .channel_db import ChannelDatabase
from .config import RuntimeRadioConfig, load_hardware_config_from_env
from .event_bridge import attach_dispatcher_callbacks, attach_event_service_subscriber
from .operations import send_advert, send_group_text, send_text
from .runtime import create_mesh_node, create_radio, import_pymc_core


class PyMCCoreSession:
    """Async wrapper around pymc_core MeshNode lifecycle."""

    def __init__(self, config: RuntimeRadioConfig, logger: LoggerCallback | None = None) -> None:
        self.config = config
        self._logger = logger
        self._radio: SX1262RadioProtocol | None = None
        self._identity: LocalIdentityProtocol | None = None
        self._node: MeshNodeProtocol | None = None
        self._event_service: EventServiceProtocol | None = None
        self._event_subscriber: EventSubscriberProtocol | None = None
        self._node_task: asyncio.Task[None] | None = None
        self._event_queue: queue.Queue[MeshEventDict] = queue.Queue()
        self._channel_db = ChannelDatabase()

    def _log(self, message: str) -> None:
        if self._logger is not None:
            self._logger(message)

    def _emit(self, payload: MeshEventDict) -> None:
        self._event_queue.put_nowait(payload)

    async def _call_maybe_async(self, target: Any, name: str) -> None:
        fn = getattr(target, name, None)
        if not callable(fn):
            return
        try:
            result = fn()
            if inspect.isawaitable(result):
                await result
        except Exception:
            pass

    async def start(self) -> None:
        if self._node is not None and self._node_task is not None and not self._node_task.done():
            return

        self._log("importing pymc_core modules")
        SX1262Radio, EventService, EventSubscriber, MeshNode, LocalIdentity = import_pymc_core()
        hardware_config = self.config.hardware or load_hardware_config_from_env()

        self._log(f"radio config {hardware_config.to_log_string()}")
        self._log("creating SX1262Radio")
        self._radio = create_radio(
            SX1262Radio,
            hardware_config,
            self._log,
            mesh_mode=self.config.mesh_mode,
            encryption_enabled=self.config.encryption_enabled,
        )

        self._log("calling radio.begin()")
        self._radio.begin()
        self._log("radio.begin() returned")

        self._event_service = EventService()
        self._log("creating MeshNode")
        self._identity, self._node = create_mesh_node(
            MeshNode,
            LocalIdentity,
            radio=self._radio,
            event_service=self._event_service,
            node_name=self.config.node_name,
            node_config={"share_public_key": self.config.share_public_key},
            channel_db=self._channel_db,
        )

        self._node.set_event_service(self._event_service)

        self._event_subscriber = attach_event_service_subscriber(
            event_service=self._event_service,
            event_subscriber_base=EventSubscriber,
            emit=self._emit,
            logger=self._log,
        )
        attach_dispatcher_callbacks(node=self._node, emit=self._emit, logger=self._log)

        self._log("creating background task for node.start()")
        self._node_task = asyncio.create_task(self._node.start())
        await asyncio.sleep(0.2)
        if self._node_task.done():
            self._log("node.start() task exited early")
            self._node_task.result()
        self._log("node.start() task running")

    async def stop(self) -> None:
        if self._event_service is not None and self._event_subscriber is not None:
            try:
                self._event_service.unsubscribe_all(self._event_subscriber)
            except Exception:
                pass

        if self._node is not None:
            self._log("stopping node")
            result = self._node.stop()
            if inspect.isawaitable(result):
                await result

        if self._node_task is not None:
            if not self._node_task.done():
                self._log("cancelling node.start() task")
                self._node_task.cancel()
            try:
                await asyncio.wait_for(self._node_task, timeout=2.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass

        # Best-effort radio cleanup for reconnect stability on Linux GPIO/SPI.
        if self._radio is not None:
            for method in (
                "stop",
                "shutdown",
                "close",
                "cleanup",
                "deinit",
                "end",
                "_cleanup_interrupt_handling",
                "_cleanup_interrupt",
            ):
                await self._call_maybe_async(self._radio, method)
            lora = getattr(self._radio, "lora", None)
            if lora is not None:
                for method in ("close", "cleanup", "deinit", "end"):
                    await self._call_maybe_async(lora, method)

        self._radio = None
        self._identity = None
        self._node = None
        self._event_service = None
        self._event_subscriber = None
        self._node_task = None
        while not self._event_queue.empty():
            try:
                self._event_queue.get_nowait()
            except queue.Empty:
                break

    async def send_text(self, peer_name: str, message: str) -> object:
        if self._node is None:
            raise RuntimeError("Session is not started.")
        return await send_text(node=self._node, peer_name=peer_name, message=message)

    async def send_group_text(self, channel_name: str, message: str) -> object:
        """Broadcast a text message to a group/public channel."""
        if self._node is None:
            raise RuntimeError("Session is not started.")
        return await send_group_text(node=self._node, channel_name=channel_name, message=message)

    async def send_advert(
        self,
        *,
        name: str | None = None,
        lat: float = 0.0,
        lon: float = 0.0,
        route_type: str = "flood",
    ) -> SendResultDict:
        if self._node is None or self._identity is None:
            raise RuntimeError("Session is not started.")
        return await send_advert(
            node=self._node,
            identity=self._identity,
            default_name=self.config.node_name,
            emit=self._emit,
            logger=self._log,
            name=name,
            lat=lat,
            lon=lon,
            route_type=route_type,
        )

    async def listen_events(self) -> AsyncIterator[MeshEventDict]:
        if self._node is None:
            raise RuntimeError("Session is not started.")
        while True:
            yield await asyncio.to_thread(self._event_queue.get)

    def drain_events(self, max_items: int = 100) -> list[MeshEventDict]:
        items: list[MeshEventDict] = []
        for _ in range(max_items):
            try:
                items.append(self._event_queue.get_nowait())
            except queue.Empty:
                break
        return items

    def status(self) -> SessionStatusDict:
        return {
            "connected": self._node is not None,
            "node_name": self.config.node_name,
            "board": "hackergadgets-aio",
            "pymc_core_version": "1.0.7",
        }

    def get_public_key(self) -> str | None:
        """Return this node's public key as a hex string, or None if unavailable."""
        if self._identity is None:
            return None
        # pyMC_core LocalIdentity exposes public_key as bytes
        pk = getattr(self._identity, "public_key", None)
        if pk is None:
            return None
        if isinstance(pk, bytes):
            return pk.hex()
        if isinstance(pk, str):
            return pk
        return None
