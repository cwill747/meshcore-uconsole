from __future__ import annotations

from typing import TYPE_CHECKING, cast

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import Gtk

if TYPE_CHECKING:
    from .messages import MessagesView

from meshcore_console.core.models import Peer
from meshcore_console.core.radio import format_rssi, format_snr
from meshcore_console.core.services import MeshcoreService
from meshcore_console.ui_gtk.layout import Layout
from meshcore_console.ui_gtk.state import UiEventStore
from meshcore_console.core.time import to_local
from meshcore_console.ui_gtk.widgets import (
    DetailRow,
    EmptyState,
    PathVisualization,
    PeerListRow,
)
from meshcore_console.ui_gtk.widgets.node_badge import STYLE_DEFAULT, STYLE_SELF


def format_public_key(key: str | None) -> str:
    """Format public key as two-byte hex chunks."""
    if not key:
        return "Unknown"
    # Split into 4-char (2 byte) chunks
    chunks = [key[i : i + 4] for i in range(0, len(key), 4)]
    return " ".join(chunks)


def format_coordinates(lat: float | None, lon: float | None) -> str:
    """Format lat/lon coordinates for display."""
    if lat is None or lon is None:
        return "Unknown"
    lat_dir = "N" if lat >= 0 else "S"
    lon_dir = "E" if lon >= 0 else "W"
    return f"{abs(lat):.5f}° {lat_dir}, {abs(lon):.5f}° {lon_dir}"


