"""Remote repeater settings: firmware CLI replies, validation, batching."""

from __future__ import annotations

import pytest

from meshcore_console.meshcore import repeater_settings as rs


def test_get_replies_from_firmware() -> None:
    assert rs.parse_get_reply("> 869.618,62.5,8,8") == "869.618,62.5,8,8"
    assert rs.parse_get_reply("> ") == ""  # e.g. an empty guest password
    assert rs.parse_get_reply("??: loop.detect") is rs.UNSUPPORTED  # v1.16, unknown key
    assert rs.parse_get_reply("> unknown") is rs.UNSUPPORTED
    assert rs.parse_get_reply("OK") is None  # a late reply to an earlier set
    assert rs.parse_get_reply(None) is None


def test_set_replies_from_firmware() -> None:
    for ok in ("OK", "OK - reboot to apply", "(OK - stats reset)"):
        assert rs.set_succeeded(ok)
    for bad in (
        "Error: interval range is 3-168 hours",
        "Error, bad chars",
        "unknown config: x",
        "",
    ):
        assert not rs.set_succeeded(bad)


@pytest.mark.parametrize(
    ("key", "text", "sent"),
    [
        ("lat", " 51.3397 ", "51.3397"),
        ("lon", "-0.5", "-0.5"),
        ("advert.interval", "0", "0"),
        ("advert.interval", "120", "120"),
        ("flood.advert.interval", "168", "168"),
        ("radio", "869.618, 62.5, 8, 8", "869.618,62.5,8,8"),
        ("owner.info", "Erik\nLeipzig", "Erik|Leipzig"),
        ("repeat", "off", "off"),
        ("txdelay", "0.50", "0.5"),
        ("guest.password", "", ""),
    ],
)
def test_valid_values_are_sent_as_firmware_expects(key: str, text: str, sent: str) -> None:
    assert rs.validate(rs.BY_KEY[key], text) == sent


@pytest.mark.parametrize(
    ("key", "text"),
    [
        ("lat", "91"),
        ("lon", "east"),
        ("advert.interval", "30"),  # firmware: 60-240 or 0
        ("flood.advert.interval", "2"),  # firmware: 3-168 or 0
        ("flood.max", "65"),
        ("radio", "869.618,62.5,13,8"),
        ("radio", "869.618,62.5,8"),
        ("name", "a,b"),
        ("name", ""),
        ("name", "x" * 32),
        ("repeat", "yes"),
        ("loop.detect", "aggressive"),
    ],
)
def test_values_firmware_would_refuse_are_refused(key: str, text: str) -> None:
    with pytest.raises(ValueError):
        rs.validate(rs.BY_KEY[key], text)


def test_admin_password_command() -> None:
    assert rs.admin_password_command("s3cret") == "password s3cret"
    for bad in ("", "x" * 16, " lead"):
        with pytest.raises(ValueError):
            rs.admin_password_command(bad)


def test_display_value_shows_owner_info_line_breaks() -> None:
    assert rs.display_value(rs.BY_KEY["owner.info"], "Erik|Leipzig") == "Erik\nLeipzig"


def _runner(replies: dict[str, str | None]):
    sent: list[str] = []

    def run(command: str) -> dict:
        sent.append(command)
        text = replies[command]
        return {"success": text is not None, "response_text": text}

    return run, sent


def test_read_skips_unsupported_keys_and_stops_at_a_silent_one() -> None:
    run, sent = _runner(
        {
            "get name": "> DF9ES_Home",
            "get loop.detect": "??: loop.detect",
            "get repeat": None,
            "get tx": "> 22",
        }
    )
    result = rs.read_settings(run, ["name", "loop.detect", "repeat", "tx"])

    assert result.values == {"name": "DF9ES_Home"}
    assert result.unsupported == ["loop.detect"]
    assert result.failed == "repeat"
    assert sent == ["get name", "get loop.detect", "get repeat"]  # tx never asked


def test_apply_reports_each_key_and_stops_after_no_reply() -> None:
    run, sent = _runner(
        {
            "set lat 51.3397": "OK",
            "set flood.advert.interval 2": "Error: interval range is 3-168 hours",
            "set lon 12.3731": None,
            "advert": "OK - Advert sent",
        }
    )
    results = rs.apply_settings(
        run,
        [
            ("set lat 51.3397", "lat"),
            ("set flood.advert.interval 2", "flood.advert.interval"),
            ("set lon 12.3731", "lon"),
            ("advert", "advert"),
        ],
    )

    assert [(r.key, r.ok) for r in results] == [
        ("lat", True),
        ("flood.advert.interval", False),
        ("lon", False),
    ]
    assert "advert" not in sent


def test_only_changed_values_are_sent() -> None:
    lat, radio = rs.BY_KEY["lat"], rs.BY_KEY["radio"]
    assert rs.changed_value(lat, "51.339700", "51.3397") is None  # same number
    assert rs.changed_value(lat, "51.3397", "51.34") == "51.34"
    assert rs.changed_value(radio, "869.618,62.500,8,8", "869.618,62.5,8,8") is None
    assert rs.changed_value(lat, None, "51.0") is None  # never read: never sent
    assert rs.changed_value(rs.BY_KEY["loop.detect"], "aggressive", "") is None
    with pytest.raises(ValueError):
        rs.changed_value(lat, "51.3", "north")


def test_secrets_are_write_only() -> None:
    guest = rs.BY_KEY["guest.password"]
    assert rs.changed_value(guest, None, "") is None
    assert rs.changed_value(guest, None, "hunter2") == "hunter2"
