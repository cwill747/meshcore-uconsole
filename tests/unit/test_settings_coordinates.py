"""A fixed position can be cleared and set to 0 in the settings form."""

from __future__ import annotations

import pytest

from meshcore_console.ui_gtk.views.settings import parse_coordinate


def test_empty_clears_the_fixed_position() -> None:
    assert parse_coordinate("", "latitude", 90.0) == 0.0
    assert parse_coordinate("   ", "longitude", 180.0) == 0.0


def test_values_including_zero() -> None:
    assert parse_coordinate("0", "latitude", 90.0) == 0.0
    assert parse_coordinate(" 51.3397 ", "latitude", 90.0) == 51.3397
    assert parse_coordinate("-12.5", "longitude", 180.0) == -12.5


def test_rejects_garbage_and_out_of_range() -> None:
    for text, limit in (("north", 90.0), ("91", 90.0), ("-180.5", 180.0)):
        with pytest.raises(ValueError):
            parse_coordinate(text, "x", limit)
