from __future__ import annotations

import hashlib
import logging
import os
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, cast

import gi

logger = logging.getLogger(__name__)

gi.require_version("Gtk", "4.0")

from gi.repository import GLib, Gtk, Pango

from meshcore_console.core.enums import AnalyzerFilter, EventType
from meshcore_console.core.packets import get_handler
from meshcore_console.core.services import MeshcoreService
from meshcore_console.ui_gtk.state import UiEventStore
from meshcore_console.ui_gtk.widgets import DaySeparator, DetailBlock, PathVisualization
from meshcore_console.ui_gtk.widgets.node_badge import STYLE_DEFAULT, STYLE_SELF


@dataclass(slots=True)
class PacketRecord:
    timestamp: str
    date: str  # YYYY-MM-DD for day-change detection
    packet_type: str
    node: str
    content: str
    rssi: int
    snr: float
    packet_id: str
    raw_hex: str
    payload_text: str
    route_type: str
    path_len: int
    path_hops: list[str]
    packet_hash: str
    channel_name: str = ""


class AnalyzerView(Gtk.Box):
    COL_TIME = 95
    COL_TYPE = 90
    COL_NODE = 140
    COL_SIGNAL = 100  # Signal column is fixed, content expands

    def __init__(self, service: MeshcoreService, event_store: UiEventStore) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self._service = service
        self._event_store = event_store
        self._geom_debug = os.environ.get("MESHCORE_UI_GEOM_DEBUG", "0") == "1"
        self._cursor = 0
        self._packets: deque[PacketRecord] = deque(maxlen=400)
        self._selected_packet: PacketRecord | None = None
        self._paused = False
        self._active_filter = AnalyzerFilter.ALL

        toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        toolbar.add_css_class("panel-card")
        toolbar.add_css_class("analyzer-toolbar")
        self.append(toolbar)

        self._filter_buttons: dict[AnalyzerFilter, Gtk.ToggleButton] = {}
        for filter_type in AnalyzerFilter:
            button = Gtk.ToggleButton.new_with_label(filter_type.value)
            button.add_css_class("analyzer-filter")
            button.add_css_class(f"filter-{self._type_class(filter_type.value)}")
            button.connect("clicked", self._on_filter_clicked, filter_type)
            self._filter_buttons[filter_type] = button
            toolbar.append(button)
        self._filter_buttons[AnalyzerFilter.ALL].set_active(True)

        # Spacer to push play/pause to the right
        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        toolbar.append(spacer)

        # Play/pause toggle button with icon
        self._pause = Gtk.ToggleButton()
        self._pause_icon = Gtk.Image.new_from_icon_name("media-playback-pause-symbolic")
        self._pause.set_child(self._pause_icon)
        self._pause.add_css_class("circular")
        self._pause.set_tooltip_text("Pause stream")
        self._pause.connect("toggled", self._on_pause_toggled)
        toolbar.append(self._pause)

        # Main content area with stream and details overlay
        center_overlay = Gtk.Overlay.new()
        center_overlay.set_hexpand(True)
        center_overlay.set_vexpand(True)
        self.append(center_overlay)

        center = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        center.set_hexpand(True)
        center.set_vexpand(True)
        center_overlay.set_child(center)

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        header.add_css_class("analyzer-stream-header")
        # Align with stream rows: panel-card padding (14px) + row border (3px) + row margin (4px)
        header.set_margin_start(21)
        header.set_margin_end(18)
        header.append(self._header_label("TIME", self.COL_TIME))
        header.append(self._header_label("TYPE", self.COL_TYPE))
        header.append(self._header_label("NODE", self.COL_NODE))
        # Content header expands to fill space
        content_header = self._header_label("CONTENT", -1)
        content_header.set_hexpand(True)
        header.append(content_header)
        header.append(self._header_label("SIGNAL", self.COL_SIGNAL))
        center.append(header)

        # Wrap stream in ScrolledWindow to prevent infinite expansion
        stream_scroll = Gtk.ScrolledWindow.new()
        stream_scroll.set_vexpand(True)
        stream_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        self._stream = Gtk.ListBox.new()
        self._stream.add_css_class("panel-card")
        self._stream.add_css_class("analyzer-stream")
        self._stream.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self._stream.connect("row-selected", self._on_packet_selected)
        stream_scroll.set_child(self._stream)
        center.append(stream_scroll)

        self._details_revealer = Gtk.Revealer.new()
        self._details_revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_LEFT)
        self._details_revealer.set_transition_duration(220)
        self._details_revealer.set_reveal_child(False)
        self._details_revealer.set_halign(Gtk.Align.END)
        self._details_revealer.set_valign(Gtk.Align.FILL)
        center_overlay.add_overlay(self._details_revealer)
        center_overlay.set_measure_overlay(self._details_revealer, False)

        # Use fixed-width box for details panel. Content must pre-wrap
        # long strings to avoid natural width explosion in GTK4.
        self._details = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self._details.add_css_class("panel-card")
        self._details.add_css_class("analyzer-details")
        self._details.add_css_class("analyzer-drawer")
        self._details.set_size_request(220, -1)
        self._details_revealer.set_child(self._details)

        GLib.timeout_add(1500, self._poll_events)
        # Load stored packets from persistent storage
        self._load_stored_packets()
        self._refresh_all()

    def _load_stored_packets(self) -> None:
        """Load packets from persistent storage on startup."""
        try:
            stored = self._service.list_stored_packets(limit=400)
            # stored is chronological [oldest...newest], appendleft reverses to newest-first
            for packet_event in stored:
                record = self._event_to_record(packet_event)
                if record is not None:
                    self._packets.appendleft(record)
            if stored:
                logger.debug("AnalyzerView: loaded %d packets from storage", len(self._packets))
        except (OSError, ValueError) as e:
            logger.warning("AnalyzerView: error loading stored packets: %s", e)

    def _on_pause_toggled(self, button: Gtk.ToggleButton) -> None:
        self._paused = button.get_active()
        if self._paused:
            self._pause_icon.set_from_icon_name("media-playback-start-symbolic")
            button.set_tooltip_text("Resume stream")
        else:
            self._pause_icon.set_from_icon_name("media-playback-pause-symbolic")
            button.set_tooltip_text("Pause stream")

    def _on_filter_clicked(self, _button: Gtk.ToggleButton, filter_type: AnalyzerFilter) -> None:
        self._active_filter = filter_type
        for key, btn in self._filter_buttons.items():
            btn.set_active(key == filter_type)
        self._refresh_all()

    def _poll_events(self) -> bool:
        if self._paused:
            return True
        self._cursor, events = self._event_store.since(self._cursor, limit=200)
        if not events:
            return True
        if self._geom_debug:
            print(
                f"[ui-geom] analyzer pre-refresh packets={len(self._packets)} new_events={len(events)}"
            )
            self._debug_width_report("pre")
        new_records: list[PacketRecord] = []
        for event in events:
            record = self._event_to_record(event)
            if record is not None:
                self._packets.appendleft(record)
                new_records.append(record)
        if new_records:
            self._append_new_rows(new_records)
        if self._geom_debug:
            print(f"[ui-geom] analyzer post-refresh packets={len(self._packets)}")
            self._debug_width_report("post")
        return True

    @staticmethod
    def _measure_h(widget: Gtk.Widget) -> tuple[int, int]:
        minimum, natural, _min_baseline, _nat_baseline = widget.measure(
            Gtk.Orientation.HORIZONTAL, -1
        )
        return minimum, natural

    def _debug_width_report(self, stage: str) -> None:
        widgets: list[tuple[str, Gtk.Widget]] = [
            ("analyzer", self),
            ("stream", self._stream),
            ("details", self._details),
            ("revealer", self._details_revealer),
        ]
        for name, widget in widgets:
            min_w, nat_w = self._measure_h(widget)
            print(
                f"[ui-geom] analyzer.{stage}.{name} "
                f"alloc={widget.get_width()}x{widget.get_height()} pref={min_w}/{nat_w}"
            )

        row = self._stream.get_row_at_index(0)
        if row is None:
            return
        row_min, row_nat = self._measure_h(row)
        print(
            f"[ui-geom] analyzer.{stage}.row0 alloc={row.get_width()}x{row.get_height()} pref={row_min}/{row_nat}"
        )
        line = row.get_child()
        if not isinstance(line, Gtk.Box):
            return
        child = line.get_first_child()
        idx = 0
        while child is not None:
            child_min, child_nat = self._measure_h(child)
            text = ""
            if isinstance(child, Gtk.Label):
                text = child.get_text()
                if len(text) > 40:
                    text = f"{text[:37]}..."
            print(
                f"[ui-geom] analyzer.{stage}.row0.col{idx} "
                f"alloc={child.get_width()} pref={child_min}/{child_nat} text='{text}'"
            )
            child = child.get_next_sibling()
            idx += 1

    def _event_to_record(self, event: dict[str, object]) -> PacketRecord | None:
        event_type = str(event.get("type", ""))

        # Only process decoded "packet" events for main display
        # Skip "raw_packet" - we were getting duplicates and missing sender info
        if event_type != EventType.PACKET:
            return None

        data = event.get("data")
        if not isinstance(data, dict):
            data = {}

        # Use payload_type_name (GRP_TXT, TXT_MSG, ADVERT, etc.) for the type column
        packet_type = str(data.get("payload_type_name") or "UNKNOWN").upper()

        # Try to get sender name from various sources (in order of preference)
        node = (
            data.get("sender_name")
            or data.get("advert_name")
            or data.get("contact_name")
            or data.get("peer_name")
            or data.get("name")
        )
        # Use sender_id as fallback (truncated for display)
        if not node:
            sender_id = data.get("sender_id") or data.get("peer_id") or data.get("peer")
            if sender_id:
                node = str(sender_id)

        if not node:
            node = "Unknown"
        node = str(node)

        # Decoded payload text (only set for successfully decrypted packets)
        payload_text = str(data.get("payload_text") or "")

        # Build display content: prefer decoded text, then type-specific, then hex
        handler = get_handler(packet_type)
        if payload_text:
            content = handler.format_content(payload_text, data)
        else:
            content = handler.content_summary(data)

        if not content:
            # Last resort for unknown/unhandled types: hex preview
            payload_hex = data.get("payload_hex") or ""
            if payload_hex:
                content = (
                    f"[{payload_hex[:40]}...]" if len(payload_hex) > 40 else f"[{payload_hex}]"
                )
            else:
                content = f"type={data.get('payload_type', '?')}"
        content = content.replace("\n", " ").replace("\r", " ")
        if len(content) > 160:
            content = f"{content[:157]}..."

        # Generate packet ID from signature
        signature = f"{packet_type}:{node}:{data.get('payload_hex', '')[:32]}"
        digest = hashlib.sha1(signature.encode("utf-8")).hexdigest()[:8].upper()

        rssi = int(data.get("rssi") or -112)
        snr = float(data.get("snr") or -9.0)
        route_type = str(data.get("route_type_name") or "FLOOD")
        # packet events don't have raw_hex, only payload_hex
        raw_hex = str(data.get("payload_hex") or "")

        # Extract routing path information
        path_len = int(data.get("path_len") or 0)
        path_hops = data.get("path_hops") or []
        packet_hash = str(data.get("packet_hash") or "")
        channel_name = str(data.get("channel_name") or "")

        # Use stored timestamp if available, otherwise current time
        timestamp, date = self._parse_event_timestamp(event)

        return PacketRecord(
            timestamp=timestamp,
            date=date,
            packet_type=packet_type,
            node=node,
            content=content,
            rssi=rssi,
            snr=snr,
            packet_id=digest,
            raw_hex=raw_hex,
            payload_text=payload_text,
            route_type=route_type,
            path_len=path_len,
            path_hops=path_hops,
            packet_hash=packet_hash,
            channel_name=channel_name,
        )

    @staticmethod
    def _extract_type_specific_content(packet_type: str, data: dict) -> str:
        """Extract human-readable content based on packet type."""
        return get_handler(packet_type).content_summary(data)

    def _parse_event_timestamp(self, event: dict[str, object]) -> tuple[str, str]:
        """Extract timestamp and date from event, falling back to current time.

        Returns (time_str, date_str) where time_str is "HH:MM:SS.mm" and
        date_str is "YYYY-MM-DD".
        """
        received_at = event.get("received_at")
        if received_at and isinstance(received_at, str):
            try:
                # Parse ISO format timestamp
                dt = datetime.fromisoformat(received_at.replace("Z", "+00:00"))
                # Convert to local time
                local_dt = dt.astimezone()
                time_str = local_dt.strftime("%H:%M:%S.%f")[:11]
                date_str = local_dt.strftime("%Y-%m-%d")
                return time_str, date_str
            except (ValueError, OSError):
                pass
        # Fallback to current time
        now = GLib.DateTime.new_now_local()
        return now.format("%H:%M:%S.%f")[:11], now.format("%Y-%m-%d")

    def _filtered_packets(self) -> list[PacketRecord]:
        if self._active_filter == AnalyzerFilter.ALL:
            return list(self._packets)
        filter_val = self._active_filter.value
        # PATH filter also matches TRACE (both are network diagnostics)
        if filter_val == "PATH":
            return [p for p in self._packets if "PATH" in p.packet_type or "TRACE" in p.packet_type]
        return [p for p in self._packets if filter_val in p.packet_type]

    def _matches_filter(self, record: PacketRecord) -> bool:
        """Check if a record matches the current active filter."""
        if self._active_filter == AnalyzerFilter.ALL:
            return True
        filter_val = self._active_filter.value
        if filter_val == "PATH":
            return "PATH" in record.packet_type or "TRACE" in record.packet_type
        return filter_val in record.packet_type

    def _refresh_all(self) -> None:
        self._refresh_stream()
        self._refresh_details()

    def _append_new_rows(self, new_records: list[PacketRecord]) -> None:
        """Incrementally prepend new rows to the stream ListBox."""
        # Filter new records that match the current filter
        matching = [r for r in new_records if self._matches_filter(r)]
        if not matching:
            return

        # If new records span multiple dates, or bridge a date boundary with
        # existing rows, do a full rebuild so day separators are inserted.
        dates = {r.date for r in matching}
        # Find the first actual packet row (skip DaySeparator rows)
        idx = 0
        while True:
            existing = self._stream.get_row_at_index(idx)
            if existing is None:
                break
            rec = getattr(existing, "_packet_record", None)
            if rec:
                dates.add(rec.date)
                break
            idx += 1
        if len(dates) > 1:
            self._refresh_all()
            return

        # Single date — fast incremental prepend.  matching is oldest-first
        # (chronological from event store), so inserting each at the insert
        # position naturally reverses to newest-first.
        # Insert after the leading date separator (position 1) if one exists,
        # otherwise at position 0.
        insert_pos = 1 if isinstance(self._stream.get_row_at_index(0), DaySeparator) else 0
        for record in matching:
            row = self._build_stream_row(record)
            self._stream.insert(row, insert_pos)

        # Trim overflow rows from the bottom
        max_rows = 180
        idx = max_rows
        while True:
            overflow = self._stream.get_row_at_index(idx)
            if overflow is None:
                break
            self._stream.remove(overflow)
            idx = max_rows  # re-check same index after removal

    def _build_stream_row(self, packet: PacketRecord) -> Gtk.ListBoxRow:
        """Create a single ListBoxRow for a packet record."""
        row = Gtk.ListBoxRow.new()
        setattr(row, "_packet_record", packet)
        row.add_css_class(f"row-{self._type_class(packet.packet_type)}")

        line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        line.add_css_class("analyzer-stream-row")
        line.set_margin_top(4)
        line.set_margin_bottom(4)
        line.set_margin_start(4)
        line.set_margin_end(4)

        time_label = Gtk.Label(label=packet.timestamp[:11])
        time_label.add_css_class("panel-muted")
        time_label.set_xalign(0)
        time_label.set_size_request(self.COL_TIME, -1)
        time_label.set_single_line_mode(True)
        line.append(time_label)

        type_label = Gtk.Label(label=packet.packet_type)
        type_label.add_css_class("packet-type")
        type_label.add_css_class(self._type_class(packet.packet_type))
        type_label.set_size_request(self.COL_TYPE, -1)
        type_label.set_ellipsize(Pango.EllipsizeMode.END)
        type_label.set_xalign(0)
        type_label.set_single_line_mode(True)
        line.append(type_label)

        node_label = Gtk.Label(label=packet.node)
        node_label.set_xalign(0)
        node_label.set_size_request(self.COL_NODE, -1)
        node_label.set_single_line_mode(True)
        node_label.set_ellipsize(Pango.EllipsizeMode.END)
        node_label.set_max_width_chars(18)
        line.append(node_label)

        content_label = Gtk.Label(label=packet.content)
        content_label.add_css_class("panel-muted")
        content_label.set_xalign(0)
        content_label.set_hexpand(True)
        content_label.set_single_line_mode(True)
        content_label.set_ellipsize(Pango.EllipsizeMode.END)
        line.append(content_label)

        sig_label = Gtk.Label(label=f"{packet.rssi} / {packet.snr:.2f}")
        sig_label.add_css_class("analyzer-rssi")
        sig_label.set_xalign(0)
        sig_label.set_size_request(self.COL_SIGNAL, -1)
        sig_label.set_single_line_mode(True)
        line.append(sig_label)

        row.set_child(line)
        return row

    def _refresh_stream(self) -> None:
        """Full rebuild of stream rows. Used for filter changes and initial load."""
        packets = self._filtered_packets()
        while True:
            row = self._stream.get_row_at_index(0)
            if row is None:
                break
            self._stream.remove(row)

        today = GLib.DateTime.new_now_local().format("%Y-%m-%d")
        prev_date: str | None = None
        for idx, packet in enumerate(packets[:180]):
            # Insert a date header at day-change boundaries and before the
            # first packet when it's not from today (so the viewer knows
            # they're looking at historical data).
            if packet.date != prev_date and not (prev_date is None and packet.date == today):
                self._stream.append(DaySeparator(packet.date))
            prev_date = packet.date

            row = self._build_stream_row(packet)
            self._stream.append(row)

        if self._selected_packet is None:
            self._stream.unselect_all()
            return

        # Re-select the previously selected packet by matching packet_id
        found = False
        idx = 0
        while True:
            row = self._stream.get_row_at_index(idx)
            if row is None:
                break
            rec = getattr(row, "_packet_record", None)
            if rec is not None and rec.packet_id == self._selected_packet.packet_id:
                self._stream.select_row(row)
                found = True
                break
            idx += 1
        if not found:
            self._selected_packet = None
            self._stream.unselect_all()
            self._details_revealer.set_reveal_child(False)

    def _on_packet_selected(self, _listbox: Gtk.ListBox, row: Gtk.ListBoxRow | None) -> None:
        if row is None:
            self._selected_packet = None
            self._details_revealer.set_reveal_child(False)
            return
        self._selected_packet = getattr(row, "_packet_record", None)
        self._refresh_details()
        self._details_revealer.set_reveal_child(True)

    def _refresh_details(self) -> None:
        while True:
            child = self._details.get_first_child()
            if child is None:
                break
            self._details.remove(child)

        if self._selected_packet is None:
            self._details_revealer.set_reveal_child(False)
            return

        packet = self._selected_packet

        title_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        title = Gtk.Label(label="Packet Details")
        title.add_css_class("panel-title")
        title.set_halign(Gtk.Align.START)
        title.set_hexpand(True)
        title_row.append(title)

        close = Gtk.Button.new_from_icon_name("window-close-symbolic")
        close.add_css_class("flat")
        close.connect("clicked", self._on_close_details_clicked)
        title_row.append(close)
        self._details.append(title_row)

        subtitle = Gtk.Label(label=f"{packet.packet_type}  •  ID: {packet.packet_id}")
        subtitle.add_css_class("panel-muted")
        subtitle.set_halign(Gtk.Align.START)
        subtitle.set_ellipsize(Pango.EllipsizeMode.END)
        subtitle.set_single_line_mode(True)
        subtitle.set_max_width_chars(36)
        self._details.append(subtitle)

        self._details.append(DetailBlock("Timestamp", packet.timestamp))
        self._details.append(
            DetailBlock("Radio Signal", f"RSSI {packet.rssi} dBm   SNR {packet.snr:.2f} dB")
        )
        self._details.append(self._decoded_payload_block(packet))
        self._details.append(self._routing_block(packet))
        self._details.append(self._raw_block(packet))

        # "View in Channel" action for group text/data packets
        if packet.channel_name and packet.packet_type in ("GRP_TXT", "GRP_DATA"):
            nav_btn = Gtk.Button()
            nav_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            nav_box.append(Gtk.Label(label="View in Channel"))
            nav_box.append(Gtk.Image.new_from_icon_name("go-next-symbolic"))
            nav_btn.set_child(nav_box)
            nav_btn.add_css_class("flat")
            nav_btn.set_margin_top(4)
            nav_btn.connect(
                "clicked", lambda _b, ch=packet.channel_name: self._navigate_to_channel(ch)
            )
            self._details.append(nav_btn)

    def _on_close_details_clicked(self, _button: Gtk.Button) -> None:
        self._selected_packet = None
        self._stream.unselect_all()
        self._details_revealer.set_reveal_child(False)

    def _navigate_to_channel(self, channel_name: str) -> None:
        """Navigate to messages view and select the given channel."""
        root = self.get_root()
        if root is None:
            return

        stack = getattr(root, "_stack", None)
        if stack is None:
            return

        stack.set_visible_child_name("messages")

        nav_buttons = getattr(root, "_nav_buttons", None)
        if nav_buttons:
            for name, btn in nav_buttons.items():
                btn.set_active(name == "messages")

        messages_widget = stack.get_child_by_name("messages")
        if messages_widget is not None:
            if TYPE_CHECKING:
                from meshcore_console.ui_gtk.views.messages import MessagesView
            messages_view = cast("MessagesView", messages_widget)
            messages_view.select_channel(channel_name)

    def _decoded_payload_block(self, packet: PacketRecord) -> DetailBlock:
        payload = packet.payload_text if packet.payload_text else "(binary payload)"
        return DetailBlock("Decoded Payload", payload)

    def _routing_block(self, packet: PacketRecord) -> DetailBlock:
        block = DetailBlock("Routing")

        # Show route type and hop count
        if packet.path_len == 0:
            route_desc = f"{packet.route_type} (direct, no hops)"
        else:
            route_desc = (
                f"{packet.route_type} ({packet.path_len} hop{'s' if packet.path_len != 1 else ''})"
            )
        route_label = Gtk.Label(label=route_desc)
        route_label.add_css_class("route-hop")
        route_label.set_halign(Gtk.Align.START)
        block.set_content(route_label)

        # Show actual path if there are hops
        if packet.path_hops:
            from meshcore_console.ui_gtk.widgets.node_badge import find_peer_for_hop

            all_peers = self._service.list_peers()
            sender_name = packet.node if packet.node else "Sender"
            sender_peer = find_peer_for_hop(all_peers, packet.node) if packet.node else None
            sender_prefix = (packet.node or "??")[:2].upper()

            path = PathVisualization(
                hops=packet.path_hops,
                peers=all_peers,
                arrow="←",
                start=("Me", "You (this node)", None, STYLE_SELF),
                end=(sender_prefix, sender_name, sender_peer, STYLE_DEFAULT),
            )
            path.set_margin_top(4)
            block.set_content(path)

        # Show packet hash for deduplication reference
        if packet.packet_hash:
            hash_label = Gtk.Label(label=f"Hash: {packet.packet_hash}")
            hash_label.add_css_class("panel-muted")
            hash_label.set_halign(Gtk.Align.START)
            hash_label.set_margin_top(4)
            block.set_content(hash_label)

        return block

    def _raw_block(self, packet: PacketRecord) -> DetailBlock:
        raw_display = packet.raw_hex if packet.raw_hex else "(no raw data)"
        block = DetailBlock("Raw Packet")
        formatted = DetailBlock._wrap_text(raw_display)
        raw = Gtk.Label(label=formatted)
        raw.add_css_class("analyzer-raw")
        raw.set_halign(Gtk.Align.START)
        raw.set_xalign(0)
        raw.set_selectable(True)
        block.set_content(raw)
        return block

    @staticmethod
    def _type_class(packet_type: str) -> str:
        """Map packet type to CSS class for styling."""
        return get_handler(packet_type).css_class

    @staticmethod
    def _header_label(text: str, width: int) -> Gtk.Label:
        label = Gtk.Label(label=text)
        label.add_css_class("panel-muted")
        label.set_xalign(0)
        label.set_halign(Gtk.Align.START)
        label.set_size_request(width, -1)
        label.set_ellipsize(Pango.EllipsizeMode.END)
        label.set_single_line_mode(True)
        return label
