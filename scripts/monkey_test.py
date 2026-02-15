#!/usr/bin/env python3
"""Monkey testing script that randomly exercises the UI under mock mode.

Launches the app in mock mode, waits for data to populate, then randomly
clicks buttons, switches views, selects rows, types text, injects fake
packet events, changes settings, and resizes the window. Captures Python
logging and GLib log messages to detect crashes, unhandled exceptions,
and GTK/Pango warnings.

Usage:
    MESHCORE_MOCK=1 python scripts/monkey_test.py [--seed N] [--rounds N] [--interval MS]

Exit code 0 = clean run, 1 = errors/criticals logged.
"""

from __future__ import annotations

import argparse
import logging
import os
import random
import signal
import string
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

os.environ["MESHCORE_MOCK"] = "1"

# Add src to path so we can import without install
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from meshcore_console.meshcore.logging_setup import configure_logging

import gi

configure_logging()

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, GLib, Gio, Gtk

from meshcore_console.app import APP_ID, _load_css
from meshcore_console.core.enums import AnalyzerFilter
from meshcore_console.ui_gtk.windows.main_window import MainWindow


# ---------------------------------------------------------------------------
# Log / warning capture
# ---------------------------------------------------------------------------


class WarningCollector(logging.Handler):
    """Collects WARNING+ log records for summary reporting."""

    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


# GLib log messages (GTK/Pango/GLib warnings that bypass Python logging)
_glib_warnings: list[str] = []
_glib_errors: list[str] = []

_last_action_label = ""  # set by _tick for diagnostic context
_prev_action_label = ""


def _glib_log_handler(
    domain: str,
    level: GLib.LogLevelFlags,
    message: str,
    *_args: object,
) -> None:
    """Custom GLib log handler that captures warnings and errors."""
    formatted = f"[{domain}] {message}" if domain else message

    if level & (GLib.LogLevelFlags.LEVEL_ERROR | GLib.LogLevelFlags.LEVEL_CRITICAL):
        _glib_errors.append(
            f"{formatted}  (after: {_last_action_label}, prev: {_prev_action_label})"
        )
    elif level & GLib.LogLevelFlags.LEVEL_WARNING:
        _glib_warnings.append(f"{formatted}  (after: {_last_action_label})")


# Domains to monitor for GTK/Pango/GLib warnings
_GLIB_LOG_DOMAINS = ["Gtk", "Gdk", "Pango", "GLib", "GLib-GObject", "GLib-GIO", "Adw", "Shumate"]


def _install_glib_log_handlers() -> None:
    """Install log handlers for common GLib domains."""
    catch_flags = (
        GLib.LogLevelFlags.LEVEL_WARNING
        | GLib.LogLevelFlags.LEVEL_ERROR
        | GLib.LogLevelFlags.LEVEL_CRITICAL
    )
    for domain in _GLIB_LOG_DOMAINS:
        GLib.log_set_handler(domain, catch_flags, _glib_log_handler)


# ---------------------------------------------------------------------------
# Fake packet generators (injected into mock service event buffer)
# ---------------------------------------------------------------------------

_FAKE_SENDERS = [
    ("Alice", "peer-alice", "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f60001"),
    ("Bob", "peer-bob", "b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a10002"),
    ("Charlie", "peer-charlie", "c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b20003"),
    ("Diana", "peer-diana", "d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c30004"),
    (
        "Relay Alpha",
        "relay-001",
        "e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d40005",
    ),
    (
        "Node Gateway",
        "gateway-001",
        "f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e50006",
    ),
    (
        "MobileNode-7",
        "mobile-007",
        "0708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f202122232425260007",
    ),
    (
        "SensorHub-X",
        "sensor-x01",
        "1a2b3c4d5e6f1a2b3c4d5e6f1a2b3c4d5e6f1a2b3c4d5e6f1a2b3c4d5e6f0008",
    ),
]

