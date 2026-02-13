from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from .paths import settings_path
from .settings import MeshcoreSettings


class SettingsStore:
    def __init__(self, path: Path | None = None) -> None:
        self._path = path if path is not None else settings_path()

    def load(self) -> MeshcoreSettings:
        if not self._path.exists():
            return MeshcoreSettings()
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except Exception:
            return MeshcoreSettings()

        defaults = asdict(MeshcoreSettings())
        for key, value in data.items():
            if key in defaults:
                defaults[key] = value
        return MeshcoreSettings(**defaults)

    def save(self, settings: MeshcoreSettings) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps(asdict(settings), indent=2, sort_keys=True), encoding="utf-8"
        )
