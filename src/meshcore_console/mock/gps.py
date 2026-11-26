"""Mock GPS provider for development and testing."""

from __future__ import annotations

import logging
from typing import Callable

from .data import MOCK_GPS_WAYPOINTS

logger = logging.getLogger(__name__)


class MockGps:
    """Mock GPS for development and testing.

    Simulates GPS movement along a path in the SF Bay Area.
    Call cycle_position() to advance to the next waypoint.
    """

    def __init__(self, positions: list[tuple[float, float]] | None = None) -> None:
        self._callback: Callable[[float, float], None] | None = None
        self._running = False
        self._position_index = 0
        self._positions = positions if positions is not None else list(MOCK_GPS_WAYPOINTS)
        self._fix = True
        self._available = True
        self._satellites_in_view: int | None = None

    def start(self) -> None:
        self._running = True
        logger.debug("MockGps: started with %d waypoints", len(self._positions))

    def stop(self) -> None:
        self._running = False

    def get_location(self) -> tuple[float, float] | None:
        if not self.has_fix():
            return None
        return self._positions[self._position_index]

    def cycle_position(self) -> bool:
        """Advance to next test position.

        Returns True to allow use as GLib timeout callback.
        """
        if not self._running:
            return False
        self._position_index = (self._position_index + 1) % len(self._positions)
        lat, lon = self._positions[self._position_index]
        if self._callback:
            self._callback(lat, lon)
        return True

    def set_callback(self, callback: Callable[[float, float], None] | None) -> None:
        self._callback = callback

    def poll(self) -> bool:
        """No-op for mock GPS. Returns True to continue polling."""
        return True

    def get_last_error(self) -> str | None:
        """Mock GPS has no errors."""
        return None

    def has_fix(self) -> bool:
        """Mock GPS has a fix when running, unless set_fix/set_available took it."""
        return self._running and self._available and self._fix

    def is_available(self) -> bool:
        return self._available

    def get_satellites_in_view(self) -> int | None:
        return self._satellites_in_view if self._available else None

    def set_satellites_in_view(self, count: int | None) -> None:
        self._satellites_in_view = count

    def set_fix(self, fix: bool) -> None:
        """Simulate gaining or losing the satellite fix (receiver still there)."""
        self._fix = fix

    def set_available(self, available: bool) -> None:
        """Simulate the receiver going missing (or coming back)."""
        self._available = available

    def set_positions(self, positions: list[tuple[float, float]]) -> None:
        """Set custom positions for testing."""
        self._positions = list(positions)
        self._position_index = 0

    def jump_to(self, lat: float, lon: float) -> None:
        """Jump to a specific location (useful for testing)."""
        # Insert at current position
        self._positions[self._position_index] = (lat, lon)
        if self._callback and self._running:
            self._callback(lat, lon)