_FAKE_MESSAGES = [
    "Hello mesh! Anyone out there?",
    "Signal check from hillside position",
    "Net status: all nodes green",
    "Forwarding traffic via relay chain",
    "Battery at 47%, switching to low power mode",
    "GPS fix acquired: 37.7749 -122.4194",
    "Testing new firmware v2.1.3 on LoRa module",
    "Emergency: lost contact with node Echo-9",
    "Mesh topology change detected, re-routing",
    "Beacon interval adjusted to 30s",
    "Weather station data: 18C, 65% humidity, 1013hPa",
    "All clear on channel #ops",
    "Acknowledge: packet ABC123 received OK",
    "Routing table updated, 12 known peers",
    "",  # empty payload (edge case)
    "x" * 200,  # very long message (edge case)
    "Unicode test: \u2603 \u2764 \u26a1 \u2708",
]

_FAKE_CHANNELS = ["public", "ops", "test", "emergency", "telemetry"]


def _random_hex(length: int) -> str:
    return "".join(random.choices("0123456789abcdef", k=length))


def _random_rssi() -> int:
    return random.randint(-115, -40)


def _random_snr() -> float:
    return round(random.uniform(-10.0, 15.0), 2)


def _random_hops() -> tuple[int, list[str]]:
    n = random.choices([0, 1, 2, 3, 4], weights=[40, 30, 15, 10, 5])[0]
    hops = [_random_hex(4).upper() for _ in range(n)]
    return n, hops


def _make_packet_event(
    payload_type: int,
    payload_type_name: str,
    route_type: int = 1,
    route_type_name: str = "FLOOD",
    extra: dict | None = None,
) -> dict:
    """Build a mock packet event dict."""
    sender_name, sender_id, sender_pubkey = random.choice(_FAKE_SENDERS)
    path_len, path_hops = _random_hops()
    data: dict = {
        "payload_type": payload_type,
        "payload_type_name": payload_type_name,
        "route_type": route_type,
        "route_type_name": route_type_name,
        "sender_name": sender_name,
        "sender_id": sender_id,
        "sender_pubkey": sender_pubkey,
        "rssi": _random_rssi(),
        "snr": _random_snr(),
        "payload_hex": _random_hex(random.randint(12, 64)),
        "path_len": path_len,
        "path_hops": path_hops,
        "packet_hash": _random_hex(12).upper(),
    }
    if extra:
        data.update(extra)
    return {
        "type": "packet",
        "received_at": datetime.now(UTC).isoformat(),
        "data": data,
    }


def gen_advert_packet() -> dict:
    sender_name, sender_id, sender_pubkey = random.choice(_FAKE_SENDERS)
    return _make_packet_event(
        4,
        "ADVERT",
        extra={
            "advert_name": sender_name,
            "advert_lat": round(random.uniform(37.0, 38.0), 6),
            "advert_lon": round(random.uniform(-123.0, -122.0), 6),
        },
    )


def gen_grp_txt_packet() -> dict:
    return _make_packet_event(
        5,
        "GRP_TXT",
        extra={
            "channel_name": random.choice(_FAKE_CHANNELS),
            "payload_text": random.choice(_FAKE_MESSAGES),
        },
    )


def gen_txt_msg_packet() -> dict:
    return _make_packet_event(
        2,
        "TXT_MSG",
        route_type=2,
        route_type_name="DIRECT",
        extra={
            "payload_text": random.choice(_FAKE_MESSAGES),
        },
    )


def gen_ack_packet() -> dict:
    return _make_packet_event(
        3,
        "ACK",
        route_type=2,
        route_type_name="DIRECT",
        extra={
            "payload_text": "",
            "ack_hash": _random_hex(12).upper(),
        },
    )


def gen_req_packet() -> dict:
    return _make_packet_event(
        0,
        "REQ",
        route_type=2,
        route_type_name="DIRECT",
        extra={
            "request_type": random.choice(["STATUS", "PEER_LIST", "TELEMETRY", "TIME_SYNC"]),
            "payload_text": "",
        },
    )


