"""Remote repeater settings over the CLI: what can be set, and how to talk about it.

A MeshCore repeater has no "dump all settings" request: every setting is one
``get <key>`` / ``set <key> <value>`` CLI round trip (admin login only), about
2-3 s each to a direct neighbour.  This module is the pure part: the schema
(keys, types and the limits firmware v1.16-v1.18 enforces), reply parsing and
command building.  Keys a repeater's firmware doesn't know answer with
something other than ``> value``; the UI hides those instead of keeping a
per-version table.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

IDENTITY = "Identity & location"
ADVERTS = "Adverts"
REPEATING = "Repeating"
TIMING = "Timing"
RADIO = "Radio"
ACCESS = "Access"
GROUPS = (IDENTITY, ADVERTS, REPEATING, TIMING, RADIO, ACCESS)

NAME_FORBIDDEN = "[]\\:,?*"  # firmware isValidName()


@dataclass(frozen=True)
class RepeaterSetting:
    key: str  # CLI key: "get <key>" / "set <key> <value>"
    label: str
    group: str
    kind: str  # "text" | "int" | "float" | "onoff" | "choice" | "radio" | "secret"
    help: str = ""
    unit: str = ""
    minimum: float | None = None
    maximum: float | None = None
    zero_is_off: bool = False  # 0 is accepted outside [minimum, maximum]
    choices: tuple[str, ...] = ()
    max_len: int = 0
    reboot: bool = False  # takes effect after a reboot
    dangerous: bool = False  # a wrong value can make the repeater unreachable


SETTINGS: tuple[RepeaterSetting, ...] = (
    RepeaterSetting("name", "Name", IDENTITY, "text", max_len=31),
    RepeaterSetting(
        "lat",
        "Latitude",
        IDENTITY,
        "float",
        help="Static position sent in the repeater's adverts.",
        minimum=-90,
        maximum=90,
    ),
    RepeaterSetting("lon", "Longitude", IDENTITY, "float", minimum=-180, maximum=180),
    RepeaterSetting(
        "owner.info",
        "Owner info",
        IDENTITY,
        "text",
        help="Free text, e.g. contact details. Line breaks are kept.",
        max_len=119,
    ),
    RepeaterSetting(
        "advert.interval",
        "Local advert every",
        ADVERTS,
        "int",
        help="Zero-hop advert for direct neighbours. 0 = off.",
        unit="min",
        minimum=60,
        maximum=240,
        zero_is_off=True,
    ),
    RepeaterSetting(
        "flood.advert.interval",
        "Flood advert every",
        ADVERTS,
        "int",
        help="Advert flooded across the mesh. 0 = off.",
        unit="h",
        minimum=3,
        maximum=168,
        zero_is_off=True,
    ),
    RepeaterSetting("repeat", "Repeat packets", REPEATING, "onoff"),
    RepeaterSetting(
        "flood.max", "Max flood hops", REPEATING, "int", unit="hops", minimum=0, maximum=64
    ),
    RepeaterSetting(
        "flood.max.advert",
        "Max flood hops (adverts)",
        REPEATING,
        "int",
        unit="hops",
        minimum=0,
        maximum=64,
    ),
    RepeaterSetting(
        "flood.max.unscoped",
        "Max flood hops (unscoped)",
        REPEATING,
        "int",
        unit="hops",
        minimum=0,
        maximum=64,
    ),
    RepeaterSetting(
        "loop.detect",
        "Loop detection",
        REPEATING,
        "choice",
        choices=("off", "minimal", "moderate", "strict"),
    ),
    RepeaterSetting(
        "path.hash.mode",
        "Path hash size",
        REPEATING,
        "choice",
        help="0 = 1-byte, 1 = 2-byte, 2 = 3-byte hop hashes.",
        choices=("0", "1", "2"),
    ),
    RepeaterSetting("multi.acks", "Multiple ACKs", REPEATING, "choice", choices=("0", "1")),
    RepeaterSetting(
        "af", "Airtime factor", TIMING, "float", help="Duty-cycle budget factor.", minimum=0
    ),
    RepeaterSetting("rxdelay", "RX delay base", TIMING, "float", minimum=0, maximum=20),
    RepeaterSetting("txdelay", "Flood TX delay factor", TIMING, "float", minimum=0, maximum=2),
    RepeaterSetting(
        "direct.txdelay", "Direct TX delay factor", TIMING, "float", minimum=0, maximum=2
    ),
    RepeaterSetting(
        "radio",
        "Frequency, bandwidth, SF, CR",
        RADIO,
        "radio",
        help="MHz,kHz,SF,CR e.g. 869.618,62.5,8,8 (EU/UK Narrow).",
        reboot=True,
        dangerous=True,
    ),
    RepeaterSetting("tx", "TX power", RADIO, "int", unit="dBm", minimum=-9, maximum=30),
    RepeaterSetting("allow.read.only", "Allow read-only logins", ACCESS, "onoff"),
    RepeaterSetting(
        "guest.password",
        "Guest password",
        ACCESS,
        "secret",
        help="Empty = guests log in without a password.",
        max_len=15,
    ),
)

BY_KEY = {s.key: s for s in SETTINGS}


UNSUPPORTED = object()  # parse_get_reply(): the firmware doesn't know the key


def parse_get_reply(reply: str | None) -> str | object | None:
    """The value from a ``get`` reply ("> 869.618,62.5,8,8"); UNSUPPORTED when
    the firmware doesn't know the key ("??: key", "> unknown", an error); None
    for anything else, e.g. a late reply to an earlier command."""
    if reply is None:
        return None
    if reply.startswith(">"):
        value = reply[1:].strip()
        return UNSUPPORTED if value == "unknown" else value
    if reply.startswith(("??", "unknown", "Err", "ERR")):
        return UNSUPPORTED
    return None


def set_succeeded(reply: str | None) -> bool:
    """Firmware answers a successful ``set`` with "OK", "OK - reboot to apply",
    "OK - repeat is now ON", ...; errors start with "Error"/"ERROR"/"unknown"."""
    return bool(reply) and reply.lstrip("(").upper().startswith("OK")


def display_value(setting: RepeaterSetting, value: str) -> str:
    """A ``get`` value as the form shows it (owner info keeps line breaks as "|")."""
    return value.replace("|", "\n") if setting.key == "owner.info" else value


def validate(setting: RepeaterSetting, text: str) -> str:
    """The value to send for ``text``, or ValueError with a message for the form."""
    text = text.strip() if setting.kind != "text" else text
    if setting.kind == "text":
        value = text.replace("\n", "|") if setting.key == "owner.info" else text.strip()
        if setting.key == "name":
            if not value:
                raise ValueError("Name can't be empty")
            bad = sorted({c for c in value if c in NAME_FORBIDDEN})
            if bad:
                raise ValueError(f"Name can't contain {' '.join(bad)}")
        if setting.max_len and len(value.encode()) > setting.max_len:
            raise ValueError(f"At most {setting.max_len} bytes")
        return value
    if setting.kind == "secret":
        if setting.max_len and len(text.encode()) > setting.max_len:
            raise ValueError(f"At most {setting.max_len} characters")
        return text
    if setting.kind == "onoff":
        if text not in ("on", "off"):
            raise ValueError("on or off")
        return text
    if setting.kind == "choice":
        if text not in setting.choices:
            raise ValueError("One of " + ", ".join(setting.choices))
        return text
    if setting.kind == "radio":
        return _validate_radio(text)
    number = _number(setting, text)
    return str(int(number)) if setting.kind == "int" else _float_text(number)


def changed_value(setting: RepeaterSetting, loaded: str | None, text: str) -> str | None:
    """What to send for a form field showing ``text``: None if it equals the
    value read from the repeater (or nothing was read); ValueError if invalid.
    A secret is never read back: any text in its field is a change."""
    if setting.kind == "secret":
        return validate(setting, text) if text else None
    if loaded is None:
        return None
    if setting.kind == "choice" and not text:
        return None  # the repeater has a value this app doesn't list; left alone
    value = validate(setting, text)
    if setting.kind in ("float", "radio"):
        try:  # compare as the app would send it, not as firmware printed it
            loaded = validate(setting, loaded)
        except ValueError:
            pass
    return None if value == loaded else value


def set_command(setting: RepeaterSetting, value: str) -> str:
    return f"set {setting.key} {value}"


# A CLI round trip: command -> the client's result dict ("success",
# "response_text"); MeshcoreService.send_repeater_command bound to a repeater.
RunCommand = Callable[[str], dict]


@dataclass
class ReadResult:
    values: dict[str, str] = field(default_factory=dict)
    unsupported: list[str] = field(default_factory=list)
    failed: str | None = None  # the key reading stopped at (no or odd reply)


def read_settings(run: RunCommand, keys: list[str]) -> ReadResult:
    """``get`` each key in turn.

    Stops at the first key without a usable reply: CLI replies carry no
    request id, so a reply arriving after its timeout would otherwise be
    taken for the answer to the next key."""
    result = ReadResult()
    for key in keys:
        reply = run(f"get {key}")
        value = parse_get_reply(reply.get("response_text") if reply.get("success") else None)
        if value is UNSUPPORTED:
            result.unsupported.append(key)
        elif value is None:
            result.failed = key
            break
        else:
            result.values[key] = str(value)
    return result


@dataclass
class ApplyResult:
    key: str
    ok: bool
    reply: str  # the repeater's reply, or why there is none


def apply_settings(run: RunCommand, changes: list[tuple[str, str]]) -> list[ApplyResult]:
    """Send ``(command, key)`` pairs in turn; stops after the first one without
    a reply (same reason as read_settings)."""
    results: list[ApplyResult] = []
    for command, key in changes:
        reply = run(command)
        text = reply.get("response_text") if reply.get("success") else None
        if text is None:
            results.append(ApplyResult(key, False, "No reply; stopped here"))
            break
        results.append(ApplyResult(key, set_succeeded(text), text))
    return results


def admin_password_command(password: str) -> str:
    """The admin password is changed with the ``password`` command, not ``set``."""
    if not password or len(password.encode()) > 15:
        raise ValueError("1-15 characters")
    if password != password.strip():
        raise ValueError("No leading or trailing spaces")
    return f"password {password}"


def _number(setting: RepeaterSetting, text: str) -> float:
    try:
        number = int(text) if setting.kind == "int" else float(text)
    except ValueError as exc:
        raise ValueError(
            "Must be a whole number" if setting.kind == "int" else "Must be a number"
        ) from exc
    if setting.zero_is_off and number == 0:
        return number
    low, high = setting.minimum, setting.maximum
    if (low is not None and number < low) or (high is not None and number > high):
        span = f"{low:g}-{high:g}" if low is not None and high is not None else ""
        if not span:
            span = f">= {low:g}" if low is not None else f"<= {high:g}"
        raise ValueError(f"Range {span}{' (or 0 = off)' if setting.zero_is_off else ''}")
    return number


def _validate_radio(text: str) -> str:
    """ "freq,bw,sf,cr" within the ranges firmware accepts (set radio)."""
    parts = [p.strip() for p in text.split(",")]
    if len(parts) != 4:
        raise ValueError("Four values: MHz,kHz,SF,CR")
    try:
        freq, bw = float(parts[0]), float(parts[1])
        sf, cr = int(parts[2]), int(parts[3])
    except ValueError as exc:
        raise ValueError("Numbers: MHz,kHz,SF,CR") from exc
    if not (150 <= freq <= 2500 and 7 <= bw <= 500 and 5 <= sf <= 12 and 5 <= cr <= 8):
        raise ValueError("150-2500 MHz, 7-500 kHz, SF 5-12, CR 5-8")
    return f"{_float_text(freq)},{_float_text(bw)},{sf},{cr}"


def _float_text(number: float) -> str:
    return f"{number:.6f}".rstrip("0").rstrip(".")
