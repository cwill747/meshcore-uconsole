"""Remote repeater tabs: what they show from the repeater's replies."""

from __future__ import annotations

from meshcore_console.ui_gtk.views.repeater_tabs import (
    format_duration,
    sort_neighbours,
    status_rows,
)


def test_durations_read_at_a_glance() -> None:
    assert format_duration(42) == "42 s"
    assert format_duration(610) == "10 m 10 s"
    assert format_duration(5 * 3600 + 12 * 60) == "5 h 12 m"
    assert format_duration(3 * 86400 + 5 * 3600 + 7) == "3 d 5 h"
    assert format_duration(-5) == "0 s"  # clock skew on the repeater


def test_status_rows_show_airtime_as_share_of_uptime() -> None:
    rows = dict(
        status_rows(
            {"bat": 4012, "uptime": 10000, "airtime": 250, "rx_airtime": 1000, "noise_floor": -112}
        )
    )
    assert rows["Battery"] == "4.01 V"
    assert rows["TX airtime"] == "4 m 10 s (2.5 %)"
    assert rows["RX airtime"] == "16 m 40 s (10.0 %)"
    assert rows["Noise floor"] == "-112 dBm"


def test_status_rows_survive_a_fresh_boot() -> None:
    assert dict(status_rows({"uptime": 0, "airtime": 0}))["TX airtime"] == "0 s"


def test_neighbour_sort_orders() -> None:
    ns = [
        {"pubkey": "a", "secs_ago": 600, "snr": 9.0},
        {"pubkey": "b", "secs_ago": 30, "snr": -4.0},
        {"pubkey": "c", "secs_ago": 90, "snr": 2.5},
    ]
    assert [n["pubkey"] for n in sort_neighbours(ns, "heard")] == ["b", "c", "a"]
    assert [n["pubkey"] for n in sort_neighbours(ns, "signal")] == ["a", "c", "b"]
