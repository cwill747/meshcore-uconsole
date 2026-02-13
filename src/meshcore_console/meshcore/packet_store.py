from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

from meshcore_console.core.types import MeshEventDict

from .paths import packets_path

MAX_PACKETS = 1000


class PacketStore:
    """Persistent packet history stored in XDG_STATE_HOME."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path if path is not None else packets_path()
        self._packets: list[MeshEventDict] = []
        self._dirty = False
        logger.debug("PacketStore initialized with path: %s", self._path)
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            logger.debug("PacketStore: no existing file at %s", self._path)
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            packets = data.get("packets", [])
            if isinstance(packets, list):
                self._packets = packets[-MAX_PACKETS:]
            logger.debug("PacketStore: loaded %d packets from %s", len(self._packets), self._path)
        except Exception as e:
            logger.warning("PacketStore load error: %s", e)

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            data = {"packets": self._packets}
            self._path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception as e:
            logger.warning("PacketStore save error: %s", e)
            raise

    def append(self, packet_data: MeshEventDict) -> None:
        """Add a packet to the store, maintaining max size."""
        # Add timestamp if not present
        if "received_at" not in packet_data:
            packet_data["received_at"] = datetime.now(UTC).isoformat()
        self._packets.append(packet_data)
        if len(self._packets) > MAX_PACKETS:
            self._packets = self._packets[-MAX_PACKETS:]
        self._dirty = True

    def get_all(self) -> list[MeshEventDict]:
        """Return all stored packets."""
        return list(self._packets)

    def get_recent(self, limit: int = 100) -> list[MeshEventDict]:
        """Return the most recent packets."""
        if limit <= 0:
            return []
        return self._packets[-limit:]

    def flush_if_dirty(self) -> None:
        """Write to disk only if data has changed since last save."""
        if self._dirty:
            self._save()
            self._dirty = False

    def clear(self) -> None:
        """Clear all stored packets."""
        self._packets = []
        self._dirty = False
        self._save()

    def __len__(self) -> int:
        return len(self._packets)
