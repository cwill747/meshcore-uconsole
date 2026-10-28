"""Where the map opens."""

from __future__ import annotations

from meshcore_console.core.map_view import (
    DEVICE_ZOOM,
    WORLD,
    decode_view,
    encode_view,
    fit_view,
    initial_view,
)
from meshcore_console.meshcore.db import open_db

LEIPZIG = (51.3397, 12.3731)
HALLE = (51.4825, 11.9700)


def test_opening_view_precedence() -> None:
    saved = (48.137, 11.575, 9.0)
    peers = [LEIPZIG, HALLE]
    assert initial_view(gps_fix=LEIPZIG, saved=saved, device=LEIPZIG, peers=peers) == (
        (*LEIPZIG, DEVICE_ZOOM),
        "gps",
    )
    assert initial_view(gps_fix=None, saved=saved, device=LEIPZIG, peers=peers) == (saved, "saved")
    assert initial_view(gps_fix=None, saved=None, device=LEIPZIG, peers=peers)[1] == "device"
    assert initial_view(gps_fix=None, saved=None, device=None, peers=peers)[1] == "peers"
    assert initial_view(gps_fix=None, saved=None, device=None, peers=[]) == (WORLD, "world")


def test_fit_view_shows_all_peers() -> None:
    lat, lon, zoom = fit_view([LEIPZIG, HALLE])
    assert 51.33 < lat < 51.49 and 11.97 < lon < 12.38
    assert zoom == 11  # 0.40° of longitude is 587 px at zoom 11: fits ~600 px
    assert fit_view([LEIPZIG]) == (*LEIPZIG, DEVICE_ZOOM)
    assert fit_view([(51.0, 12.0), (51.0, 12.0)])[2] == 14  # same spot: capped
    assert fit_view([(-40.0, -170.0), (60.0, 170.0)])[2] == 2  # spread: world


def test_stored_view_round_trip_and_garbage() -> None:
    assert decode_view(encode_view(51.3397, 12.3731, 11.5)) == (51.3397, 12.3731, 11.5)
    for raw in (None, "", "51,12", "a,b,c", "91,0,3", "0,0,25"):
        assert decode_view(raw) is None


def test_client_keeps_the_view_beside_the_settings(tmp_path, monkeypatch) -> None:
    from meshcore_console.meshcore import client as client_mod
    from meshcore_console.meshcore.client import MeshcoreClient
    from meshcore_console.meshcore.config import runtime_config_from_settings
    from meshcore_console.meshcore.settings import MeshcoreSettings
    from meshcore_console.meshcore.settings_store import SettingsStore
    from meshcore_console.mock import MockOpenHopCoreSession

    db_conn = open_db(str(tmp_path / "test.db"))
    monkeypatch.setattr(client_mod, "open_db", lambda *a, **k: db_conn)
    client = MeshcoreClient(
        session=MockOpenHopCoreSession(runtime_config_from_settings(MeshcoreSettings())),
        require_openhop=False,
    )
    assert client.get_map_view() is None
    client.save_map_view(51.3397, 12.3731, 11.0)
    assert client.get_map_view() == (51.3397, 12.3731, 11.0)
    # an ordinary settings save/load neither drops it nor trips over it
    store = SettingsStore(db_conn)
    store.save(store.load())
    assert store.load() == MeshcoreSettings()
    assert client.get_map_view() == (51.3397, 12.3731, 11.0)
