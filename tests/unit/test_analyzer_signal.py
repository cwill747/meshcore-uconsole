"""The analyzer shows measured signal values, and n/a for missing ones."""

from __future__ import annotations

from meshcore_console.ui_gtk.views.analyzer import signal_part, signal_text


def test_zero_snr_is_a_real_value() -> None:
    assert signal_text(-30, 0.0) == "-30 / 0.00"


def test_missing_values_are_not_invented() -> None:
    assert signal_text(None, None) == "n/a / n/a"
    assert signal_part(None, "{:d} dBm") == "n/a"
    assert signal_part(-112, "{:d} dBm") == "-112 dBm"
