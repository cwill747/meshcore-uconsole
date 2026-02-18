from __future__ import annotations

import logging
from collections import deque

from gi.repository import GLib, GObject

from meshcore_console.core.services import MeshcoreService
from meshcore_console.core.types import MeshEventDict

logger = logging.getLogger(__name__)


class UiEventStore(GObject.Object):
    __gsignals__ = {
        "events-available": (GObject.SignalFlags.RUN_LAST, None, ()),
    }

    def __init__(self, service: MeshcoreService) -> None:
        super().__init__()
        self._service = service
        self._events: deque[tuple[int, MeshEventDict]] = deque(maxlen=500)
        self._seq = 0
        self._pump_scheduled = False

    def schedule_pump(self) -> None:
        """Thread-safe: schedule a pump on the main thread via GLib.idle_add."""
        if self._pump_scheduled:
            return
        self._pump_scheduled = True
        GLib.idle_add(self._do_pump)

    def _do_pump(self) -> bool:
        """Drain the queue and emit signal. Called as a GLib idle callback."""
        self._pump_scheduled = False
        events = self._service.poll_events(limit=100)
        for event in events:
            self._seq += 1
            self._events.append((self._seq, event))
        if events:
            self.emit("events-available")
        return False  # One-shot idle

    def pump(self, limit: int = 100) -> list[MeshEventDict]:
        """Synchronous pump — drains queue, emits signal if events found."""
        self._pump_scheduled = False
        events = self._service.poll_events(limit=limit)
        for event in events:
            self._seq += 1
            self._events.append((self._seq, event))
        if events:
            self.emit("events-available")
        return events

    def recent(self, limit: int = 50) -> list[MeshEventDict]:
        if limit <= 0:
            return []
        return [event for _, event in list(self._events)[-limit:]]

    def since(self, cursor: int, limit: int = 100) -> tuple[int, list[MeshEventDict]]:
        items = [event for seq, event in self._events if seq > cursor]
        if len(items) > limit:
            items = items[-limit:]
        return self._seq, items
