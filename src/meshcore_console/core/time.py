"""Timezone-aware timestamp helpers for display."""

from __future__ import annotations

from datetime import datetime, timezone


def to_local(dt: datetime) -> datetime:
    """Convert a datetime to the system local timezone.

    Treats naive datetimes as UTC (all internal timestamps use UTC).
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone()
