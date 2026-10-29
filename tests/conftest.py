"""Shared test fixtures."""

from __future__ import annotations

import pytest

from meshcore_console.meshcore.config import _HARDWARE_ENV_OVERRIDES


@pytest.fixture(autouse=True)
def _clear_hardware_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove the MESHCORE_* hardware overrides for every test.

    The environment now wins over the persisted settings (#85), so a developer
    shell that exports MESHCORE_GPIO_CHIP would otherwise change the result of
    any test that builds a radio config.
    """
    for _field, name, _parse in _HARDWARE_ENV_OVERRIDES:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def _isolate_user_dirs(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch):
    """Point the app's database, identity, config and log at a temp dir.

    MeshcoreClient() and OpenHopCoreSession() open the default database
    (XDG_STATE_HOME) even when a test injects its own stores, and the log lives
    in the same directory, so without this a test run writes into the
    developer's real ~/.local/state/meshcore-uconsole.
    """
    root = tmp_path_factory.mktemp("xdg")
    for var, sub in (
        ("XDG_STATE_HOME", "state"),
        ("XDG_DATA_HOME", "data"),
        ("XDG_CONFIG_HOME", "config"),
    ):
        monkeypatch.setenv(var, str(root / sub))
