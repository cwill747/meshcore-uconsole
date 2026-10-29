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


def test_concurrent_connects_start_the_radio_once(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    client._openhop_available = True
    client._config.hardware = None  # no preflight probing of real GPIO
    starts: list[int] = []
    original_start = client._session.start

    async def slow_start() -> None:
        starts.append(1)
        await asyncio.sleep(0.2)
        await original_start()

    monkeypatch.setattr(client._session, "start", slow_start)
    monkeypatch.setattr(client._gps_provider, "start", lambda: None)
    # post-connect hooks the mock session doesn't implement
    for hook in ("set_is_favorite_fn", "set_telemetry_data_fn"):
        monkeypatch.setattr(client._session, hook, lambda _fn: None, raising=False)
    monkeypatch.setattr(client, "_seed_contact_book", lambda: None)
    threads = [threading.Thread(target=client.connect) for _ in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert len(starts) == 1 and client._connected
    client._shutdown_loop()


def test_failed_connect_stops_the_half_started_session(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    client._openhop_available = True
    client._config.hardware = None
    failed = client._session
    stopped: list[int] = []

    async def broken_start() -> None:
        raise RuntimeError("radio.begin() returned False")

    async def stop() -> None:
        stopped.append(1)

    monkeypatch.setattr(failed, "start", broken_start)
    monkeypatch.setattr(failed, "stop", stop)
    monkeypatch.setattr(client, "_new_session", lambda: failed.__class__(failed.config))

    with pytest.raises(RuntimeError):
        client.connect()
    assert stopped == [1] and client._session is not failed and not client._connected
    client._shutdown_loop()


def test_replaced_sessions_release_their_database(tmp_path, monkeypatch) -> None:
    import sqlite3

    from meshcore_console.meshcore.config import runtime_config_from_settings
    from meshcore_console.meshcore.session import OpenHopCoreSession
    from meshcore_console.meshcore.settings import MeshcoreSettings

    session = OpenHopCoreSession(runtime_config_from_settings(MeshcoreSettings()))
    session.close()
    with pytest.raises(sqlite3.ProgrammingError):
        session._db.execute("SELECT 1")

    client = _client(tmp_path, monkeypatch)
    old = client._session
    closed: list[int] = []
    monkeypatch.setattr(old, "close", lambda: closed.append(1))
    monkeypatch.setattr(client, "_new_session", lambda: old.__class__(old.config))
    client.update_settings(client._settings)  # disconnected: prepares a new session
    assert closed == [1] and client._session is not old
