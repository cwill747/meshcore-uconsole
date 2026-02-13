"""GPS provider for device location."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Callable, Protocol

logger = logging.getLogger(__name__)


def _nmea_to_decimal(coord: str, direction: str, is_longitude: bool = False) -> float:
    """Convert NMEA coordinate format (DDMM.MMMM or DDDMM.MMMM) to decimal degrees.

    Args:
        coord: Coordinate string in NMEA format (e.g., "3746.9410" for latitude)
        direction: Direction character (N, S, E, or W)
        is_longitude: If True, expects 3 digit degrees (longitude), else 2 (latitude)

    Returns:
        Decimal degrees (negative for S or W)
    """
    deg_len = 3 if is_longitude else 2
    degrees = float(coord[:deg_len])
    minutes = float(coord[deg_len:])
    result = degrees + minutes / 60.0
    if direction in ("S", "W"):
        result = -result
    return result


class GpsProvider(Protocol):
    """Protocol for GPS providers."""

    def start(self) -> None:
        """Start the GPS provider."""
        ...

    def stop(self) -> None:
        """Stop the GPS provider."""
        ...

    def get_location(self) -> tuple[float, float] | None:
        """Return current (latitude, longitude) or None if no fix."""
        ...

    def set_callback(self, callback: Callable[[float, float], None] | None) -> None:
        """Set callback for location updates."""
        ...

    def poll(self) -> bool:
        """Poll for new GPS data. Returns True to continue polling."""
        ...

    def get_last_error(self) -> str | None:
        """Get the last error message, if any."""
        ...

    def has_fix(self) -> bool:
        """Return True if GPS has acquired a satellite fix."""
        ...


class UConsoleGps:
    """GPS provider for uConsole AIO board.

    The AIO V2 board provides GPS via the Pi's UART at /dev/ttyS0.
    GPIO 27 is used to enable/disable the GPS module.
    """

    GPIO_ENABLE_PIN = 27
    SERIAL_PORT = "/dev/ttyS0"
    BAUD_RATE = 9600

    def __init__(self) -> None:
        self._callback: Callable[[float, float], None] | None = None
        self._error_callback: Callable[[str], None] | None = None
        self._running = False
        self._serial: object | None = None
        self._latitude: float | None = None
        self._longitude: float | None = None
        self._gpio_enabled = False
        self._last_error: str | None = None
        self._poll_count = 0
        self._has_fix = False
        self._last_fix_quality = 0
        self._last_num_sats = 0
        self._sats_in_view = 0
        self._last_status_log_count = 0

    def start(self) -> None:
        if self._running:
            return

        # Enable GPS module via GPIO
        try:
            import RPi.GPIO as GPIO  # type: ignore[import-not-found]

            GPIO.setmode(GPIO.BCM)
            GPIO.setup(self.GPIO_ENABLE_PIN, GPIO.OUT)
            GPIO.output(self.GPIO_ENABLE_PIN, GPIO.HIGH)
            self._gpio_enabled = True
            logger.debug("GPS: enabled via GPIO %d", self.GPIO_ENABLE_PIN)
        except ImportError:
            logger.debug("GPS: RPi.GPIO not available, skipping GPIO enable")
        except (RuntimeError, OSError) as e:
            self._report_error(f"GPIO enable failed: {e}")

        # Open serial port
        try:
            import serial  # type: ignore[import-not-found]

            self._serial = serial.Serial(
                self.SERIAL_PORT,
                self.BAUD_RATE,
                timeout=1.0,
            )
            self._running = True
            logger.debug("GPS: opened %s at %d baud", self.SERIAL_PORT, self.BAUD_RATE)
        except ImportError:
            self._report_error("pyserial not installed - GPS unavailable")
        except PermissionError:
            self._report_error(
                f"Permission denied on {self.SERIAL_PORT} - add user to dialout group"
            )
        except OSError as e:
            self._report_error(f"Serial port error: {e}")

    def stop(self) -> None:
        self._running = False

        # Close serial port
        if self._serial is not None:
            try:
                self._serial.close()  # type: ignore[union-attr]
            except OSError as e:
                logger.debug("GPS: serial close error: %s", e)
            self._serial = None

        # Disable GPS module and release the pin
        if self._gpio_enabled:
            try:
                import RPi.GPIO as GPIO  # type: ignore[import-not-found]

                GPIO.output(self.GPIO_ENABLE_PIN, GPIO.LOW)
                GPIO.cleanup(self.GPIO_ENABLE_PIN)
                self._gpio_enabled = False
            except (RuntimeError, OSError) as e:
                logger.debug("GPS: GPIO disable error: %s", e)

    def get_location(self) -> tuple[float, float] | None:
        if not self._running:
            return None
        if self._latitude is not None and self._longitude is not None:
            return (self._latitude, self._longitude)
        return None

    def set_callback(self, callback: Callable[[float, float], None] | None) -> None:
        self._callback = callback

    def set_error_callback(self, callback: Callable[[str], None] | None) -> None:
        """Set callback for error notifications."""
        self._error_callback = callback

    def get_last_error(self) -> str | None:
        """Get the last error message, if any."""
        return self._last_error

    def has_fix(self) -> bool:
        """Return True if GPS has acquired a fix."""
        return self._has_fix

    def _report_error(self, message: str) -> None:
        """Report an error via callback and store it."""
        self._last_error = message
        logger.warning("GPS: %s", message)
        if self._error_callback:
            self._error_callback(message)

    def _update_location(self, lat: float, lon: float) -> None:
        """Update location if valid and notify callback."""
        if lat == 0.0 and lon == 0.0:
            return
        if not self._has_fix:
            self._has_fix = True
            logger.info("GPS: fix acquired: %.6f, %.6f", lat, lon)
        self._latitude = lat
        self._longitude = lon
        if self._callback:
            self._callback(lat, lon)

    def _update_location_if_changed(self, lat: float, lon: float) -> None:
        """Update location if valid and significantly changed."""
        if lat == 0.0 and lon == 0.0:
            return
        if not self._has_fix:
            self._has_fix = True
            logger.info("GPS: fix acquired: %.6f, %.6f", lat, lon)
        old_lat, old_lon = self._latitude, self._longitude
        self._latitude = lat
        self._longitude = lon
        if old_lat is None or abs(lat - old_lat) > 0.00001 or abs(lon - old_lon) > 0.00001:
            if self._callback:
                self._callback(lat, lon)

    def poll(self) -> bool:
        """Read and parse NMEA sentences. Call from GLib timeout.

        Returns True to continue polling.
        """
        if not self._running or self._serial is None:
            return self._running

        self._poll_count += 1

        try:
            line = self._serial.readline()  # type: ignore[union-attr]
            if not line:
                # No data received - check if this is persistent
                if self._poll_count == 15:  # ~30 seconds with 2s poll interval
                    self._report_error("No GPS data received - check serial connection")
                return True

            sentence = line.decode("ascii", errors="ignore").strip()
            if not sentence:
                return True

            # Log all NMEA sentence types for debugging (first time only)
            if not hasattr(self, "_logged_sentences"):
                self._logged_sentences: set[str] = set()
            sentence_type = sentence.split(",")[0] if "," in sentence else sentence[:6]
            if sentence_type not in self._logged_sentences:
                self._logged_sentences.add(sentence_type)
                logger.debug("GPS: receiving NMEA: %s", sentence_type)

            if sentence.startswith("$GNGGA") or sentence.startswith("$GPGGA"):
                self._log_gga_status(sentence)
                self._parse_gga(sentence)
            elif sentence.startswith("$GNRMC") or sentence.startswith("$GPRMC"):
                self._log_rmc_status(sentence)
                self._parse_rmc(sentence)
            elif (
                sentence.startswith("$GPGSV")
                or sentence.startswith("$GLGSV")
                or sentence.startswith("$GNGSV")
            ):
                self._log_gsv_status(sentence)

            # Check for no-fix condition after getting data
            if self._poll_count == 30 and not self._has_fix:  # ~60 seconds
                self._report_error("GPS acquiring satellites - waiting for fix")

        except (OSError, UnicodeDecodeError) as e:
            self._report_error(f"GPS read error: {e}")

        return True

    def _should_log_status(self) -> bool:
        """Return True if we should emit a periodic status log.

        Logs on polls 1-5 (initial startup), then every ~15 polls (~30s at 2s interval).
        """
        if self._poll_count <= 5:
            return True
        if self._poll_count - self._last_status_log_count >= 15:
            return True
        return False

    def _mark_status_logged(self) -> None:
        self._last_status_log_count = self._poll_count

    def _log_gga_status(self, sentence: str) -> None:
        """Log GGA fix quality and satellite count periodically."""
        parts = sentence.split(",")
        fix_quality = int(parts[6]) if len(parts) > 6 and parts[6].isdigit() else 0
        num_sats = int(parts[7]) if len(parts) > 7 and parts[7].isdigit() else 0
        lat_field = parts[2] if len(parts) > 2 else ""
        hdop = parts[8] if len(parts) > 8 and parts[8] else "-"
        alt = f"{parts[9]}{parts[10]}" if len(parts) > 10 and parts[9] else "-"

        changed = fix_quality != self._last_fix_quality or num_sats != self._last_num_sats
        self._last_fix_quality = fix_quality
        self._last_num_sats = num_sats

        if changed or self._should_log_status():
            fix_desc = {0: "no fix", 1: "GPS", 2: "DGPS", 6: "dead reckoning"}.get(
                fix_quality, f"unknown({fix_quality})"
            )
            logger.debug(
                "GPS: fix=%s sats=%d hdop=%s alt=%s lat='%s'",
                fix_desc,
                num_sats,
                hdop,
                alt,
                lat_field,
            )
            self._mark_status_logged()

    def _log_rmc_status(self, sentence: str) -> None:
        """Log RMC validity status periodically."""
        parts = sentence.split(",")
        if len(parts) < 3:
            return
        status = parts[2]  # A=active, V=void
        if self._should_log_status():
            status_desc = "active" if status == "A" else "void (no fix)"
            logger.debug("GPS: RMC status=%s", status_desc)

    def _log_gsv_status(self, sentence: str) -> None:
        """Log satellites in view from GSV sentences."""
        parts = sentence.split(",")
        # GSV format: $GPGSV,total_msgs,msg_num,sats_in_view,...
        if len(parts) < 4:
            return
        # Only log from the first message in the sequence
        msg_num = parts[2]
        if msg_num != "1":
            return
        try:
            sats_in_view = int(parts[3])
        except ValueError:
            return
        constellation = sentence[:6]  # e.g. $GPGSV, $GLGSV
        if sats_in_view != self._sats_in_view or self._should_log_status():
            self._sats_in_view = sats_in_view
            logger.debug("GPS: %s satellites in view: %d", constellation, sats_in_view)

    def _parse_gga(self, sentence: str) -> None:
        """Parse a GGA NMEA sentence for position."""
        try:
            import pynmea2  # type: ignore[import-not-found]

            msg = pynmea2.parse(sentence)
            if hasattr(msg, "latitude") and hasattr(msg, "longitude"):
                lat = float(msg.latitude)
                lon = float(msg.longitude)
                self._update_location(lat, lon)
        except ImportError:
            # Fallback: basic manual parsing
            self._parse_gga_manual(sentence)
        except ValueError as e:
            logger.debug("GPS: parse error: %s", e)

    def _parse_gga_manual(self, sentence: str) -> None:
        """Manual GGA parsing fallback without pynmea2."""
        try:
            parts = sentence.split(",")
            if len(parts) < 6:
                return

            # GGA format: $GNGGA,time,lat,N/S,lon,E/W,...
            lat_str, lat_dir = parts[2], parts[3]
            lon_str, lon_dir = parts[4], parts[5]

            if not lat_str or not lon_str:
                return

            lat = _nmea_to_decimal(lat_str, lat_dir, is_longitude=False)
            lon = _nmea_to_decimal(lon_str, lon_dir, is_longitude=True)

            self._update_location(lat, lon)
        except (ValueError, IndexError) as e:
            logger.debug("GPS: manual parse error: %s", e)

    def _parse_rmc(self, sentence: str) -> None:
        """Parse an RMC NMEA sentence for position (backup to GGA)."""
        try:
            import pynmea2  # type: ignore[import-not-found]

            msg = pynmea2.parse(sentence)
            if hasattr(msg, "latitude") and hasattr(msg, "longitude"):
                lat = float(msg.latitude)
                lon = float(msg.longitude)
                self._update_location_if_changed(lat, lon)
        except ImportError:
            # Fallback: basic manual parsing
            self._parse_rmc_manual(sentence)
        except ValueError as e:
            logger.debug("GPS: RMC parse error: %s", e)

    def _parse_rmc_manual(self, sentence: str) -> None:
        """Manual RMC parsing fallback without pynmea2."""
        try:
            parts = sentence.split(",")
            if len(parts) < 7:
                return

            # RMC format: $GNRMC,time,status,lat,N/S,lon,E/W,...
            status = parts[2]
            if status != "A":  # A = valid, V = void
                return

            lat_str, lat_dir = parts[3], parts[4]
            lon_str, lon_dir = parts[5], parts[6]

            if not lat_str or not lon_str:
                return

            lat = _nmea_to_decimal(lat_str, lat_dir, is_longitude=False)
            lon = _nmea_to_decimal(lon_str, lon_dir, is_longitude=True)

            self._update_location_if_changed(lat, lon)
        except (ValueError, IndexError) as e:
            logger.debug("GPS: manual RMC parse error: %s", e)


def create_gps_provider() -> GpsProvider:
    """Create the appropriate GPS provider for the current environment."""
    if os.environ.get("MESHCORE_MOCK", "0") == "1":
        from meshcore_console.mock import MockGps

        return MockGps()

    # Check if we're on a Pi with GPS hardware
    if Path("/dev/ttyS0").exists():
        return UConsoleGps()

    # Fall back to mock
    from meshcore_console.mock import MockGps

    return MockGps()
