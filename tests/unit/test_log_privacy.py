"""Logs keep message text, positions and control characters out."""

from __future__ import annotations

import logging

from meshcore_console.meshcore.logging_setup import PrivacyFilter
from meshcore_console.platform.gps import GpsdProvider


def filtered(name: str, msg: str, *args: object) -> str:
    record = logging.LogRecord(name, logging.INFO, __file__, 1, msg, args or None, None)
    assert PrivacyFilter().filter(record)
    return record.getMessage()


def test_decrypted_message_text_is_withheld() -> None:
    assert filtered("Dispatcher", "Received TXT_MSG: meet at the van 21:00") == (
        "Received TXT_MSG: (text withheld)"
    )
    assert filtered("Dispatcher", "<<< Channel [ops] Bob: the code is 1234 >>>") == (
        "<<< Channel [ops] Bob: (text withheld) >>>"
    )
    # other Dispatcher lines and other loggers keep their text
    assert filtered("Dispatcher", "TX %d bytes", 108) == "TX 108 bytes"
    assert filtered("app", "Received TXT_MSG: x") == "Received TXT_MSG: x"


def test_control_characters_cannot_forge_lines_or_drive_the_terminal() -> None:
    out = filtered("app", "peer %s", "hi\n2026-10-27 INFO fake\x1b]0;pwned\x07")
    assert "\n" not in out and "\x1b" not in out and "\x07" not in out
    assert out.startswith("peer hi\\n2026-10-27 INFO fake\\x1b")


def test_gps_fix_is_logged_without_the_position(caplog) -> None:
    gps = GpsdProvider(host="127.0.0.1", port=1)
    with caplog.at_level(logging.DEBUG):
        gps._update_location(52.516389, 13.377778)
    assert "fix acquired" in caplog.text
    assert "52.5" not in caplog.text and "13.3" not in caplog.text


def test_gpsd_position_is_only_reported_with_a_current_fix() -> None:
    gps = GpsdProvider(host="127.0.0.1", port=1)
    gps._update_location(52.5, 13.4)
    assert gps.get_location() == (52.5, 13.4)
    gps._has_fix = False  # what a mode < 2 TPV report or a lost gpsd connection does
    assert gps.get_location() is None
    gps._update_location(52.5, 13.4)
    gps.stop()
    assert gps.get_location() is None


def test_serial_gps_stop_leaves_the_power_rail_alone(monkeypatch) -> None:
    import sys
    import types

    from meshcore_console.platform.gps import UConsoleGps

    calls: list[str] = []
    fake = types.SimpleNamespace(
        output=lambda *a: calls.append("output"),
        cleanup=lambda *a: calls.append("cleanup"),
        LOW=0,
    )
    rpi = types.ModuleType("RPi")
    rpi.GPIO = fake
    monkeypatch.setitem(sys.modules, "RPi", rpi)
    monkeypatch.setitem(sys.modules, "RPi.GPIO", fake)

    gps = UConsoleGps(serial_port="/dev/null")
    gps._gpio_enabled = True
    gps.stop()
    assert calls == []
