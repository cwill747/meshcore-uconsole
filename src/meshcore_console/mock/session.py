"""Mock pyMC_core session for UI development."""

from __future__ import annotations

import asyncio
import queue
import random
from datetime import UTC, datetime
from typing import AsyncIterator, Callable

from meshcore_console.core.types import MeshEventDict, SendResultDict, SessionStatusDict
from meshcore_console.meshcore.config import RuntimeRadioConfig

from .data import MOCK_PEER_LOCATIONS, create_mock_packet_events


class MockPyMCCoreSession:
    """Low-level mock of the pyMC session API for UI development."""

    def __init__(self, config: RuntimeRadioConfig) -> None:
        self.config = config
        self._connected = True
        self._event_queue: queue.Queue[MeshEventDict] = queue.Queue()
        self._event_notify: Callable[[], None] | None = None
        self._advert_index = 0
        # Queue initial events immediately so packets appear in analyzer
        self._queue_initial_events()

    def set_event_notify(self, notify_fn: Callable[[], None]) -> None:
        self._event_notify = notify_fn

    def _emit_notify(self) -> None:
        if self._event_notify is not None:
            try:
                self._event_notify()
            except Exception:  # noqa: BLE001
                pass

    def _queue_initial_events(self) -> None:
        """Queue initial mock events for analyzer demo."""
        self._event_queue.put_nowait(
            {
                "type": "session_started",
                "data": {"node_name": self.config.node_name, "at": datetime.now(UTC).isoformat()},
            }
        )
        # Queue initial mock peer adverts with locations
        for name, peer_id, lat, lon, is_repeater in MOCK_PEER_LOCATIONS:
            self._queue_mock_advert(name, peer_id, lat, lon, is_repeater)
        # Queue mock packet events for analyzer demo
        for event in create_mock_packet_events():
            self._event_queue.put_nowait(event)

    async def start(self) -> None:
        self._connected = True

    async def stop(self) -> None:
        self._connected = False
        self._event_queue.put_nowait(
            {
                "type": "session_stopped",
                "data": {"node_name": self.config.node_name, "at": datetime.now(UTC).isoformat()},
            }
        )
        self._emit_notify()

    async def send_text(self, peer_name: str, message: str) -> MeshEventDict:
        self._event_queue.put_nowait(
            {
                "type": "mock_message_tx",
                "data": {"peer_name": peer_name, "message": message, "ok": True},
            }
        )
        self._emit_notify()
        return {"ok": True}

    async def send_group_text(self, channel_name: str, message: str) -> MeshEventDict:
        """Mock broadcast to a group/public channel."""
        self._event_queue.put_nowait(
            {
                "type": "mock_group_message_tx",
                "data": {"channel_name": channel_name, "message": message, "ok": True},
            }
        )
        self._emit_notify()
        return {"ok": True}

    async def send_advert(
        self,
        *,
        name: str | None = None,
        lat: float = 0.0,
        lon: float = 0.0,
        route_type: str = "flood",
    ) -> SendResultDict:
        advert_name = name or self.config.node_name
        payload = {
            "name": advert_name,
            "lat": lat,
            "lon": lon,
            "route_type": route_type,
            "success": True,
            "tx_metadata": {"mock": True},
            "dispatcher_result": True,
        }
        self._event_queue.put_nowait({"type": "advert_sent", "data": payload})
        self._emit_notify()
        return {
            "success": True,
            "tx_metadata": payload["tx_metadata"],
            "dispatcher_result": True,
        }

    async def listen_events(self) -> AsyncIterator[MeshEventDict]:
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
            "connected": self._connected,
            "node_name": self.config.node_name,
            "board": "mock",
            "pymc_core_version": "mock",
        }

    def get_public_key(self) -> str | None:
        """Return a mock public key for testing."""
        return "6b547fd13630e0f7a6b167df23b9876543210abcdef0123456789abcdef0a619"

    def _queue_mock_advert(
        self,
        name: str,
        peer_id: str,
        lat: float,
        lon: float,
        is_repeater: bool,
    ) -> None:
        """Queue a mock ADVERT event with GPS coordinates.

        ADVERT packets (type 4) advertise a node's identity, location, and public key.
        """
        # Add some random jitter to coordinates (simulate GPS drift)
        lat_jitter = random.uniform(-0.001, 0.001)
        lon_jitter = random.uniform(-0.001, 0.001)

        rssi = random.randint(-90, -50)
        snr = random.uniform(-5.0, 12.0)
        path_hops = [] if is_repeater else ["relay-001"]

        # ADV_TYPE_REPEATER = 2 per pyMC_core; client nodes are 0.
        advert_type = 2 if is_repeater else 0

        event = {
            "type": "packet",
            "received_at": datetime.now(UTC).isoformat(),
            "data": {
                "payload_type": 4,  # ADVERT
                "payload_type_name": "ADVERT",
                "route_type": 1,  # FLOOD
                "route_type_name": "FLOOD",
                "sender_name": name,
                "advert_name": name,
                "sender_id": peer_id,
                "peer_id": peer_id,
                "advert_type": advert_type,
                "advert_lat": lat + lat_jitter,
                "advert_lon": lon + lon_jitter,
                "rssi": rssi,
                "snr": round(snr, 2),
                "path_len": len(path_hops),
                "path_hops": path_hops,
                "sender_pubkey": f"{peer_id:0<64}",  # Fake 64-char public key
                "payload_hex": f"{peer_id:0<32}",  # Fake payload
                "packet_hash": f"{peer_id[:12].upper()}",
            },
        }
        self._event_queue.put_nowait(event)
        self._emit_notify()

    def schedule_mock_advert(self) -> None:
        """Schedule an additional mock advert (for periodic updates)."""
        if not MOCK_PEER_LOCATIONS:
            return

        # Cycle through peers
        name, peer_id, lat, lon, is_repeater = MOCK_PEER_LOCATIONS[
            self._advert_index % len(MOCK_PEER_LOCATIONS)
        ]
        self._advert_index += 1
        self._queue_mock_advert(name, peer_id, lat, lon, is_repeater)