def gen_response_packet() -> dict:
    return _make_packet_event(
        1,
        "RESPONSE",
        route_type=2,
        route_type_name="DIRECT",
        extra={
            "payload_text": f"ACK {_random_hex(6).upper()} OK",
        },
    )


def gen_path_packet() -> dict:
    return _make_packet_event(8, "PATH", extra={"payload_text": ""})


def gen_trace_packet() -> dict:
    return _make_packet_event(9, "TRACE", extra={"payload_text": ""})


def gen_grp_data_packet() -> dict:
    return _make_packet_event(
        6,
        "GRP_DATA",
        extra={
            "channel_name": random.choice(["telemetry", "sensor-data", "binary"]),
            "payload_text": "",
        },
    )


# Weighted generators for random packet injection
_PACKET_GENERATORS: list[tuple[int, object]] = [
    (20, gen_advert_packet),
    (20, gen_grp_txt_packet),
    (15, gen_txt_msg_packet),
    (10, gen_ack_packet),
    (8, gen_req_packet),
    (8, gen_response_packet),
    (7, gen_path_packet),
    (5, gen_trace_packet),
    (7, gen_grp_data_packet),
]


def gen_random_packet() -> dict:
    """Generate a random packet event using weighted selection."""
    total = sum(w for w, _ in _PACKET_GENERATORS)
    r = random.randint(1, total)
    cumulative = 0
    for weight, gen_fn in _PACKET_GENERATORS:
        cumulative += weight
        if r <= cumulative:
            return gen_fn()
    return gen_advert_packet()  # fallback


# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------

VIEW_PAGES = ["analyzer", "peers", "messages", "map"]


def _find_children(parent: Gtk.Widget, widget_type: type) -> list[Gtk.Widget]:
    """Recursively find all children of a given type."""
    results: list[Gtk.Widget] = []
    child = parent.get_first_child()
    while child is not None:
        if isinstance(child, widget_type):
            results.append(child)
        results.extend(_find_children(child, widget_type))
        child = child.get_next_sibling()
    return results


def _listbox_rows(listbox: Gtk.ListBox) -> list[Gtk.ListBoxRow]:
    """Get all rows from a ListBox."""
    rows: list[Gtk.ListBoxRow] = []
    idx = 0
    while True:
        row = listbox.get_row_at_index(idx)
        if row is None:
            break
        rows.append(row)
        idx += 1
    return rows


def _random_listbox_row(listbox: Gtk.ListBox) -> Gtk.ListBoxRow | None:
    """Pick a random selectable row from a ListBox."""
    rows = [r for r in _listbox_rows(listbox) if r.get_selectable()]
    return random.choice(rows) if rows else None


def _click_button(btn: Gtk.Button) -> None:
    """Emit the clicked signal on a button."""
    btn.emit("clicked")


# ---------------------------------------------------------------------------
# Monkey App
# ---------------------------------------------------------------------------