class PeersView(Gtk.Box):
    def __init__(self, service: MeshcoreService, event_store: UiEventStore, layout: Layout) -> None:
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._service = service
        self._event_store = event_store
        self._last_peer_snapshot: str = ""
        self._selected_peer: Peer | None = None

        # Column 1: Contacts
        contacts_column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        contacts_column.add_css_class("panel-card")
        contacts_column.set_size_request(layout.peers_list_width, -1)

        contacts_header = Gtk.Label(label="Contacts")
        contacts_header.add_css_class("panel-title")
        contacts_header.set_halign(Gtk.Align.START)
        contacts_header.set_margin_start(12)
        contacts_header.set_margin_top(10)
        contacts_header.set_margin_bottom(8)
        contacts_column.append(contacts_header)

        contacts_scroll = Gtk.ScrolledWindow.new()
        contacts_scroll.set_vexpand(True)
        contacts_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        self._contacts_list = Gtk.ListBox.new()
        self._contacts_list.add_css_class("peer-list")
        self._contacts_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self._contacts_list.connect("row-selected", self._on_peer_selected)
        contacts_scroll.set_child(self._contacts_list)
        contacts_column.append(contacts_scroll)

        self.append(contacts_column)

        # Column 2: Repeaters/Network
        network_column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        network_column.add_css_class("panel-card")
        network_column.set_size_request(layout.peers_list_width, -1)

        network_header = Gtk.Label(label="Repeaters")
        network_header.add_css_class("panel-title")
        network_header.set_halign(Gtk.Align.START)
        network_header.set_margin_start(12)
        network_header.set_margin_top(10)
        network_header.set_margin_bottom(8)
        network_column.append(network_header)

        network_scroll = Gtk.ScrolledWindow.new()
        network_scroll.set_vexpand(True)
        network_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        self._network_list = Gtk.ListBox.new()
        self._network_list.add_css_class("peer-list")
        self._network_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self._network_list.connect("row-selected", self._on_peer_selected)
        network_scroll.set_child(self._network_list)
        network_column.append(network_scroll)

        self.append(network_column)

        # Column 3: Details panel
        details_column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        details_column.add_css_class("panel-card")
        details_column.set_hexpand(True)

        self._details_title = Gtk.Label(label="Select a peer")
        self._details_title.add_css_class("panel-title")
        self._details_title.set_halign(Gtk.Align.START)
        self._details_title.set_margin_start(12)
        self._details_title.set_margin_top(10)
        self._details_title.set_margin_bottom(8)
        details_column.append(self._details_title)

        details_scroll = Gtk.ScrolledWindow.new()
        details_scroll.set_vexpand(True)
        details_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        self._details_content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self._details_content.set_margin_start(12)
        self._details_content.set_margin_end(12)
        self._details_content.set_margin_bottom(12)
        details_scroll.set_child(self._details_content)
        details_column.append(details_scroll)

        self.append(details_column)

        self._show_empty_details()
        self._refresh_peers()
        self._event_store.connect("events-available", lambda _store: self._poll_peers())

    @staticmethod
    def _peer_snapshot(peers: list[Peer]) -> str:
        """Compute a snapshot string to detect peer data changes."""
        parts = []
        for p in peers:
            parts.append(
                f"{p.display_name}:{p.last_advert_time}:{p.signal_quality}:{p.rssi}:{p.snr}:{p.is_favorite}"
            )
        return "|".join(parts)

    def _poll_peers(self) -> bool:
        """Check for peer changes and refresh if needed."""
        peers = self._service.list_peers()
        snapshot = self._peer_snapshot(peers)
        if snapshot != self._last_peer_snapshot:
            self._refresh_peers()
        return True

    def _refresh_peers(self) -> None:
        """Refresh the peers list display, preserving selection."""
        # Remember selected peer
        selected_name = self._selected_peer.display_name if self._selected_peer else None

        # Clear existing rows
        for listbox in (self._contacts_list, self._network_list):
            while True:
                row = listbox.get_row_at_index(0)
                if row is None:
                    break
                listbox.remove(row)

        peers = self._service.list_peers()
        self._last_peer_snapshot = self._peer_snapshot(peers)

        contacts = sorted(
            (p for p in peers if not p.is_repeater),
            key=lambda p: (p.is_favorite, p.last_advert_time is not None, p.last_advert_time),
            reverse=True,
        )
        network = sorted(
            (p for p in peers if p.is_repeater),
            key=lambda p: (p.is_favorite, p.last_advert_time is not None, p.last_advert_time),
            reverse=True,
        )

        self._populate_list(self._contacts_list, contacts, "No contacts yet")
        self._populate_list(self._network_list, network, "No repeaters yet")

        # Restore selection
        if selected_name:
            self._reselect_peer(selected_name)

    def _populate_list(self, listbox: Gtk.ListBox, peers: list[Peer], empty_msg: str) -> None:
        """Populate a listbox with peers."""
        if not peers:
            row = Gtk.ListBoxRow.new()
            row.set_selectable(False)
            row.set_child(EmptyState(empty_msg))
            listbox.append(row)
            return

        for peer in peers:
            listbox.append(PeerListRow(peer))

    def _on_peer_selected(self, listbox: Gtk.ListBox, row: Gtk.ListBoxRow | None) -> None:
        """Handle peer selection to show details."""
        # Deselect the other list
        other = self._network_list if listbox == self._contacts_list else self._contacts_list
        other.unselect_all()

        if row is None:
            self._show_empty_details()
            return

        peer = getattr(row, "peer", None)
        if peer is None:
            self._show_empty_details()
            return

        self._selected_peer = peer
        self._show_peer_details(peer)

    def get_default_focus(self) -> Gtk.Widget:
        """Return the widget that should receive focus when this view is shown."""
        return self._contacts_list

    def _show_empty_details(self) -> None:
        """Show empty state in details panel."""
        self._details_title.set_text("Peer Details")
        self._clear_details()
        self._details_content.append(
            EmptyState("Select a contact or repeater to view details", vexpand=True)
        )

    def _clear_details(self) -> None:
        """Clear the details content area."""
        while True:
            child = self._details_content.get_first_child()
            if child is None:
                break
            self._details_content.remove(child)

    def _show_peer_details(self, peer: Peer) -> None:
        """Show details for a selected peer."""
        self._details_title.set_text(peer.display_name)
        self._clear_details()

        # Type badge
        type_label = Gtk.Label(label="Repeater" if peer.is_repeater else "Contact")
        type_label.add_css_class("status-pill")
        type_label.add_css_class("ok" if not peer.is_repeater else "warn")
        type_label.set_halign(Gtk.Align.START)
        self._details_content.append(type_label)

        # === Signal Section ===
        self._add_section_header("Signal")

        if peer.rssi is not None:
            self._details_content.append(DetailRow("RSSI:", format_rssi(peer.rssi)))
        if peer.snr is not None:
            self._details_content.append(DetailRow("SNR:", format_snr(peer.snr)))
        if peer.signal_quality is not None:
            self._details_content.append(DetailRow("Quality:", f"{peer.signal_quality}%"))

        if peer.rssi is None and peer.snr is None and peer.signal_quality is None:
            no_signal = Gtk.Label(label="No signal data available")
            no_signal.add_css_class("panel-muted")
            no_signal.set_halign(Gtk.Align.START)
            self._details_content.append(no_signal)

        # === Location Section ===
        self._add_section_header("Location")

        if peer.latitude is not None and peer.longitude is not None:
            coords = format_coordinates(peer.latitude, peer.longitude)
            self._details_content.append(DetailRow("Coordinates:", coords))

            if peer.location_updated:
                loc_time = to_local(peer.location_updated).strftime("%b %d at %H:%M")
                self._details_content.append(DetailRow("Updated:", loc_time))
        else:
            no_loc = Gtk.Label(label="No location reported")
            no_loc.add_css_class("panel-muted")
            no_loc.set_halign(Gtk.Align.START)
            self._details_content.append(no_loc)

        # === Last Seen Section ===
        self._add_section_header("Activity")

        if peer.last_advert_time:
            time_str = to_local(peer.last_advert_time).strftime("%b %d, %Y at %I:%M %p")
            self._details_content.append(DetailRow("Last Seen:", time_str))
        else:
            self._details_content.append(DetailRow("Last Seen:", "Unknown"))

        # === Network Path Section ===
        self._add_section_header("Network Path")

        if peer.last_path:
            peer_prefix = (peer.public_key or peer.display_name)[:2].upper()
            path = PathVisualization(
                hops=peer.last_path,
                peers=self._service.list_peers(),
                start=("Me", "You (this node)", None, STYLE_SELF),
                end=(peer_prefix, peer.display_name, peer, STYLE_DEFAULT),
            )
            self._details_content.append(path)
        else:
            direct_label = Gtk.Label(label="Direct connection (no hops)")
            direct_label.add_css_class("panel-muted")
            direct_label.set_halign(Gtk.Align.START)
            self._details_content.append(direct_label)

        # === Public Key Section ===
        self._add_section_header("Public Key")

        key_text = format_public_key(peer.public_key)
        key_label = Gtk.Label(label=key_text)
        key_label.add_css_class("analyzer-raw")
        key_label.set_halign(Gtk.Align.START)
        key_label.set_wrap(True)
        key_label.set_wrap_mode(Gtk.WrapMode.CHAR)
        key_label.set_selectable(True)
        self._details_content.append(key_label)

        # === Action buttons ===
        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        actions.set_margin_top(16)

        fav_label = "☆ Favorite" if not peer.is_favorite else "★ Unfavorite"
        fav_btn = Gtk.Button.new_with_label(fav_label)
        fav_btn.connect("clicked", self._on_toggle_favorite_clicked, peer)
        actions.append(fav_btn)

        if not peer.is_repeater:
            message_btn = Gtk.Button.new_with_label("Send Message")
            message_btn.add_css_class("suggested-action")
            message_btn.connect("clicked", self._on_send_message_clicked, peer)
            actions.append(message_btn)

        self._details_content.append(actions)

    def _add_section_header(self, title: str) -> None:
        """Add a section header to the details panel."""
        header = Gtk.Label(label=title)
        header.add_css_class("message-detail-header")
        header.set_halign(Gtk.Align.START)
        header.set_margin_top(12)
        self._details_content.append(header)

    def select_peer(self, peer_id: str) -> None:
        """Select a peer programmatically and show its details."""
        for listbox in (self._contacts_list, self._network_list):
            index = 0
            while True:
                row = listbox.get_row_at_index(index)
                if row is None:
                    break
                peer = getattr(row, "peer", None)
                if peer and peer.peer_id == peer_id:
                    listbox.select_row(row)
                    return
                index += 1

    def _reselect_peer(self, display_name: str) -> None:
        """Re-select a peer by name after a list refresh."""
        for listbox in (self._contacts_list, self._network_list):
            index = 0
            while True:
                row = listbox.get_row_at_index(index)
                if row is None:
                    break
                peer = getattr(row, "peer", None)
                if peer and peer.display_name == display_name:
                    listbox.select_row(row)
                    return
                index += 1

    def _on_toggle_favorite_clicked(self, _button: Gtk.Button, peer: Peer) -> None:
        """Toggle the favorite status of a peer."""
        self._service.set_favorite(peer.peer_id, not peer.is_favorite)
        peer.is_favorite = not peer.is_favorite
        self._refresh_peers()

    def _on_send_message_clicked(self, _button: Gtk.Button, peer: Peer) -> None:
        """Navigate to messages view and start a conversation with this peer."""
        # Find the main window and switch to messages view
        root = self.get_root()
        if root is None:
            return

        # Access the stack through the window
        stack = getattr(root, "_stack", None)
        if stack is None:
            return

        # Switch to messages view
        stack.set_visible_child_name("messages")

        # Update nav buttons if they exist
        nav_buttons = getattr(root, "_nav_buttons", None)
        if nav_buttons:
            for name, btn in nav_buttons.items():
                btn.set_active(name == "messages")

        # Select the peer's channel in the messages view
        messages_widget = stack.get_child_by_name("messages")
        if messages_widget is not None:
            messages_view = cast("MessagesView", messages_widget)
            messages_view.select_channel(peer.display_name)
