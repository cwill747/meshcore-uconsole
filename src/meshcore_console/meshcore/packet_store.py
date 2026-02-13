from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from meshcore_console.core.types import MeshEventDict

from .paths import packets_path

MAX_PACKETS = 1000


class PacketStore:
    """Persistent packet history stored in XDG_STATE_HOME."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path if path is not None else packets_path()
        self._packets: list[MeshEventDict] = []
        print(f"[PacketStore] initialized with path: {self._path}", file=sys.stderr)
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            print(f"[PacketStore] no existing file at {self._path}", file=sys.stderr)
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            packets = data.get("packets", [])
            if isinstance(packets, list):
                self._packets = packets[-MAX_PACKETS:]
            print(
                f"[PacketStore] loaded {len(self._packets)} packets from {self._path}",
                file=sys.stderr,
            )
        except Exception as e:
            print(f"[PacketStore] load error: {e}", file=sys.stderr)

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            data = {"packets": self._packets}
            self._path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception as e:
            print(f"[PacketStore] save error: {e}", file=sys.stderr)
            raise

    def append(self, packet_data: MeshEventDict) -> None:
        """Add a packet to the store, maintaining max size."""
        # Add timestamp if not present
        if "received_at" not in packet_data:
            packet_data["received_at"] = datetime.now(UTC).isoformat()
        self._packets.append(packet_data)
        if len(self._packets) > MAX_PACKETS:
            self._packets = self._packets[-MAX_PACKETS:]
        self._save()

    def get_all(self) -> list[MeshEventDict]:
        """Return all stored packets."""
        return list(self._packets)

    def get_recent(self, limit: int = 100) -> list[MeshEventDict]:
        """Return the most recent packets."""
        if limit <= 0:
            return []
        return self._packets[-limit:]

    def clear(self) -> None:
        """Clear all stored packets."""
        self._packets = []
        self._save()

    def __len__(self) -> int:
        return len(self._packets)
