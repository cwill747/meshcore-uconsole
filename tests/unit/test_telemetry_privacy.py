"""Telemetry replies carry the position only when the user shares it."""

from __future__ import annotations

from meshcore_console.meshcore.db import open_db


class FixedGps:
    def get_location(self) -> tuple[float, float]:
        return 52.516389, 13.377778

    def start(self) -> None: ...

    def stop(self) -> None: ...


def _client(tmp_path, monkeypatch, **settings):
    from meshcore_console.meshcore import client as client_mod
    from meshcore_console.meshcore.client import MeshcoreClient
    from meshcore_console.meshcore.config import runtime_config_from_settings
    from meshcore_console.meshcore.settings import MeshcoreSettings
    from meshcore_console.mock import MockOpenHopCoreSession

    db_conn = open_db(str(tmp_path / "test.db"))
    monkeypatch.setattr(client_mod, "open_db", lambda *a, **k: db_conn)
    client = MeshcoreClient(
        session=MockOpenHopCoreSession(runtime_config_from_settings(MeshcoreSettings())),
        require_openhop=False,
        gps_provider=FixedGps(),
    )
    for key, value in settings.items():
        setattr(client._settings, key, value)
    return client


def test_no_position_unless_shared(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch, allow_telemetry=True, share_position=False)
    data = client._get_local_telemetry()
    assert data["allow"] is True
    assert data["lat"] is None and data["lon"] is None


def test_position_when_shared(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch, allow_telemetry=True, share_position=True)
    data = client._get_local_telemetry()
    assert (data["lat"], data["lon"]) == (52.516389, 13.377778)


class NoGps(FixedGps):
    def get_location(self) -> None:
        return None


def test_own_map_shows_the_fixed_position_even_when_not_shared(tmp_path, monkeypatch) -> None:
    client = _client(
        tmp_path, monkeypatch, share_position=False, latitude=51.3397, longitude=12.3731
    )
    client._gps_provider = NoGps()
    assert client.get_device_location() == (51.3397, 12.3731)
    # ...while nothing about it goes on air
    assert client._get_local_telemetry()["lat"] is None
    sent: dict = {}

    async def send_advert(name=None, lat=0.0, lon=0.0, route_type="flood"):
        sent.update(lat=lat, lon=lon)
        return {"success": True}

    monkeypatch.setattr(client._session, "send_advert", send_advert)
    client._connected = True
    client.send_advert()
    assert sent == {"lat": 0.0, "lon": 0.0}
