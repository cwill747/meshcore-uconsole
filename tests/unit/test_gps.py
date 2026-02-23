"""Tests for GPS providers."""

from __future__ import annotations

import os
from unittest.mock import patch

from meshcore_console.platform.gps import (
    GpsdProvider,
    UConsoleGps,
    _gpsd_available,
    _nmea_to_decimal,
    create_gps_provider,
)


# --- _nmea_to_decimal ---


def test_nmea_to_decimal_latitude_north() -> None:
    # 37 degrees 46.9410 minutes N = 37.78235
    result = _nmea_to_decimal("3746.9410", "N", is_longitude=False)
    assert abs(result - 37.78235) < 0.001


def test_nmea_to_decimal_latitude_south() -> None:
    result = _nmea_to_decimal("3746.9410", "S", is_longitude=False)
    assert result < 0
    assert abs(result + 37.78235) < 0.001


def test_nmea_to_decimal_longitude_west() -> None:
    result = _nmea_to_decimal("12225.1234", "W", is_longitude=True)
    assert result < 0


def test_nmea_to_decimal_longitude_east() -> None:
    result = _nmea_to_decimal("12225.1234", "E", is_longitude=True)
    assert result > 0


# --- GpsdProvider ---


def test_gpsd_provider_initial_state() -> None:
    provider = GpsdProvider()
    assert provider.get_location() is None
    assert not provider.has_fix()
    assert provider.get_last_error() is None


def test_gpsd_provider_update_location() -> None:
    provider = GpsdProvider()
    provider._update_location(37.7749, -122.4194)
    assert provider.has_fix()
    loc = provider.get_location()
    assert loc is not None
    assert abs(loc[0] - 37.7749) < 0.0001
    assert abs(loc[1] - (-122.4194)) < 0.0001


def test_gpsd_provider_rejects_zero_zero() -> None:
    provider = GpsdProvider()
    provider._update_location(0.0, 0.0)
    assert provider.get_location() is None
    assert not provider.has_fix()


def test_gpsd_provider_callback() -> None:
    provider = GpsdProvider()
    received: list[tuple[float, float]] = []
    provider.set_callback(lambda lat, lon: received.append((lat, lon)))
    provider._update_location(37.7749, -122.4194)
    assert len(received) == 1
    assert received[0] == (37.7749, -122.4194)


def test_gpsd_provider_fix_loss() -> None:
    provider = GpsdProvider()
    provider._update_location(37.7749, -122.4194)
    assert provider.has_fix()
    # Simulate fix loss: _has_fix is cleared by the _run loop when mode < 2,
    # but we can test the attribute directly
    provider._has_fix = False
    assert not provider.has_fix()


def test_gpsd_provider_poll_returns_running_state() -> None:
    provider = GpsdProvider()
    assert provider.poll() is False  # not started
    provider._running = True
    assert provider.poll() is True


# --- _gpsd_available ---


def test_gpsd_available_returns_false_for_closed_port() -> None:
    # Port 1 should not have gpsd listening
    assert _gpsd_available("127.0.0.1", 1) is False


# --- create_gps_provider ---


def test_create_gps_provider_mock_mode() -> None:
    from meshcore_console.mock.gps import MockGps

    with patch.dict(os.environ, {"MESHCORE_MOCK": "1"}):
        provider = create_gps_provider()
        assert isinstance(provider, MockGps)


def test_create_gps_provider_prefers_gpsd_when_available() -> None:
    env = {"MESHCORE_MOCK": "0"}
    with (
        patch.dict(os.environ, env, clear=False),
        patch("meshcore_console.platform.gps._gpsd_available", return_value=True),
    ):
        provider = create_gps_provider()
        assert isinstance(provider, GpsdProvider)


def test_create_gps_provider_respects_gpsd_disable() -> None:
    env = {"MESHCORE_MOCK": "0", "MESHCORE_GPSD_DISABLE": "1"}
    with (
        patch.dict(os.environ, env, clear=False),
        patch("meshcore_console.platform.gps._gpsd_available", return_value=True) as mock_avail,
        patch("meshcore_console.platform.gps.Path") as mock_path,
    ):
        mock_path.return_value.exists.return_value = False
        provider = create_gps_provider()
        # gpsd should not even be checked
        mock_avail.assert_not_called()
        # Should fall through to mock since /dev/ttyS0 doesn't exist
        from meshcore_console.mock.gps import MockGps

        assert isinstance(provider, MockGps)


def test_create_gps_provider_falls_back_to_serial() -> None:
    env = {"MESHCORE_MOCK": "0"}
    with (
        patch.dict(os.environ, env, clear=False),
        patch("meshcore_console.platform.gps._gpsd_available", return_value=False),
        patch("meshcore_console.platform.gps.Path") as mock_path,
    ):
        mock_path.return_value.exists.return_value = True
        provider = create_gps_provider()
        assert isinstance(provider, UConsoleGps)
