"""The map's GPS status: live fix, no fix, unavailable, and a fixed location.

Derived from the provider's state and where the position comes from: the
location alone may be the fixed position from Settings, and the serial
provider reports "waiting for a fix" as an error string."""

from __future__ import annotations

import sys
import time
import types

import pytest

from meshcore_console.core.map_view import gps_status_label
from meshcore_console.core.models import GpsState
from meshcore_console.meshcore.db import open_db
from meshcore_console.mock import MockGps, MockMeshcoreClient
from meshcore_console.platform.gps import GpsdProvider, NullGps, UConsoleGps

BERLIN = (52.516389, 13.377778)


@pytest.fixture(autouse=True)
def _disable_gpio(monkeypatch) -> None:
    """Serial-provider tests must never power on a real receiver."""
    monkeypatch.setitem(sys.modules, "RPi.GPIO", None)


def test_labels() -> None:
    assert gps_status_label(GpsState(available=True, fix=True)) == ("GPS fix", "ok")
    assert gps_status_label(GpsState(available=True, fix=False)) == ("No GPS fix", "warn")
    assert gps_status_label(GpsState(available=False, fix=False)) == (
        "GPS unavailable",
        "offline",
    )
    # a fixed location never hides what the receiver is doing
    assert gps_status_label(GpsState(available=True, fix=False, manual=True)) == (
        "Fixed location · No GPS fix",
        "warn",
    )
    assert gps_status_label(GpsState(available=False, fix=False, manual=True)) == (
        "Fixed location · GPS unavailable",
        "offline",
    )


@pytest.mark.parametrize(
    ("count", "label"),
    [(0, "0 satellites in view"), (1, "1 satellite in view"), (5, "5 satellites in view")],
)
def test_no_fix_shows_reported_satellite_count(count, label) -> None:
    assert gps_status_label(GpsState(available=True, fix=False, satellites_in_view=count)) == (
        f"No GPS fix · {label}",
        "warn",
    )


# --- providers ---------------------------------------------------------------


def test_no_receiver_is_unavailable() -> None:
    assert not NullGps().is_available()


def test_serial_port_that_cannot_open_is_unavailable() -> None:
    gps = UConsoleGps(serial_port="/nonexistent/ttyGPS")
    assert gps.is_available()  # not known to fail before it was tried
    gps.start()
    assert not gps.is_available()


def test_waiting_for_a_fix_is_not_unavailable() -> None:
    gps = UConsoleGps(serial_port="/dev/null")
    gps._report_error("GPS acquiring satellites - waiting for fix")
    assert gps.get_last_error()
    assert gps.is_available()
    assert not gps.has_fix()


def test_gpsd_is_unavailable_before_receiving_a_report() -> None:
    assert not GpsdProvider().is_available()


# --- mock client: the transitions the map shows ------------------------------


def test_mock_fix_loss_unavailable_and_fixed_location() -> None:
    client = MockMeshcoreClient()
    gps: MockGps = client._gps_provider

    assert gps_status_label(client.get_gps_state())[0] == "GPS fix"

    gps.set_fix(False)
    assert gps_status_label(client.get_gps_state())[0] == "No GPS fix"
    assert client.get_device_location() is None
    assert not client.has_gps_fix()
    gps.set_satellites_in_view(3)
    assert gps_status_label(client.get_gps_state())[0] == "No GPS fix · 3 satellites in view"

    gps.set_available(False)
    assert gps_status_label(client.get_gps_state())[0] == "GPS unavailable"

    settings = client.get_settings()
    settings.latitude, settings.longitude = BERLIN
    client.update_settings(settings)
    assert client.get_device_location() == BERLIN
    assert gps_status_label(client.get_gps_state())[0] == "Fixed location · GPS unavailable"

    gps.set_available(True)
    gps.set_fix(True)  # live GPS wins over the fixed location
    assert gps_status_label(client.get_gps_state())[0] == "GPS fix"
    assert client.get_device_location() != BERLIN


# --- real client -------------------------------------------------------------


class FakeGps:
    def __init__(self, location=None, fix=False, available=True) -> None:
        self.location, self.fix, self.available = location, fix, available

    def get_location(self):
        return self.location

    def has_fix(self) -> bool:
        return self.fix

    def is_available(self) -> bool:
        return self.available

    def get_satellites_in_view(self) -> int | None:
        return None

    def start(self) -> None: ...

    def stop(self) -> None: ...


def _client(tmp_path, monkeypatch, gps):
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
        gps_provider=gps,
    )


def test_client_live_fix(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch, FakeGps(BERLIN, fix=True))
    assert client.get_gps_state() == GpsState(available=True, fix=True, manual=False)


