"""GPS provider for device location."""

from __future__ import annotations

import logging
import os
import socket
import threading
from pathlib import Path
from time import monotonic
from typing import Callable, Protocol

logger = logging.getLogger(__name__)

# Some receivers report sky view only periodically. The serial provider
# reads one NMEA line per poll, so allow for a sequence of other sentences.
SKY_REPORT_MAX_AGE = 45.0


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


def _gga_has_fix(sentence: str) -> bool:
    """GGA field 6, the fix quality: 0 (or missing) is no fix."""
    parts = sentence.split(",")
    return len(parts) > 6 and parts[6].isdigit() and int(parts[6]) > 0


def _rmc_has_fix(sentence: str) -> bool:
    """RMC field 2, the status: A is valid, V (void) is no fix."""
    parts = sentence.split(",")
    return len(parts) > 2 and parts[2] == "A"


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

    def is_available(self) -> bool:
        """False when there is no receiver, or it explicitly can't be used
        (port won't open, gpsd unreachable).  Waiting for a fix is available."""
        ...

    def get_satellites_in_view(self) -> int | None:
        """Latest reported count, or None without a recent report."""
        ...


class UConsoleGps:
    """GPS provider for uConsole AIO board.

    The AIO V2 board provides GPS via the Pi's UART, /dev/ttyS0 on older
    CM4-based units and /dev/ttyAMA0 on CM5-based units. The port can be
    overridden via the constructor (Settings > GPS Device) or the
    MESHCORE_GPS_DEVICE environment variable.
    GPIO 27 powers the GPS module; it is switched on at start and left on.
    """

    GPIO_ENABLE_PIN = 27
    SERIAL_PORT = "/dev/ttyS0"  # Default; see GPS_SERIAL_CANDIDATES
    BAUD_RATE = 9600
    MAX_EMPTY_READS = 3

    def __init__(self, serial_port: str | None = None) -> None:
        self._serial_port = serial_port or self.SERIAL_PORT
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
        self._empty_reads = 0
        self._sky_reports: dict[str, tuple[int, float]] = {}
        self._last_status_log_count = 0
        self._port_failed = False  # the serial port could not be opened or read

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
                self._serial_port,
                self.BAUD_RATE,
                timeout=1.0,
            )
            self._running = True
            self._port_failed = False
            self._empty_reads = 0
            logger.debug("GPS: opened %s at %d baud", self._serial_port, self.BAUD_RATE)
        except ImportError:
            self._port_failed = True
            self._report_error("pyserial not installed - GPS unavailable")
        except PermissionError:
            self._port_failed = True
            self._report_error(
                f"Permission denied on {self._serial_port} - add user to dialout group"
            )
        except OSError as e:
            self._port_failed = True
            self._report_error(f"Serial port error: {e}")

    def stop(self) -> None:
        self._running = False
        self._lose_fix()
        self._sky_reports.clear()

        # Close serial port
        if self._serial is not None:
            try:
                self._serial.close()  # type: ignore[union-attr]
            except OSError as e:
                logger.debug("GPS: serial close error: %s", e)
            self._serial = None

        # Leave the GPS power rail as it is.  It is the board's shared rail
        # (gpsd, other apps and the board's own tools use it): driving it LOW,
        # or GPIO.cleanup() floating the pin, powered the GPS off under them.
        self._gpio_enabled = False

    def get_location(self) -> tuple[float, float] | None:
        # Only a current fix, as GpsdProvider: the last position is not where
        # the device is once the fix (or the receiver) is lost.
        if not self._running or not self._has_fix:
            return None
        if self._latitude is not None and self._longitude is not None:
            return (self._latitude, self._longitude)
        return None

    def _lose_fix(self) -> None:
        """No current fix: forget the position with it."""
        if self._has_fix:
            logger.info("GPS: fix lost")
        self._has_fix = False
        self._latitude = self._longitude = None

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

    def is_available(self) -> bool:
        """The port opened and reads.  "No data" and "waiting for a fix" are
        reported as errors too, but the receiver is there."""
        return not self._port_failed

    def get_satellites_in_view(self) -> int | None:
        now = monotonic()
        counts = {
            talker: count
            for talker, (count, received_at) in self._sky_reports.items()
            if now - received_at <= SKY_REPORT_MAX_AGE
        }
        if "GN" in counts:  # a combined report already includes the constellations
            return counts["GN"]
        return sum(counts.values()) if counts else None

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
            logger.info("GPS: fix acquired")  # never log the position itself
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
            logger.info("GPS: fix acquired")  # never log the position itself
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
            self._port_failed = False
            if not line:
                # A quiet port can still be open after the receiver stops.
                self._empty_reads += 1
                if self._empty_reads >= self.MAX_EMPTY_READS:
                    self._lose_fix()
                    self._sky_reports.clear()
                # No data received - check if this is persistent
                if self._poll_count == 15:  # ~30 seconds with 2s poll interval
                    self._report_error("No GPS data received - check serial connection")
                return True

            self._empty_reads = 0
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

            # A sentence that reports no fix ends the fix, even if it still
            # carries the last position (receivers send one); parse only valid ones.
            if sentence.startswith("$GNGGA") or sentence.startswith("$GPGGA"):
                self._log_gga_status(sentence)
                if _gga_has_fix(sentence):
                    self._parse_gga(sentence)
                else:
                    self._lose_fix()
            elif sentence.startswith("$GNRMC") or sentence.startswith("$GPRMC"):
                self._log_rmc_status(sentence)
                if _rmc_has_fix(sentence):
                    self._parse_rmc(sentence)
                else:
                    self._lose_fix()
            elif sentence_type in {
                "$GPGSV",
                "$GLGSV",
                "$GAGSV",
                "$GBGSV",
                "$BDGSV",
                "$GQGSV",
                "$GIGSV",
                "$GNGSV",
            }:
                self._record_gsv_status(sentence)

            # Check for no-fix condition after getting data
            if self._poll_count == 30 and not self._has_fix:  # ~60 seconds
                self._report_error("GPS acquiring satellites - waiting for fix")

        except OSError as e:
            self._port_failed = True  # e.g. a USB receiver unplugged
            self._lose_fix()
            self._sky_reports.clear()
            self._report_error(f"GPS read error: {e}")
        except UnicodeDecodeError as e:
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

    def _record_gsv_status(self, sentence: str) -> None:
        """Record the count once per GSV sequence and constellation."""
        parts = sentence.partition("*")[0].split(",", 4)
        if len(parts) < 4 or parts[2] != "1" or not parts[3].isdigit():
            return
        count = int(parts[3])
        talker = sentence[1:3]
        if talker == "BD":  # older talker ID for BeiDou
            talker = "GB"
        previous = self._sky_reports.get(talker)
        self._sky_reports[talker] = (count, monotonic())
        if previous is None or previous[0] != count or self._should_log_status():
            logger.debug("GPS: %s satellites in view: %d", parts[0], count)

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