class MonkeyApp(Adw.Application):
    """App that launches the UI and randomly exercises it."""

    def __init__(self, seed: int, rounds: int, interval_ms: int) -> None:
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.FLAGS_NONE)
        self._seed = seed
        self._rounds = rounds
        self._interval_ms = interval_ms
        self._tick_count = 0
        self._action_counts: dict[str, int] = {}
        self._window: MainWindow | None = None

        from meshcore_console.mock import MockMeshcoreClient

        self.service = MockMeshcoreClient()

    def do_activate(self) -> None:
        _load_css()
        self._window = MainWindow(application=self, service=self.service)
        self._window.present()

        print(
            f"Monkey test: seed={self._seed}, rounds={self._rounds}, interval={self._interval_ms}ms"
        )
        print("Waiting 3s for mock data to populate...")

        # Wait for mock data, then start ticking
        GLib.timeout_add(3000, self._start_ticking)

    def _start_ticking(self) -> bool:
        print("Starting monkey actions...")
        GLib.timeout_add(self._interval_ms, self._tick)
        return False  # one-shot

    def _tick(self) -> bool:
        if self._tick_count >= self._rounds:
            self._finish()
            return False

        self._tick_count += 1
        try:
            self._do_random_action()
        except Exception as exc:
            print(f"  [EXCEPTION] tick {self._tick_count}: {type(exc).__name__}: {exc}")
            _glib_errors.append(f"Python exception in tick {self._tick_count}: {exc}")

        return True  # continue

    def _record_action(self, name: str) -> None:
        global _last_action_label, _prev_action_label
        _prev_action_label = _last_action_label
        _last_action_label = f"tick {self._tick_count}: {name}"
        self._action_counts[name] = self._action_counts.get(name, 0) + 1

    def _get_visible_view(self) -> str:
        if self._window is None:
            return ""
        return self._window._stack.get_visible_child_name() or ""

    def _do_random_action(self) -> None:
        """Pick and execute a weighted random action."""
        view = self._get_visible_view()

        # Build action pool based on current view
        actions: list[tuple[int, str, object]] = []

        # --- Data injection (always available, high weight) ---
        actions.append((12, "inject_packet", self._action_inject_packet))
        actions.append((5, "inject_burst", self._action_inject_burst))

        # --- Navigation (always available) ---
        actions.append((10, "switch_view", self._action_switch_view))
        actions.append((4, "settings", self._action_open_settings))

        # --- Header bar (always available) ---
        actions.append((3, "connect_toggle", self._action_connect_toggle))
        actions.append((3, "advert_popover", self._action_advert_popover))

        # --- View-specific actions ---
        if view == "analyzer":
            actions.append((8, "analyzer_filter", self._action_analyzer_filter))
            actions.append((4, "analyzer_pause", self._action_analyzer_pause))
            actions.append((6, "analyzer_select_row", self._action_analyzer_select_row))
            actions.append((3, "analyzer_close_details", self._action_analyzer_close_details))

        elif view == "peers":
            actions.append((6, "peers_select_contact", self._action_peers_select_contact))
            actions.append((6, "peers_select_repeater", self._action_peers_select_repeater))
            actions.append((4, "peers_send_message", self._action_peers_send_message))

        elif view == "messages":
            actions.append((6, "messages_select_channel", self._action_messages_select_channel))
            actions.append((5, "messages_click_bubble", self._action_messages_click_bubble))
            actions.append((3, "messages_click_badge", self._action_messages_click_badge))
            actions.append((3, "messages_close_details", self._action_messages_close_details))
            actions.append((6, "messages_send_text", self._action_messages_send_text))
            actions.append((3, "messages_send_empty", self._action_messages_send_empty))

        elif view == "map":
            actions.append((5, "map_zoom_in", self._action_map_zoom_in))
            actions.append((5, "map_zoom_out", self._action_map_zoom_out))
            actions.append((3, "map_center", self._action_map_center))
            actions.append((3, "map_simulate", self._action_map_simulate))

        elif view == "settings":
            actions.append((6, "settings_preset", self._action_settings_preset))
            actions.append((5, "settings_toggle_switch", self._action_settings_toggle_switch))
            actions.append((5, "settings_type_entry", self._action_settings_type_entry))
            actions.append((4, "settings_save", self._action_settings_save))
            actions.append((3, "settings_reload", self._action_settings_reload))

        # --- Stress / edge cases (low weight) ---
        actions.append((2, "rapid_switch", self._action_rapid_switch))
        actions.append((2, "resize_window", self._action_resize_window))

        # Weighted random selection
        total = sum(w for w, _, _ in actions)
        r = random.randint(1, total)
        cumulative = 0
        for weight, name, fn in actions:
            cumulative += weight
            if r <= cumulative:
                self._record_action(name)
                fn()
                return

    # ===================================================================
    # Data injection actions
    # ===================================================================

    def _action_inject_packet(self) -> None:
        """Inject a single random packet into the mock event buffer."""
        self.service._event_buffer.append(gen_random_packet())

    def _action_inject_burst(self) -> None:
        """Inject a burst of 3-8 random packets (simulates busy network)."""
        n = random.randint(3, 8)
        for _ in range(n):
            self.service._event_buffer.append(gen_random_packet())

    # ===================================================================
    # Navigation actions
    # ===================================================================

    def _action_switch_view(self) -> None:
        page = random.choice(VIEW_PAGES)
        if self._window is None:
            return
        self._window._switch_to_page(page)
        for name, btn in self._window._nav_buttons.items():
            btn.set_active(name == page)

    def _action_open_settings(self) -> None:
        if self._window is None:
            return
        for btn in self._window._nav_buttons.values():
            btn.set_active(False)
        self._window._switch_to_page("settings")

    # ===================================================================
    # Header bar actions
    # ===================================================================

    def _action_connect_toggle(self) -> None:
        if self._window is None:
            return
        _click_button(self._window._connect_button)

    def _action_advert_popover(self) -> None:
        if self._window is None:
            return
        popover = self._window._advert_btn.get_popover()
        if popover is None:
            return
        if popover.get_visible():
            popover.popdown()
        else:
            popover.popup()
            # Click one of the advert buttons
            advert_box = popover.get_child()
            if advert_box is not None:
                buttons = _find_children(advert_box, Gtk.Button)
                if buttons:
                    btn = random.choice(buttons)
                    GLib.timeout_add(50, lambda: _click_button(btn) or False)

    # ===================================================================
    # Analyzer actions
    # ===================================================================

    def _action_analyzer_filter(self) -> None:
        if self._window is None:
            return
        analyzer = self._window._stack.get_child_by_name("analyzer")
        if analyzer is None:
            return
        filter_type = random.choice(list(AnalyzerFilter))
        buttons = getattr(analyzer, "_filter_buttons", {})
        if filter_type in buttons:
            _click_button(buttons[filter_type])

    def _action_analyzer_pause(self) -> None:
        if self._window is None:
            return
        analyzer = self._window._stack.get_child_by_name("analyzer")
        if analyzer is None:
            return
        pause_btn = getattr(analyzer, "_pause", None)
        if pause_btn is not None:
            pause_btn.set_active(not pause_btn.get_active())

    def _action_analyzer_select_row(self) -> None:
        if self._window is None:
            return
        analyzer = self._window._stack.get_child_by_name("analyzer")
        if analyzer is None:
            return
        stream = getattr(analyzer, "_stream", None)
        if stream is None:
            return
        row = _random_listbox_row(stream)
        if row is not None:
            stream.select_row(row)

    def _action_analyzer_close_details(self) -> None:
        if self._window is None:
            return
        analyzer = self._window._stack.get_child_by_name("analyzer")
        if analyzer is None:
            return
        revealer = getattr(analyzer, "_details_revealer", None)
        if revealer is not None and revealer.get_reveal_child():
            details = getattr(analyzer, "_details", None)
            if details is not None:
                close_buttons = _find_children(details, Gtk.Button)
                if close_buttons:
                    _click_button(close_buttons[0])

    # ===================================================================
    # Peers actions
    # ===================================================================

    def _action_peers_select_contact(self) -> None:
        if self._window is None:
            return
        peers = self._window._stack.get_child_by_name("peers")
        if peers is None:
            return
        contacts_list = getattr(peers, "_contacts_list", None)
        if contacts_list is None:
            return
        row = _random_listbox_row(contacts_list)
        if row is not None:
            contacts_list.select_row(row)

    def _action_peers_select_repeater(self) -> None:
        if self._window is None:
            return
        peers = self._window._stack.get_child_by_name("peers")
        if peers is None:
            return
        network_list = getattr(peers, "_network_list", None)
        if network_list is None:
            return
        row = _random_listbox_row(network_list)
        if row is not None:
            network_list.select_row(row)

    def _action_peers_send_message(self) -> None:
        if self._window is None:
            return
        peers = self._window._stack.get_child_by_name("peers")
        if peers is None:
            return
        details_content = getattr(peers, "_details_content", None)
        if details_content is None:
            return
        buttons = _find_children(details_content, Gtk.Button)
        send_buttons = [b for b in buttons if b.get_label() == "Send Message"]
        if send_buttons:
            _click_button(random.choice(send_buttons))

    # ===================================================================
    # Messages actions
    # ===================================================================

    def _action_messages_select_channel(self) -> None:
        if self._window is None:
            return
        messages = self._window._stack.get_child_by_name("messages")
        if messages is None:
            return
        channel_list = getattr(messages, "_channel_list", None)
        if channel_list is None:
            return
        row = _random_listbox_row(channel_list)
        if row is not None:
            channel_list.select_row(row)

    def _action_messages_click_bubble(self) -> None:
        if self._window is None:
            return
        messages = self._window._stack.get_child_by_name("messages")
        if messages is None:
            return
        message_box = getattr(messages, "_message_box", None)
        if message_box is None:
            return
        from meshcore_console.ui_gtk.widgets.message_bubble import MessageBubble

        # Find MessageBubble children, then get their bubble button (plain Gtk.Button, not NodeBadge)
        bubbles = _find_children(message_box, MessageBubble)
        bubble_buttons: list[Gtk.Button] = []
        for bubble in bubbles:
            child = bubble.get_first_child()
            while child is not None:
                if type(child) is Gtk.Button:
                    bubble_buttons.append(child)
                    break
                child = child.get_next_sibling()
        if bubble_buttons:
            _click_button(random.choice(bubble_buttons))

    def _action_messages_click_badge(self) -> None:
        """Click a NodeBadge inside a message bubble (tests popover lifecycle)."""
        if self._window is None:
            return
        messages = self._window._stack.get_child_by_name("messages")
        if messages is None:
            return
        message_box = getattr(messages, "_message_box", None)
        if message_box is None:
            return
        from meshcore_console.ui_gtk.widgets.node_badge import NodeBadge

        badges = [b for b in _find_children(message_box, NodeBadge) if b.get_realized()]
        if badges:
            # Dismiss any existing popover before clicking a new badge
            for b in badges:
                pop = getattr(b, "_popover", None)
                if pop is not None and pop.get_visible():
                    pop.popdown()
            badge = random.choice(badges)
            _click_button(badge)

    def _action_messages_close_details(self) -> None:
        if self._window is None:
            return
        messages = self._window._stack.get_child_by_name("messages")
        if messages is None:
            return
        revealer = getattr(messages, "_details_revealer", None)
        if revealer is not None and revealer.get_reveal_child():
            details_box = getattr(messages, "_details_box", None)
            if details_box is not None:
                close_buttons = [
                    b for b in _find_children(details_box, Gtk.Button) if b.get_label() == "Close"
                ]
                if close_buttons:
                    _click_button(close_buttons[0])

    def _action_messages_send_text(self) -> None:
        if self._window is None:
            return
        messages = self._window._stack.get_child_by_name("messages")
        if messages is None:
            return
        entry = getattr(messages, "_entry", None)
        send_btn = getattr(messages, "_send_button", None)
        if entry is None or send_btn is None:
            return
        text = random.choice(
            [
                # Normal messages
                "".join(
                    random.choices(
                        string.ascii_letters + string.digits + " ", k=random.randint(3, 30)
                    )
                ),
                # Unicode
                "Testing \u2603 \u26a1 \u2764 emoji support",
                # Very long
                "x" * random.randint(100, 300),
                # Numbers only
                str(random.randint(0, 999999)),
                # Special chars
                "<script>alert('xss')</script>",
                "path/to/file.txt",
                "user@example.com",
                "https://example.com/test?foo=bar&baz=1",
            ]
        )
        entry.set_text(text)
        _click_button(send_btn)

    def _action_messages_send_empty(self) -> None:
        if self._window is None:
            return
        messages = self._window._stack.get_child_by_name("messages")
        if messages is None:
            return
        entry = getattr(messages, "_entry", None)
        send_btn = getattr(messages, "_send_button", None)
        if entry is None or send_btn is None:
            return
        entry.set_text("")
        _click_button(send_btn)

    # ===================================================================
    # Map actions
    # ===================================================================

    def _action_map_zoom_in(self) -> None:
        if self._window is None:
            return
        map_view = self._window._stack.get_child_by_name("map")
        if map_view is None:
            return
        buttons = _find_children(map_view, Gtk.Button)
        for btn in buttons:
            if btn.get_icon_name() == "zoom-in-symbolic":
                _click_button(btn)
                return

    def _action_map_zoom_out(self) -> None:
        if self._window is None:
            return
        map_view = self._window._stack.get_child_by_name("map")
        if map_view is None:
            return
        buttons = _find_children(map_view, Gtk.Button)
        for btn in buttons:
            if btn.get_icon_name() == "zoom-out-symbolic":
                _click_button(btn)
                return

    def _action_map_center(self) -> None:
        if self._window is None:
            return
        map_view = self._window._stack.get_child_by_name("map")
        if map_view is None:
            return
        center_btn = getattr(map_view, "_center_btn", None)
        if center_btn is not None:
            _click_button(center_btn)

    def _action_map_simulate(self) -> None:
        if self._window is None:
            return
        map_view = self._window._stack.get_child_by_name("map")
        if map_view is None:
            return
        buttons = _find_children(map_view, Gtk.Button)
        for btn in buttons:
            if btn.get_icon_name() == "media-skip-forward-symbolic":
                _click_button(btn)
                return

    # ===================================================================
    # Settings actions
    # ===================================================================

    def _action_settings_preset(self) -> None:
        if self._window is None:
            return
        settings = self._window._stack.get_child_by_name("settings")
        if settings is None:
            return
        preset = getattr(settings, "_preset", None)
        if preset is None:
            return
        presets = ["meshcore-us", "meshcore-eu", "custom"]
        preset.set_active_id(random.choice(presets))

    def _action_settings_toggle_switch(self) -> None:
        if self._window is None:
            return
        settings = self._window._stack.get_child_by_name("settings")
        if settings is None:
            return
        switches = getattr(settings, "_switches", {})
        if not switches:
            return
        key = random.choice(list(switches.keys()))
        sw = switches[key]
        sw.set_active(not sw.get_active())

    def _action_settings_type_entry(self) -> None:
        if self._window is None:
            return
        settings = self._window._stack.get_child_by_name("settings")
        if settings is None:
            return
        entries = getattr(settings, "_entries", {})
        if not entries:
            return
        key = random.choice(list(entries.keys()))
        entry = entries[key]
        # Type plausible values based on field
        if key == "node_name":
            entry.set_text(
                random.choice(
                    [
                        "".join(random.choices(string.ascii_letters, k=random.randint(3, 12))),
                        "test-node-" + str(random.randint(1, 99)),
                        "",  # empty name (edge case)
                        "A" * 50,  # very long name
                    ]
                )
            )
        elif key in ("latitude", "longitude"):
            entry.set_text(
                random.choice(
                    [
                        f"{random.uniform(-90, 90):.6f}",
                        "0",
                        "invalid",  # non-numeric (edge case)
                        "",  # empty
                    ]
                )
            )
        elif key == "frequency":
            entry.set_text(
                random.choice(
                    [
                        f"{random.uniform(900, 930):.6f}",
                        "915.0",
                        "abc",  # invalid
                    ]
                )
            )
        elif key == "bandwidth":
            entry.set_text(str(random.choice([125, 250, 500, 0, -1])))
        else:
            entry.set_text(
                random.choice(
                    [
                        str(random.randint(0, 30)),
                        "0",
                        "",
                        "bad",
                    ]
                )
            )

    def _action_settings_save(self) -> None:
        if self._window is None:
            return
        settings = self._window._stack.get_child_by_name("settings")
        if settings is None:
            return
        buttons = _find_children(settings, Gtk.Button)
        for btn in buttons:
            if btn.get_label() == "Save Settings":
                _click_button(btn)
                return

    def _action_settings_reload(self) -> None:
        if self._window is None:
            return
        settings = self._window._stack.get_child_by_name("settings")
        if settings is None:
            return
        buttons = _find_children(settings, Gtk.Button)
        for btn in buttons:
            if btn.get_label() == "Reload":
                _click_button(btn)
                return

    # ===================================================================
    # Stress / edge case actions
    # ===================================================================

    def _action_rapid_switch(self) -> None:
        """Rapidly switch views 3 times back-to-back."""
        if self._window is None:
            return
        for _ in range(3):
            page = random.choice(VIEW_PAGES)
            self._window._switch_to_page(page)
            for name, btn in self._window._nav_buttons.items():
                btn.set_active(name == page)

    def _action_resize_window(self) -> None:
        if self._window is None:
            return
        w = random.randint(800, 1920)
        h = random.randint(400, 1080)
        self._window.set_default_size(w, h)

    # ===================================================================
    # Finish
    # ===================================================================

    def _finish(self) -> None:
        """Print summary and quit."""
        print(f"\n{'=' * 60}")
        print(f"Monkey test complete: {self._rounds} actions")
        print(f"Seed: {self._seed}")
        print("\nAction distribution:")
        for name, count in sorted(self._action_counts.items(), key=lambda x: -x[1]):
            print(f"  {name}: {count}")

        # Count Python errors (ERROR/CRITICAL)
        py_errors = [r for r in warning_collector.records if r.levelno >= logging.ERROR]
        py_warnings = [r for r in warning_collector.records if r.levelno < logging.ERROR]

        print(f"\nGLib errors/criticals: {len(_glib_errors)}")
        print(f"GLib warnings: {len(_glib_warnings)}")
        print(f"Python errors/criticals: {len(py_errors)}")
        print(f"Python warnings: {len(py_warnings)}")

        if _glib_errors:
            print("\n--- GLib Errors ---")
            for msg in _glib_errors:
                print(f"  ERROR: {msg}")

        if _glib_warnings:
            print("\n--- GLib Warnings ---")
            for msg in _glib_warnings[:20]:
                print(f"  WARN: {msg}")
            if len(_glib_warnings) > 20:
                print(f"  ... and {len(_glib_warnings) - 20} more")

        if py_errors:
            print("\n--- Python Errors ---")
            for r in py_errors:
                print(f"  {r.levelname}: [{r.name}] {r.getMessage()}")

        if py_warnings:
            print("\n--- Python Warnings ---")
            for r in py_warnings[:20]:
                print(f"  {r.levelname}: [{r.name}] {r.getMessage()}")
            if len(py_warnings) > 20:
                print(f"  ... and {len(py_warnings) - 20} more")

        total_errors = len(_glib_errors) + len(py_errors)
        warn_count = len(_glib_warnings) + len(py_warnings)
        print(f"\n{'=' * 60}")
        if total_errors:
            print(f"FAIL: {total_errors} error(s) detected")
        else:
            print(f"OK: {self._rounds} actions, {warn_count} warning(s)")
        print(f"{'=' * 60}")

        self._exit_code = 1 if total_errors else 0
        self.quit()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

# Install warning collector before anything else
warning_collector = WarningCollector()
logging.root.addHandler(warning_collector)


def main() -> None:
    parser = argparse.ArgumentParser(description="Monkey test the GTK UI")
    parser.add_argument("--seed", type=int, default=None, help="Random seed for reproducibility")
    parser.add_argument(
        "--rounds", type=int, default=200, help="Number of random actions (default: 200)"
    )
    parser.add_argument(
        "--interval", type=int, default=300, help="Milliseconds between actions (default: 300)"
    )
    args = parser.parse_args()

    seed = args.seed if args.seed is not None else int(time.time() * 1000) % (2**31)
    random.seed(seed)

    # Install GLib log handlers to capture GTK/Pango warnings
    _install_glib_log_handlers()

    app = MonkeyApp(seed=seed, rounds=args.rounds, interval_ms=args.interval)
    GLib.unix_signal_add(GLib.PRIORITY_HIGH, signal.SIGINT, lambda: app.quit() or True)
    app.run([])
    sys.exit(getattr(app, "_exit_code", 0))


if __name__ == "__main__":
    main()