def test_client_fixed_location_without_a_fix(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch, FakeGps())
    client._settings.latitude, client._settings.longitude = 51.3397, 12.3731
    assert client.get_device_location() == (51.3397, 12.3731)
    # the location getter returns the fixed position: that is no fix
    assert client.get_gps_state() == GpsState(available=True, fix=False, manual=True)


def test_client_no_receiver_and_no_fixed_location(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch, FakeGps(available=False))
    assert client.get_gps_state() == GpsState(available=False, fix=False, manual=False)


def test_client_never_reports_a_fix_from_an_unavailable_receiver(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch, FakeGps(BERLIN, fix=True, available=False))
    assert client.get_gps_state() == GpsState(available=False, fix=False, manual=False)
    assert gps_status_label(client.get_gps_state())[0] == "GPS unavailable"


# --- serial receiver: fix, loss of fix, read failure, recovery ---------------


def _nmea(body: str) -> bytes:
    """A sentence with its checksum, as the receiver sends it."""
    checksum = 0
    for char in body:
        checksum ^= ord(char)
    return f"${body}*{checksum:02X}\r\n".encode("ascii")


# Munich.  The no-fix sentences still carry these coordinates, as receivers
# holding a last position send them.
GGA_FIX = _nmea("GPGGA,123519,4807.038,N,01131.000,E,1,08,0.9,545.4,M,46.9,M,,")
GGA_NO_FIX = _nmea("GPGGA,123520,4807.038,N,01131.000,E,0,00,,,M,,M,,")
RMC_FIX = _nmea("GPRMC,123521,A,4807.038,N,01131.000,E,022.4,084.4,230394,003.1,W")
RMC_VOID = _nmea("GPRMC,123522,V,4807.038,N,01131.000,E,,,230394,,")
MUNICH = (48.1173, 11.516667)
LEIPZIG = (51.3397, 12.3731)


class FakeSerialPort:
    """pyserial's Serial, reading the queued lines (an exception is raised)."""

    lines: list[bytes | Exception] = []

    def __init__(self, port: str, baudrate: int, timeout: float) -> None:
        self.port = port

    def readline(self) -> bytes:
        item = FakeSerialPort.lines.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def close(self) -> None: ...


def _serial_gps(monkeypatch, parser: str) -> UConsoleGps:
    """A started serial provider on the fake port, parsing with pynmea2 or
    the built-in fallback parser."""
    fake_serial = types.ModuleType("serial")
    fake_serial.Serial = FakeSerialPort  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "serial", fake_serial)
    if parser == "manual":
        monkeypatch.setitem(sys.modules, "pynmea2", None)  # import fails: fallback
    monkeypatch.setattr(FakeSerialPort, "lines", [])
    gps = UConsoleGps(serial_port="/dev/ttyGPS")
    gps.start()
    assert gps.is_available()
    return gps


def _feed(client, line: bytes | Exception) -> str:
    """One line through the map's GPS poll; the status it then shows."""
    FakeSerialPort.lines.append(line)
    client.poll_gps()
    return gps_status_label(client.get_gps_state())[0]


def _near(loc, expected) -> bool:
    return loc is not None and all(abs(a - b) < 1e-4 for a, b in zip(loc, expected))


def test_serial_sky_reports_add_distinct_constellations_and_expire(tmp_path, monkeypatch) -> None:
    from meshcore_console.platform import gps as gps_module

    now = 10.0
    monkeypatch.setattr(gps_module, "monotonic", lambda: now)
    gps = _serial_gps(monkeypatch, "pynmea2")
    client = _client(tmp_path, monkeypatch, gps)
    assert _feed(client, _nmea("GPGSV,1,1,00")) == "No GPS fix · 0 satellites in view"
    assert _feed(client, _nmea("GPGSV,2,1,05,1")) == "No GPS fix · 5 satellites in view"
    assert _feed(client, _nmea("GPGSV,2,2,05,1")) == "No GPS fix · 5 satellites in view"
    assert _feed(client, _nmea("GLGSV,1,1,02,1")) == "No GPS fix · 7 satellites in view"
    assert _feed(client, _nmea("GNGSV,2,1,06,1")) == "No GPS fix · 6 satellites in view"
    now += 90.0
    assert _feed(client, RMC_VOID) == "No GPS fix"
    assert _feed(client, _nmea("GAGSV,1,1,01,7")) == "No GPS fix · 1 satellite in view"
    assert _feed(client, OSError("device disconnected")) == "GPS unavailable"
    assert _feed(client, RMC_VOID) == "No GPS fix"