class GpsdProvider:
    """GPS provider that reads from gpsd via gpsdclient.

    Runs a background daemon thread streaming position and sky-view reports.
    Reconnects with exponential backoff if gpsd disconnects.
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 2947) -> None:
        self._host = host
        self._port = port
        self._callback: Callable[[float, float], None] | None = None
        self._running = False
        self._latitude: float | None = None
        self._longitude: float | None = None
        self._has_fix = False
        self._last_error: str | None = None
        self._thread: threading.Thread | None = None
        # A TCP connection alone does not tell us if gpsd has a receiver.
        self._reachable = False
        self._sky_report: tuple[int, float] | None = None
        self._fix_device: str | None = None

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        logger.info("GpsdProvider: started (host=%s, port=%d)", self._host, self._port)

    def stop(self) -> None:
        self._running = False
        self._clear_receiver_state()
        logger.debug("GpsdProvider: stopped")

    def _clear_receiver_state(self) -> None:
        self._has_fix = False
        self._latitude = self._longitude = None
        self._fix_device = None
        self._sky_report = None
        self._reachable = False

    def get_location(self) -> tuple[float, float] | None:
        # Only a current fix: after the fix (or gpsd) was lost the last
        # position went on into adverts, telemetry and the map as if current.
        if self._has_fix and self._latitude is not None and self._longitude is not None:
            return (self._latitude, self._longitude)
        return None

    def set_callback(self, callback: Callable[[float, float], None] | None) -> None:
        self._callback = callback

    def poll(self) -> bool:
        return self._running

    def get_last_error(self) -> str | None:
        return self._last_error

    def has_fix(self) -> bool:
        return self._has_fix

    def is_available(self) -> bool:
        return self._reachable

    def get_satellites_in_view(self) -> int | None:
        report = self._sky_report
        if report is not None and monotonic() - report[1] <= SKY_REPORT_MAX_AGE:
            return report[0]
        return None

    def _record_sky_status(self, result: dict) -> None:
        count = result.get("nSat")
        satellites = result.get("satellites")
        if not isinstance(count, int) or count < 0:
            count = len(satellites) if isinstance(satellites, list) else None
        if count is not None:
            self._sky_report = (count, monotonic())

    def _update_location(self, lat: float, lon: float, *, device: str | None = None) -> None:
        if lat == 0.0 and lon == 0.0:
            return
        if not self._has_fix:
            self._has_fix = True
            logger.info("GpsdProvider: fix acquired")  # never log the position itself
        self._latitude = lat
        self._longitude = lon
        self._fix_device = device
        if self._callback:
            self._callback(lat, lon)

    def _run(self) -> None:
        from gpsdclient import GPSDClient  # type: ignore[import-not-found]

        backoff = 1.0
        max_backoff = 30.0

        while self._running:
            try:
                logger.debug("GpsdProvider: connecting to %s:%d", self._host, self._port)
                with GPSDClient(host=self._host, port=self._port) as client:
                    for result in client.dict_stream(filter=["TPV", "SKY", "DEVICE"]):
                        if not self._running:
                            return
                        report_type = result.get("class")
                        if report_type == "DEVICE":
                            # A receiver can deactivate while the WATCH stream stays open.
                            path = result.get("path")
                            if result.get("activated") == 0 and (
                                self._fix_device is None or path in (None, self._fix_device)
                            ):
                                self._clear_receiver_state()
                            continue
                        # gpsdclient connects lazily when the stream starts.
                        backoff = 1.0
                        self._reachable = True
                        if report_type == "SKY":
                            self._record_sky_status(result)
                            continue
                        mode = result.get("mode", 0)
                        if mode < 2:
                            if self._has_fix:
                                self._has_fix = False
                                logger.info("GpsdProvider: fix lost (mode=%d)", mode)
                            continue
                        lat = result.get("lat")
                        lon = result.get("lon")
                        if lat is not None and lon is not None:
                            self._update_location(
                                float(lat), float(lon), device=result.get("device")
                            )
                # The stream ended without an error: gpsd closed the connection
                # (restart, shutdown).  Its fix ended with it.
                if not self._running:
                    return
                reason = "gpsd closed the connection"
            except Exception as e:
                if not self._running:
                    return
                reason = f"gpsd connection error: {e}"
            self._clear_receiver_state()
            self._last_error = reason
            logger.warning("GpsdProvider: %s (retry in %.0fs)", reason, backoff)
            # Back off before reconnecting (after a close too: a gpsd that
            # accepts and closes at once would be retried in a busy loop).
            # Sleep in small increments so we can check _running
            elapsed = 0.0
            while elapsed < backoff and self._running:
                import time

                time.sleep(min(0.5, backoff - elapsed))
                elapsed += 0.5
            backoff = min(backoff * 2, max_backoff)


def _gpsd_available(host: str = "127.0.0.1", port: int = 2947) -> bool:
    """Check if gpsd is reachable via a raw socket connect."""
    try:
        with socket.create_connection((host, port), timeout=1.0):
            return True
    except OSError:
        return False


class NullGps:
    """No-op GPS provider for systems without GPS hardware."""

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def get_location(self) -> tuple[float, float] | None:
        return None

    def set_callback(self, callback: Callable[[float, float], None] | None) -> None:
        pass

    def poll(self) -> bool:
        return True

    def get_last_error(self) -> str | None:
        return None

    def has_fix(self) -> bool:
        return False

    def is_available(self) -> bool:
        return False  # no receiver configured or detected

    def get_satellites_in_view(self) -> int | None:
        return None


# Serial ports probed when no explicit device is configured. /dev/ttyS0 is
# the GPS UART on CM4-based uConsoles; CM5-based units expose it at
# /dev/ttyAMA0 instead (#82).
GPS_SERIAL_CANDIDATES: tuple[str, ...] = ("/dev/ttyS0", "/dev/ttyAMA0")


def create_gps_provider(serial_port: str | None = None) -> GpsProvider:
    """Create the appropriate GPS provider for the current environment.

    Priority:
    1. MESHCORE_MOCK=1 → MockGps
    2. Explicit device (``serial_port`` arg, e.g. from Settings, or the
       MESHCORE_GPS_DEVICE env var) → UConsoleGps on that port
    3. gpsd reachable (unless MESHCORE_GPSD_DISABLE=1) → GpsdProvider
    4. First existing candidate port (/dev/ttyS0, /dev/ttyAMA0) → UConsoleGps
    5. Fallback → NullGps (returns None; callers use settings fixed position)
    """
    if os.environ.get("MESHCORE_MOCK", "0") == "1":
        from meshcore_console.mock import MockGps

        return MockGps()

    # An explicitly configured device wins over auto-detection. Use it even
    # if it doesn't exist yet (e.g. USB GPS plugged in later); start() will
    # surface a visible error if the port can't be opened.
    configured = (serial_port or os.environ.get("MESHCORE_GPS_DEVICE", "")).strip()
    if configured:
        if not Path(configured).exists():
            logger.warning("GPS: configured device %s not present (yet)", configured)
        logger.info("GPS: using configured device %s", configured)
        return UConsoleGps(serial_port=configured)

    # Check for gpsd
    if os.environ.get("MESHCORE_GPSD_DISABLE", "0") != "1":
        host = os.environ.get("MESHCORE_GPSD_HOST", "127.0.0.1")
        port = int(os.environ.get("MESHCORE_GPSD_PORT", "2947"))
        if _gpsd_available(host, port):
            logger.info("GPS: gpsd detected at %s:%d, using GpsdProvider", host, port)
            return GpsdProvider(host=host, port=port)

    # Check if we're on a Pi with GPS hardware (CM4: ttyS0, CM5: ttyAMA0)
    for candidate in GPS_SERIAL_CANDIDATES:
        if Path(candidate).exists():
            logger.info("GPS: auto-detected serial device %s", candidate)
            return UConsoleGps(serial_port=candidate)

    logger.debug("GPS: no hardware detected, using NullGps")
    return NullGps()
