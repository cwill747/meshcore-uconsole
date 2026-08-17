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
