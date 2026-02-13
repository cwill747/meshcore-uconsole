from pathlib import Path

import pytest


def test_desktop_entry_uses_stable_launcher() -> None:
    desktop = Path("packaging/deb/meshcore-console.desktop").read_text(encoding="utf-8")
    assert "Exec=meshcore-console" in desktop


def test_launcher_invokes_python_entrypoint() -> None:
    launcher_path = Path("packaging/deb/meshcore-console-launcher")
    if not launcher_path.exists():
        pytest.skip("launcher file not present (removed in favor of pyproject entrypoint)")
    launcher = launcher_path.read_text(encoding="utf-8")
    assert "python3 -m meshcore_console.main" in launcher
    assert "PYTHONPATH=src" in launcher
