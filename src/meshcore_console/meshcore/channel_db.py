from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from .paths import channels_path


# Default Public channel secret - shared across all MeshCore devices
PUBLIC_CHANNEL_SECRET = "8b3387e9c5cdea6ac9e5edbaa115cd72"


@dataclass
class ChannelConfig:
    name: str
    secret: str


class ChannelDatabase:
    """Persistent channel database for group message decryption."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path if path is not None else channels_path()
        self._channels: dict[str, ChannelConfig] = {}
        self._load()
        # Always ensure Public channel exists
        if "public" not in self._channels:
            self.add_channel("Public", PUBLIC_CHANNEL_SECRET)

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            for item in data.get("channels", []):
                name = item.get("name", "")
                secret = item.get("secret", "")
                if name and secret:
                    self._channels[name.lower()] = ChannelConfig(name=name, secret=secret)
        except Exception:
            pass

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        data = {"channels": [asdict(ch) for ch in self._channels.values()]}
        self._path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")

    def add_channel(self, name: str, secret: str) -> None:
        self._channels[name.lower()] = ChannelConfig(name=name, secret=secret)
        self._save()

    def remove_channel(self, name: str) -> None:
        if self._channels.pop(name.lower(), None) is not None:
            self._save()

    def get_channels(self) -> list[dict[str, str]]:
        """Return channels in the format expected by pymc_core GroupTextHandler."""
        return [{"name": ch.name, "secret": ch.secret} for ch in self._channels.values()]

    def get_channel(self, name: str) -> dict[str, str] | None:
        ch = self._channels.get(name.lower())
        if ch is None:
            return None
        return {"name": ch.name, "secret": ch.secret}
