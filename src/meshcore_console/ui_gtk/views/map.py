"""Map view showing peer locations on OpenStreetMap tiles."""

from __future__ import annotations

import logging
import sys
from typing import TYPE_CHECKING, cast

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Shumate", "1.0")

from gi.repository import GLib, Gtk, Pango

if TYPE_CHECKING:
    from .messages import MessagesView

logger = logging.getLogger(__name__)

try:
    from gi.repository import Shumate

    SHUMATE_AVAILABLE = True
except ImportError:
    SHUMATE_AVAILABLE = False
    print("[MapView] libshumate not available", file=sys.stderr)

from meshcore_console.core.models import Peer
from meshcore_console.core.radio import format_rssi, format_snr
from meshcore_console.core.services import MeshcoreService
from meshcore_console.ui_gtk.layout import Layout
from meshcore_console.core.time import to_local
from meshcore_console.platform.mbtiles import MBTilesReader, find_mbtiles_files
from meshcore_console.ui_gtk.widgets import DetailRow, EmptyState


# Tile URL templates - Shumate uses {z}, {x}, {y} placeholders
CARTO_DARK_URL = "https://basemaps.cartocdn.com/dark_all/{z}/{x}/{y}.png"
OSM_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"


class MapView(Gtk.Box):
    """Map view with peer markers and details panel."""

    def __init__(self, service: MeshcoreService, layout: Layout) -> None:
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        self._service = service
        self._layout = layout
        self._selected_peer: Peer | None = None
        self._peer_markers: dict[str, object] = {}  # peer_id -> marker
        self._device_marker: object | None = None
        self._mbtiles: MBTilesReader | None = None
        self._last_peer_count = 0
        self._shown_gps_errors: set[str] = set()  # Track shown errors to avoid repeats
        self._following = False  # Whether map follows device location
        self._programmatic_move = False  # Suppress follow-disable during go_to

        if not SHUMATE_AVAILABLE:
            self._build_fallback_ui()
            return

        self._build_map_ui()
        GLib.timeout_add(2000, self._poll_locations)

        # In mock mode, auto-cycle GPS position every 5 seconds
        if self._service.is_mock_mode():
            GLib.timeout_add(5000, self._cycle_mock_gps)

    def _build_fallback_ui(self) -> None:
        """Build fallback UI when Shumate is not available."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.set_hexpand(True)
        box.set_vexpand(True)

        title = Gtk.Label(label="Map unavailable")
        title.add_css_class("panel-title")
        title.set_halign(Gtk.Align.CENTER)
        title.set_valign(Gtk.Align.END)
        title.set_vexpand(True)
        box.append(title)

        hint = EmptyState("Install gir1.2-shumate-1.0 to enable the map view.")
        hint.set_vexpand(True)
        hint.set_valign(Gtk.Align.START)
        box.append(hint)

        self.append(box)

    def _build_map_ui(self) -> None:
        """Build the map UI with Shumate."""
        # Main horizontal split: map | details panel
        # Map takes remaining space, details panel is fixed width

        # Use overlay as the main container to avoid re-parenting
        overlay = Gtk.Overlay.new()
        overlay.set_hexpand(True)
        overlay.set_vexpand(True)
        self.append(overlay)

        # Create Shumate map
        self._map = Shumate.SimpleMap.new()
        self._map.set_hexpand(True)
        self._map.set_vexpand(True)

        # Hide built-in controls - we'll add our own styled ones
        self._map.set_show_zoom_buttons(False)
        self._map.get_scale().set_visible(False)
        self._map.get_compass().set_visible(False)

        # Get the map widget and viewport
        map_widget = self._map.get_map()
        self._viewport = map_widget.get_viewport()

        # Set up tile source - CartoDB Dark Matter
        self._tile_source = self._create_tile_source()
        self._map.set_map_source(self._tile_source)

        # Set initial view - center on SF Bay Area if mock mode, otherwise US
        if self._service.is_mock_mode():
            # SF Bay Area for mock data
            self._viewport.set_zoom_level(12)
            self._viewport.set_location(37.7749, -122.4194)
        else:
            # Centered on US
            self._viewport.set_zoom_level(4)
            self._viewport.set_location(39.8283, -98.5795)

        # Detect user panning to disable follow mode
        self._viewport.connect("notify::latitude", self._on_viewport_moved)
        self._viewport.connect("notify::longitude", self._on_viewport_moved)

        # Create marker layer
        self._marker_layer = Shumate.MarkerLayer.new(self._viewport)
        map_widget.add_layer(self._marker_layer)

        # Add map as the base child of overlay
        overlay.set_child(self._map)

        # Add control overlays
        self._add_map_controls(overlay)

        # Details panel (initially hidden)
        self._details_panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self._details_panel.add_css_class("panel-card")
        self._details_panel.add_css_class("map-details-panel")
        self._details_panel.set_size_request(self._layout.map_details_width, -1)
        self._details_panel.set_visible(False)
        self.append(self._details_panel)

        self._details_title = Gtk.Label(label="")
        self._details_title.add_css_class("panel-title")
        self._details_title.set_halign(Gtk.Align.START)
        self._details_panel.append(self._details_title)

        self._details_content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self._details_panel.append(self._details_content)

        # Load MBTiles if available for offline support
        self._load_mbtiles()

        # Initial marker load
        self._refresh_markers()

    def _create_tile_source(self) -> Shumate.MapSource:
        """Create a tile source for the map."""
        # Use built-in OSM source which is known to work
        registry = Shumate.MapSourceRegistry.new_with_defaults()
        osm = registry.get_by_id("osm-mapnik")
        if osm is not None:
            print(f"[MapView] Using built-in OSM source: {osm.get_name()}", file=sys.stderr)
            return osm

        # Fallback: create OSM source manually
        print(f"[MapView] Creating manual OSM source with URL: {OSM_URL}", file=sys.stderr)
        return Shumate.RasterRenderer.new_full_from_url(
            "osm-tiles",
            "OpenStreetMap",
            "© OpenStreetMap contributors",
            "https://www.openstreetmap.org/copyright",
            0,  # min zoom
            19,  # max zoom
            256,  # tile size
            Shumate.MapProjection.MERCATOR,
            OSM_URL,
        )

    def _add_map_controls(self, overlay: Gtk.Overlay) -> None:
        """Add zoom and location controls as overlay."""
        # Zoom controls in bottom-right
        zoom_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        zoom_box.set_halign(Gtk.Align.END)
        zoom_box.set_valign(Gtk.Align.END)
        zoom_box.set_margin_end(12)
        zoom_box.set_margin_bottom(12)

        zoom_in = Gtk.Button.new_from_icon_name("zoom-in-symbolic")
        zoom_in.add_css_class("map-control-button")
        zoom_in.update_property([Gtk.AccessibleProperty.LABEL], ["Zoom in"])
        zoom_in.connect("clicked", self._on_zoom_in)
        zoom_box.append(zoom_in)

        zoom_out = Gtk.Button.new_from_icon_name("zoom-out-symbolic")
        zoom_out.add_css_class("map-control-button")
        zoom_out.update_property([Gtk.AccessibleProperty.LABEL], ["Zoom out"])
        zoom_out.connect("clicked", self._on_zoom_out)
        zoom_box.append(zoom_out)

        overlay.add_overlay(zoom_box)

        # Center on device button in bottom-left
        center_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        center_box.set_halign(Gtk.Align.START)
        center_box.set_valign(Gtk.Align.END)
        center_box.set_margin_start(12)
        center_box.set_margin_bottom(12)

        self._center_btn = Gtk.Button.new_from_icon_name("find-location-symbolic")
        self._center_btn.add_css_class("map-control-button")
        self._center_btn.set_tooltip_text("Center on device")
        self._center_btn.update_property([Gtk.AccessibleProperty.LABEL], ["Center on device"])
        self._center_btn.connect("clicked", self._on_center_device)
        center_box.append(self._center_btn)

        # In mock mode, add a "simulate movement" button
        if self._service.is_mock_mode():
            move_btn = Gtk.Button.new_from_icon_name("media-skip-forward-symbolic")
            move_btn.add_css_class("map-control-button")
            move_btn.set_tooltip_text("Simulate GPS movement")
            move_btn.connect("clicked", self._on_simulate_movement)
            center_box.append(move_btn)

        overlay.add_overlay(center_box)

        # Mock mode indicator in top-left
        if self._service.is_mock_mode():
            mock_label = Gtk.Label(label="MOCK MODE")
            mock_label.add_css_class("status-pill")
            mock_label.add_css_class("warn")
            mock_label.set_halign(Gtk.Align.START)
            mock_label.set_valign(Gtk.Align.START)
            mock_label.set_margin_start(12)
            mock_label.set_margin_top(12)
            overlay.add_overlay(mock_label)

    def _on_zoom_in(self, _button: Gtk.Button) -> None:
        """Zoom in on the map."""
        self._programmatic_move = True
        current = self._viewport.get_zoom_level()
        self._viewport.set_zoom_level(min(current + 1, 19))
        self._programmatic_move = False

    def _on_zoom_out(self, _button: Gtk.Button) -> None:
        """Zoom out on the map."""
        self._programmatic_move = True
        current = self._viewport.get_zoom_level()
        self._viewport.set_zoom_level(max(current - 1, 0))
        self._programmatic_move = False

    def _on_center_device(self, _button: Gtk.Button) -> None:
        """Center the map on the device location and enable follow mode."""
        location = self._service.get_device_location()
        if location:
            lat, lon = location
            self._set_following(True)
            self._programmatic_move = True
            self._viewport.set_zoom_level(12)
            self._viewport.set_location(lat, lon)
            self._programmatic_move = False
        else:
            # No fix yet - show feedback
            if self._service.has_gps_fix():
                self._show_toast("GPS location unavailable")
            else:
                self._show_toast("GPS acquiring satellites...")

    def _on_viewport_moved(self, viewport: object, pspec: object) -> None:
        """Disable follow mode when user pans the map."""
        if not self._programmatic_move and self._following:
            self._set_following(False)

    def _set_following(self, active: bool) -> None:
        """Toggle follow mode and update button visual."""
        self._following = active
        if active:
            self._center_btn.add_css_class("map-control-active")
        else:
            self._center_btn.remove_css_class("map-control-active")

    def _on_simulate_movement(self, _button: Gtk.Button) -> None:
        """Manually trigger GPS position cycle in mock mode."""
        self._service.cycle_mock_gps()
        self._update_device_marker()

    def _cycle_mock_gps(self) -> bool:
        """Auto-cycle mock GPS position. GLib timeout callback."""
        if not self._service.is_mock_mode():
            return False
        self._service.cycle_mock_gps()
        return True  # Continue timeout

    def _load_mbtiles(self) -> None:
        """Load MBTiles file for offline tiles if available."""
        files = find_mbtiles_files()
        if files:
            self._mbtiles = MBTilesReader(files[0])
            if self._mbtiles.open():
                metadata = self._mbtiles.get_metadata()
                name = metadata.get("name", files[0].name)
                print(f"[MapView] Loaded offline tiles: {name}", file=sys.stderr)

    def _poll_locations(self) -> bool:
        """Poll for location updates."""
        # Poll GPS for new data (reads serial port for real GPS)
        self._service.poll_gps()

        # Check for GPS errors and show toast
        self._check_gps_status()

        # Update device marker
        self._update_device_marker()

        # Check if peers changed
        peers = self._service.list_peers()
        peers_with_location = [
            p for p in peers if p.latitude is not None and p.longitude is not None
        ]
        if len(peers_with_location) != self._last_peer_count:
            self._refresh_markers()

        return True

    def _check_gps_status(self) -> None:
        """Check GPS status and show toast for errors."""
        error = self._service.get_gps_error()
        if error and error not in self._shown_gps_errors:
            self._shown_gps_errors.add(error)
            logger.warning("GPS error: %s", error)
            self._show_toast(f"GPS: {error}")

    def _show_toast(self, message: str) -> None:
        """Show a toast message via the main window's toast overlay."""
        try:
            import gi

            gi.require_version("Adw", "1")
            from gi.repository import Adw

            root = self.get_root()
            if root is None:
                return

            # Look for toast overlay on the main window
            toast_overlay = getattr(root, "_toast_overlay", None)
            if toast_overlay is not None and hasattr(toast_overlay, "add_toast"):
                toast_overlay.add_toast(Adw.Toast.new(message))
        except Exception as e:
            print(f"[MapView] Toast error: {e}", file=sys.stderr)

    def _refresh_markers(self) -> None:
        """Refresh all peer markers."""
        peers = self._service.list_peers()
        self._last_peer_count = sum(1 for p in peers if p.latitude is not None)

        # Track which peers we've seen
        seen_ids: set[str] = set()

        for peer in peers:
            if peer.latitude is None or peer.longitude is None:
                continue

            seen_ids.add(peer.peer_id)

            if peer.peer_id in self._peer_markers:
                # Update existing marker position
                marker = self._peer_markers[peer.peer_id]
                marker.set_location(peer.latitude, peer.longitude)
            else:
                # Create new marker
                marker = self._create_peer_marker(peer)
                self._peer_markers[peer.peer_id] = marker
                self._marker_layer.add_marker(marker)

        # Remove markers for peers no longer in list
        for peer_id in list(self._peer_markers.keys()):
            if peer_id not in seen_ids:
                marker = self._peer_markers.pop(peer_id)
                self._marker_layer.remove_marker(marker)

        # Update device marker
        self._update_device_marker()

    def _create_peer_marker(self, peer: Peer) -> Shumate.Marker:
        """Create a marker for a peer."""
        marker = Shumate.Marker.new()
        marker.set_location(peer.latitude, peer.longitude)

        # Create marker widget
        marker_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        marker_box.set_halign(Gtk.Align.CENTER)

        # Marker dot
        dot = Gtk.DrawingArea()
        dot.set_size_request(16, 16)
        dot.set_draw_func(self._draw_marker_dot, peer)
        marker_box.append(dot)

        # Label
        label = Gtk.Label(label=peer.display_name)
        label.add_css_class("map-marker-label")
        label.set_ellipsize(Pango.EllipsizeMode.END)
        label.set_max_width_chars(12)
        marker_box.append(label)

        marker.set_child(marker_box)

        # Store peer reference for click handling
        setattr(marker, "_peer", peer)

        # Make marker clickable
        click = Gtk.GestureClick.new()
        click.connect("released", self._on_marker_clicked, marker)
        marker_box.add_controller(click)

        return marker

    def _draw_marker_dot(
        self,
        area: Gtk.DrawingArea,
        cr: object,  # cairo.Context
        width: int,
        height: int,
        peer: Peer,
    ) -> None:
        """Draw a marker dot."""
        # Choose color based on peer type
        if peer.is_repeater:
            # Warn color (orange)
            cr.set_source_rgba(0.937, 0.725, 0.247, 1.0)  # #efb93f
        else:
            # Text color (light)
            cr.set_source_rgba(0.906, 0.933, 0.973, 1.0)  # #e7eef8

        # Draw filled circle
        cr.arc(width / 2, height / 2, 6, 0, 2 * 3.14159)
        cr.fill()

        # Draw border
        cr.set_source_rgba(0.180, 0.235, 0.286, 1.0)  # #2e3a49
        cr.arc(width / 2, height / 2, 6, 0, 2 * 3.14159)
        cr.set_line_width(2)
        cr.stroke()

    def _update_device_marker(self) -> None:
        """Update or create the device location marker."""
        location = self._service.get_device_location()

        if location is None:
            if self._device_marker is not None:
                self._marker_layer.remove_marker(self._device_marker)
                self._device_marker = None
            if self._following:
                self._set_following(False)
            return

        lat, lon = location

        if self._device_marker is None:
            self._device_marker = self._create_device_marker(lat, lon)
            self._marker_layer.add_marker(self._device_marker)
        else:
            self._device_marker.set_location(lat, lon)

        # Keep map centered on device when following
        if self._following:
            self._programmatic_move = True
            self._viewport.set_location(lat, lon)
            self._programmatic_move = False

    def _create_device_marker(self, lat: float, lon: float) -> Shumate.Marker:
        """Create a marker for the device location."""
        marker = Shumate.Marker.new()
        marker.set_location(lat, lon)

        # Create marker widget with pulsing effect
        marker_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        marker_box.set_halign(Gtk.Align.CENTER)

        # Marker dot with accent color
        dot = Gtk.DrawingArea()
        dot.set_size_request(20, 20)
        dot.set_draw_func(self._draw_device_dot, None)
        marker_box.append(dot)

        # Label
        label = Gtk.Label(label="You")
        label.add_css_class("map-marker-label")
        marker_box.append(label)

        marker.set_child(marker_box)
        return marker

    def _draw_device_dot(
        self,
        area: Gtk.DrawingArea,
        cr: object,  # cairo.Context
        width: int,
        height: int,
        _data: object,
    ) -> None:
        """Draw the device marker dot with accent color."""
        # Outer ring (semi-transparent)
        cr.set_source_rgba(0.208, 0.761, 0.608, 0.3)  # #35c29b at 30%
        cr.arc(width / 2, height / 2, 9, 0, 2 * 3.14159)
        cr.fill()

        # Inner dot (solid accent)
        cr.set_source_rgba(0.208, 0.761, 0.608, 1.0)  # #35c29b
        cr.arc(width / 2, height / 2, 5, 0, 2 * 3.14159)
        cr.fill()

    def _on_marker_clicked(
        self,
        gesture: Gtk.GestureClick,
        n_press: int,
        x: float,
        y: float,
        marker: Shumate.Marker,
    ) -> None:
        """Handle marker click to show details."""
        peer = getattr(marker, "_peer", None)
        if peer is None:
            return

        self._selected_peer = peer
        self._show_peer_details(peer)

    def _show_peer_details(self, peer: Peer) -> None:
        """Show details panel for a peer."""
        self._details_title.set_text(peer.display_name)
        self._clear_details()

        # Type badge
        type_label = Gtk.Label(label="Repeater" if peer.is_repeater else "Contact")
        type_label.add_css_class("status-pill")
        type_label.add_css_class("warn" if peer.is_repeater else "ok")
        type_label.set_halign(Gtk.Align.START)
        self._details_content.append(type_label)

        # Location
        if peer.latitude is not None and peer.longitude is not None:
            coord_str = f"{peer.latitude:.6f}, {peer.longitude:.6f}"
            self._details_content.append(DetailRow("Location:", coord_str))

        if peer.location_updated:
            time_str = to_local(peer.location_updated).strftime("%H:%M")
            self._details_content.append(DetailRow("Updated:", time_str))

        # Signal info
        if peer.rssi is not None:
            self._details_content.append(DetailRow("RSSI:", format_rssi(peer.rssi)))
        if peer.snr is not None:
            self._details_content.append(DetailRow("SNR:", format_snr(peer.snr)))

        # Last seen
        if peer.last_advert_time:
            time_str = to_local(peer.last_advert_time).strftime("%b %d at %H:%M")
            self._details_content.append(DetailRow("Last seen:", time_str))

        # Action buttons (only for contacts)
        if not peer.is_repeater:
            actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            actions.set_margin_top(16)

            message_btn = Gtk.Button.new_with_label("Send Message")
            message_btn.connect("clicked", self._on_send_message_clicked, peer)
            actions.append(message_btn)

            self._details_content.append(actions)

        # Close button
        close_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        close_box.set_halign(Gtk.Align.END)
        close_box.set_margin_top(8)

        close_btn = Gtk.Button.new_from_icon_name("window-close-symbolic")
        close_btn.add_css_class("flat")
        close_btn.update_property([Gtk.AccessibleProperty.LABEL], ["Close details"])
        close_btn.connect("clicked", self._on_close_details)
        close_box.append(close_btn)

        self._details_panel.prepend(close_box)
        self._details_panel.set_visible(True)

    def _clear_details(self) -> None:
        """Clear the details content area."""
        # Remove all children except title
        child = self._details_content.get_first_child()
        while child is not None:
            next_child = child.get_next_sibling()
            self._details_content.remove(child)
            child = next_child

        # Also remove close button if present
        child = self._details_panel.get_first_child()
        while child is not None:
            next_child = child.get_next_sibling()
            if child != self._details_title and child != self._details_content:
                self._details_panel.remove(child)
            child = next_child

    def close_active_detail(self) -> bool:
        """Close the details panel if open. Returns True if something was closed."""
        if hasattr(self, "_details_panel") and self._details_panel.get_visible():
            self._selected_peer = None
            self._details_panel.set_visible(False)
            return True
        return False

    def get_default_focus(self) -> Gtk.Widget:
        """Return the widget that should receive focus when this view is shown."""
        if hasattr(self, "_map"):
            return self._map
        return self

    def _on_close_details(self, _button: Gtk.Button) -> None:
        """Close the details panel."""
        self._selected_peer = None
        self._details_panel.set_visible(False)

    def _on_send_message_clicked(self, _button: Gtk.Button, peer: Peer) -> None:
        """Navigate to messages view and start a conversation with this peer."""
        root = self.get_root()
        if root is None:
            return

        stack = getattr(root, "_stack", None)
        if stack is None:
            return

        # Switch to messages view
        stack.set_visible_child_name("messages")

        # Update nav buttons
        nav_buttons = getattr(root, "_nav_buttons", None)
        if nav_buttons:
            for name, btn in nav_buttons.items():
                btn.set_active(name == "messages")

        # Select the peer's channel
        messages_widget = stack.get_child_by_name("messages")
        if messages_widget is not None:
            messages_view = cast("MessagesView", messages_widget)
            messages_view.select_channel(peer.display_name)
