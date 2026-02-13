from __future__ import annotations

from meshcore_console.core.services import MeshcoreService
from meshcore_console.core.types import MeshEventDict


class UiEventStore:
    def __init__(self, service: MeshcoreService) -> None:
        self._service = service
        self._events: list[tuple[int, MeshEventDict]] = []
        self._seq = 0
        self._max_events = 500

    def pump(self, limit: int = 100) -> list[MeshEventDict]:
        events = self._service.poll_events(limit=limit)
        for event in events:
            self._seq += 1
            self._events.append((self._seq, event))
        if len(self._events) > self._max_events:
            self._events = self._events[-self._max_events :]
        return events

    def recent(self, limit: int = 50) -> list[MeshEventDict]:
        if limit <= 0:
            return []
        return [event for _, event in self._events[-limit:]]

    def since(self, cursor: int, limit: int = 100) -> tuple[int, list[MeshEventDict]]:
        items = [event for seq, event in self._events if seq > cursor]
        if len(items) > limit:
            items = items[-limit:]
        return self._seq, items