def test_serial_silence_expires_the_previous_fix(tmp_path, monkeypatch) -> None:
    gps = _serial_gps(monkeypatch, "pynmea2")
    client = _client(tmp_path, monkeypatch, gps)
    assert _feed(client, GGA_FIX) == "GPS fix"
    assert _feed(client, b"") == "GPS fix"
    assert _feed(client, b"") == "GPS fix"
    assert _feed(client, b"") == "No GPS fix"
    assert client.get_device_location() is None
    assert _feed(client, RMC_FIX) == "GPS fix"


@pytest.mark.parametrize("parser", ["pynmea2", "manual"])
def test_serial_fix_loss_read_failure_and_recovery(tmp_path, monkeypatch, parser) -> None:
    gps = _serial_gps(monkeypatch, parser)
    client = _client(tmp_path, monkeypatch, gps)

    assert _feed(client, GGA_FIX) == "GPS fix"
    assert _near(client.get_device_location(), MUNICH)

    # GGA fix quality 0: no fix, though the sentence still carries a position
    assert _feed(client, GGA_NO_FIX) == "No GPS fix"
    assert gps.get_location() is None and not client.has_gps_fix()
    assert client.get_device_location() is None  # not the last position

    assert _feed(client, RMC_FIX) == "GPS fix"
    assert _near(client.get_device_location(), MUNICH)
    assert _feed(client, RMC_VOID) == "No GPS fix"
    assert client.get_device_location() is None

    # The fixed position from Settings stands in; the GPS state stays visible
    client._settings.latitude, client._settings.longitude = LEIPZIG
    assert _feed(client, GGA_NO_FIX) == "Fixed location · No GPS fix"
    assert client.get_device_location() == LEIPZIG

    # A fix wins over the fixed position
    assert _feed(client, GGA_FIX) == "GPS fix"
    assert _near(client.get_device_location(), MUNICH)

    # A read error (receiver unplugged): unavailable, and the fix goes with it
    assert _feed(client, OSError("device disconnected")) == "Fixed location · GPS unavailable"
    assert not gps.is_available() and not client.has_gps_fix()
    assert client.get_device_location() == LEIPZIG
    client._settings.latitude, client._settings.longitude = 0.0, 0.0  # unset
    assert gps_status_label(client.get_gps_state())[0] == "GPS unavailable"
    assert client.get_device_location() is None

    # It reads again: available, and only a new fix counts as one
    assert _feed(client, GGA_NO_FIX) == "No GPS fix"
    assert gps.is_available()
    assert _feed(client, GGA_FIX) == "GPS fix"
    assert _near(client.get_device_location(), MUNICH)
    assert FakeSerialPort.lines == []


# --- gpsd: the end of the stream ends the fix ---------------------------------


def test_gpsd_stream_end_drops_the_fix(monkeypatch) -> None:
    """gpsd closing the connection (restart, shutdown) ends the stream with
    no error.  The fix must not outlive it, e.g. while a socket-activated
    gpsd accepts the reconnect but has no receiver to report from."""
    gps = GpsdProvider()
    seen_on_reconnect: list[tuple[bool, object, bool, int | None]] = []
    availability: list[bool] = []
    sky_counts: list[int | None] = []

    class FakeGpsdClient:
        connections = 0

        def __init__(self, host: str, port: int) -> None:
            FakeGpsdClient.connections += 1
            self.number = FakeGpsdClient.connections

        def __enter__(self):
            return self

        def __exit__(self, *_exc: object) -> None: ...

        def dict_stream(self, filter):
            if self.number == 1:
                assert filter == ["TPV", "SKY"]
                availability.append(gps.is_available())
                yield {"class": "SKY", "nSat": 5}
                availability.append(gps.is_available())
                sky_counts.append(gps.get_satellites_in_view())
                yield {"class": "SKY", "hdop": 1.2}
                sky_counts.append(gps.get_satellites_in_view())
                yield {"class": "SKY", "satellites": []}
                sky_counts.append(gps.get_satellites_in_view())
                yield {"class": "TPV", "mode": 3, "lat": BERLIN[0], "lon": BERLIN[1]}
                return  # EOF: gpsd closed the connection
            seen_on_reconnect.append(
                (
                    gps.has_fix(),
                    gps.get_location(),
                    gps.is_available(),
                    gps.get_satellites_in_view(),
                )
            )
            yield {"class": "TPV", "mode": 1}
            availability.append(gps.is_available())
            gps.stop()

    fake = types.ModuleType("gpsdclient")
    fake.GPSDClient = FakeGpsdClient  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "gpsdclient", fake)
    monkeypatch.setattr(time, "sleep", lambda _s: None)

    gps._running = True
    gps._run()  # in this thread; returns once stopped

    assert seen_on_reconnect == [(False, None, False, None)]
    assert availability == [False, True, True]
    assert sky_counts == [5, 5, 0]
    assert gps.get_satellites_in_view() is None
    assert not gps.is_available()
