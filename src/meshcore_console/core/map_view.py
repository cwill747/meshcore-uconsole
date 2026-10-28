"""Where the map opens: the last view, else this device, else the known peers."""

from __future__ import annotations

import math

View = tuple[float, float, float]  # latitude, longitude, zoom level

DEVICE_ZOOM = 12.0  # streets around this device
WORLD: View = (20.0, 0.0, 2.0)
MAX_ZOOM = 19.0


def encode_view(lat: float, lon: float, zoom: float) -> str:
    return f"{lat:.6f},{lon:.6f},{zoom:.2f}"


def decode_view(raw: str | None) -> View | None:
    """A stored view, or None if missing or not a sane view."""
    try:
        lat, lon, zoom = (float(part) for part in (raw or "").split(","))
    except ValueError:
        return None
    if not (-90 <= lat <= 90 and -180 <= lon <= 180 and 0 <= zoom <= MAX_ZOOM):
        return None
    return lat, lon, zoom


def fit_view(points: list[tuple[float, float]], width_px: int = 600, height_px: int = 400) -> View:
    """Centre and zoom showing every point on a map about this size
    (256-pixel tiles, Web Mercator; ignores the antimeridian)."""
    if len(points) == 1:
        return points[0][0], points[0][1], DEVICE_ZOOM
    lats = [p[0] for p in points]
    lons = [p[1] for p in points]
    lat, lon = (min(lats) + max(lats)) / 2, (min(lons) + max(lons)) / 2
    lat_span = max(max(lats) - min(lats), 1e-4)
    lon_span = max(max(lons) - min(lons), 1e-4)
    zoom_lon = math.log2(width_px * 360 / (256 * lon_span))
    zoom_lat = math.log2(height_px * 360 * math.cos(math.radians(lat)) / (256 * lat_span))
    return lat, lon, float(max(2, min(14, math.floor(min(zoom_lon, zoom_lat)))))


def initial_view(
    *,
    gps_fix: tuple[float, float] | None,
    saved: View | None,
    device: tuple[float, float] | None,
    peers: list[tuple[float, float]],
) -> tuple[View, str]:
    """The opening view and where it came from: "gps", "saved", "device"
    (the fixed position from Settings), "peers" or "world"."""
    if gps_fix is not None:
        return (gps_fix[0], gps_fix[1], DEVICE_ZOOM), "gps"
    if saved is not None:
        return saved, "saved"
    if device is not None:
        return (device[0], device[1], DEVICE_ZOOM), "device"
    if peers:
        return fit_view(peers), "peers"
    return WORLD, "world"
