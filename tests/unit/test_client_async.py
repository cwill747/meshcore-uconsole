"""Blocking radio calls always end: on disconnect, timeout or sys.exit()."""

from __future__ import annotations

import asyncio
import threading
import time

import pytest

from meshcore_console.meshcore.db import open_db


def _client(tmp_path, monkeypatch):
    from meshcore_console.meshcore import client as client_mod
    from meshcore_console.meshcore.client import MeshcoreClient
    from meshcore_console.meshcore.config import runtime_config_from_settings
    from meshcore_console.meshcore.settings import MeshcoreSettings
    from meshcore_console.mock import MockOpenHopCoreSession

    db_conn = open_db(str(tmp_path / "test.db"))
    monkeypatch.setattr(client_mod, "open_db", lambda *a, **k: db_conn)
    return MeshcoreClient(
        session=MockOpenHopCoreSession(runtime_config_from_settings(MeshcoreSettings())),
        require_openhop=False,
    )


def test_disconnect_releases_a_caller_waiting_on_the_radio(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    outcome: list[BaseException] = []

    def worker() -> None:
        try:
            client._run_async(asyncio.sleep(30), timeout=20)
        except BaseException as exc:  # noqa: BLE001
            outcome.append(exc)

    thread = threading.Thread(target=worker)
    thread.start()
    time.sleep(0.2)
    started = time.monotonic()
    client._shutdown_loop()
    thread.join(timeout=5)

    assert not thread.is_alive() and time.monotonic() - started < 5
    assert isinstance(outcome[0], RuntimeError)


def test_sys_exit_in_a_radio_call_does_not_kill_the_loop(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)

    async def gpio_busy() -> None:
        raise SystemExit(1)

    with pytest.raises(RuntimeError, match="exit code 1"):
        client._run_async(gpio_busy())
    assert client._run_async(asyncio.sleep(0, result="alive")) == "alive"
    client._shutdown_loop()


def test_inner_timeout_still_applies(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    with pytest.raises(TimeoutError):
        client._run_async(asyncio.sleep(5), timeout=0.1)
    client._shutdown_loop()
