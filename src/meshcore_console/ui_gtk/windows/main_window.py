from __future__ import annotations

import logging
import os
import threading
from typing import TYPE_CHECKING, cast

import gi

if TYPE_CHECKING:
    from meshcore_console.ui_gtk.views.settings import SettingsView

logger = logging.getLogger(__name__)

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, GLib, Gtk
from gi.repository import Pango

from meshcore_console.core.services import MeshcoreService
from meshcore_console.ui_gtk.layout import Layout
from meshcore_console.ui_gtk.state import UiEventStore
from meshcore_console.ui_gtk.views.analyzer import AnalyzerView
from meshcore_console.ui_gtk.views.map import MapView
from meshcore_console.ui_gtk.views.messages import MessagesView
from meshcore_console.ui_gtk.views.peers import PeersView
from meshcore_console.ui_gtk.views.settings import SettingsView
from meshcore_console.ui_gtk.widgets import StatusPill


class MainWindow(Adw.ApplicationWindow):
    def __init__(self, application: Adw.Application, service: MeshcoreService) -> None:
        super().__init__(application=application)
        self._service = service
        self._event_store = UiEventStore(service)
        self._geom_debug = os.environ.get("MESHCORE_UI_GEOM_DEBUG", "0") == "1"
        self._target_width = 1280
        self._target_height = 720
        self._last_window_width = 0
        self._last_window_height = 0
        self._surface_debug_wired = False
        self.set_title("Meshcore Console")
        self._apply_window_geometry()
        self.add_css_class("app-root")

        header_bar = Adw.HeaderBar.new()
        header_bar.add_css_class("app-header")

        # Navigation buttons on the left (set_active called after _stack is created)
        self._nav_buttons: dict[str, Gtk.ToggleButton] = {}
        for label, page_name in [
            ("Analyzer", "analyzer"),
            ("Peers", "peers"),
            ("Channels", "messages"),
            ("Map", "map"),
        ]:
            btn = Gtk.ToggleButton.new_with_label(label)
            btn.add_css_class("nav-button")
            btn.connect("toggled", self._on_nav_button_toggled, page_name)
            self._nav_buttons[page_name] = btn
            header_bar.pack_start(btn)

        # Title in center
        title_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        title = Gtk.Label(label="MeshCore Console")
        title.add_css_class("app-title")
        title.set_halign(Gtk.Align.CENTER)
        status = service.get_status()
        self._subtitle = Gtk.Label(label=self._subtitle_text(status.node_id))
        self._subtitle.add_css_class("app-subtitle")
        self._subtitle.set_halign(Gtk.Align.CENTER)
        self._subtitle.set_ellipsize(Pango.EllipsizeMode.END)
        self._subtitle.set_max_width_chars(28)
        self._subtitle.set_single_line_mode(True)
        title_box.append(title)
        title_box.append(self._subtitle)
        header_bar.set_title_widget(title_box)

        # Right side: status badge, connect button, settings button
        status_text = "Connected" if status.connected else "Offline"
        self._status_badge = StatusPill(status_text, state="ok" if status.connected else "offline")
        self._status_badge.update_property(
            [Gtk.AccessibleProperty.LABEL],
            [f"Connection status: {status_text}"],
        )
        header_bar.pack_end(self._status_badge)

        self._connect_button = Gtk.Button.new_with_label(
            "Disconnect" if status.connected else "Connect"
        )
        self._connect_button.connect("clicked", self._on_connect_toggle)
        header_bar.pack_end(self._connect_button)

        settings_btn = Gtk.Button.new_from_icon_name("emblem-system-symbolic")
        settings_btn.set_tooltip_text("Settings")
        settings_btn.update_property([Gtk.AccessibleProperty.LABEL], ["Settings"])
        settings_btn.connect("clicked", self._on_settings_clicked)
        header_bar.pack_end(settings_btn)

        # Advert button with popover for route type selection
        self._advert_btn = Gtk.MenuButton.new()
        self._advert_btn.set_icon_name("network-transmit-symbolic")
        self._advert_btn.set_tooltip_text("Send Advert")
        self._advert_btn.update_property([Gtk.AccessibleProperty.LABEL], ["Send Advert"])
        self._advert_btn.set_sensitive(status.connected)
        advert_popover = Gtk.Popover.new()
        advert_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        advert_box.set_margin_top(8)
        advert_box.set_margin_bottom(8)
        advert_box.set_margin_start(8)
        advert_box.set_margin_end(8)
        flood_btn = Gtk.Button.new_with_label("Flood Advert")
        flood_btn.connect("clicked", self._on_advert_flood, advert_popover)
        advert_box.append(flood_btn)
        direct_btn = Gtk.Button.new_with_label("Direct Advert")
        direct_btn.connect("clicked", self._on_advert_direct, advert_popover)
        advert_box.append(direct_btn)
        advert_popover.set_child(advert_box)
        self._advert_btn.set_popover(advert_popover)
        header_bar.pack_end(self._advert_btn)

        # Compute proportional layout from target screen width
        layout = Layout(content_width=self._target_width - 16)

        # Stack with views (no sidebar)
        self._stack = Gtk.Stack.new()
        self._stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self._stack.set_hexpand(True)
        self._stack.set_vexpand(True)
        self._stack.add_named(AnalyzerView(service, self._event_store, layout), "analyzer")
        self._stack.add_named(PeersView(service, self._event_store, layout), "peers")
        self._stack.add_named(MessagesView(service, self._event_store, layout), "messages")
        self._stack.add_named(MapView(service, self._event_store, layout), "map")
        self._stack.add_named(SettingsView(service), "settings")
        self._stack.set_visible_child_name("analyzer")

        # Now safe to set nav button active (after _stack exists)
        self._nav_buttons["analyzer"].set_active(True)

        content_pane = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        content_pane.add_css_class("content-pane")
        content_pane.set_hexpand(True)
        content_pane.set_vexpand(True)

        content_pane.append(self._stack)

        page = Adw.ToolbarView.new()
        page.add_top_bar(header_bar)
        page.set_content(content_pane)

        self._toast_overlay = Adw.ToastOverlay.new()
        self._toast_overlay.set_child(page)

        self.set_content(self._toast_overlay)
        self._wire_style_manager()
        self._wire_keyboard_shortcuts()
        # Wire signal-driven event flow: notify → schedule_pump → events-available
        service.set_event_notify(self._event_store.schedule_pump)
        self._event_store.pump()  # Drain pre-queued events (e.g. mock boot)
        self._event_cursor = 0
        self._event_store.connect("events-available", self._on_events_available)
        GLib.timeout_add_seconds(30, self._safety_net_pump)
        GLib.idle_add(self._wire_surface_debug)
        if self._geom_debug:
            GLib.timeout_add(600, self._debug_geometry_tick)
        if self._service.get_settings().autoconnect:
            GLib.idle_add(self._autoconnect)

    def _switch_to_page(self, page_name: str) -> None:
        """Switch the stack to *page_name*, skipping crossfade when a
        transition is already in-flight to avoid a GTK4 freeze-count
        underflow (``gdk_surface_thaw_updates`` assertion).
        """
        # Dismiss any active popover on the current view to avoid
        # surface freeze-count conflicts with the stack crossfade.
        focus = self.get_focus()
        if focus is not None:
            popover = focus.get_ancestor(Gtk.Popover)
            if popover is not None and popover.get_visible():
                popover.popdown()

        if self._stack.get_transition_running():
            saved = self._stack.get_transition_type()
            self._stack.set_transition_type(Gtk.StackTransitionType.NONE)
            self._stack.set_visible_child_name(page_name)
            self._stack.set_transition_type(saved)
        else:
            self._stack.set_visible_child_name(page_name)

    def navigate_to(self, page_name: str, then: object = None) -> None:
        """Navigate to *page_name* and optionally call *then* on the target view.

        *then* should be a ``(method_name, arg)`` tuple — e.g.
        ``("select_peer", peer_id)`` — or ``None`` to just switch pages.
        """
        self._switch_to_page(page_name)
        # Update nav buttons
        if page_name == "settings":
            for btn in self._nav_buttons.values():
                btn.set_active(False)
        elif page_name in self._nav_buttons:
            for name, btn in self._nav_buttons.items():
                btn.set_active(name == page_name)
        self._focus_current_view()
        # Call target method if requested
        if then is not None:
            method_name, arg = then
            view = self._stack.get_child_by_name(page_name)
            if view is not None:
                fn = getattr(view, method_name, None)
                if fn is not None:
                    fn(arg)

    def _on_nav_button_toggled(self, button: Gtk.ToggleButton, page_name: str) -> None:
        logger.debug("UI: nav button toggled page=%s active=%s", page_name, button.get_active())
        if button.get_active():
            # Deactivate other nav buttons
            for name, btn in self._nav_buttons.items():
                if name != page_name:
                    btn.set_active(False)
            self._switch_to_page(page_name)
            self._focus_current_view()
        elif all(not btn.get_active() for btn in self._nav_buttons.values()):
            # Don't allow all buttons to be deactivated
            button.set_active(True)

    def _on_settings_clicked(self, _button: Gtk.Button) -> None:
        logger.debug("UI: settings button clicked")
        # Deactivate all nav buttons when showing settings
        for btn in self._nav_buttons.values():
            btn.set_active(False)
        self._switch_to_page("settings")
        self._focus_current_view()

    def _on_advert_flood(self, _button: Gtk.Button, popover: Gtk.Popover) -> None:
        logger.debug("UI: flood advert button clicked")
        popover.popdown()
        self._send_advert(route_type="flood")

    def _on_advert_direct(self, _button: Gtk.Button, popover: Gtk.Popover) -> None:
        logger.debug("UI: direct advert button clicked")
        popover.popdown()
        self._send_advert(route_type="direct")

    def _send_advert(self, route_type: str) -> None:
        def do_send() -> None:
            error: str | None = None
            try:
                self._service.send_advert(route_type=route_type)
            except Exception as exc:  # noqa: BLE001
                error = str(exc) or type(exc).__name__

            def on_done() -> bool:
                if error:
                    logger.error("Advert failed: %s", error)
                    logger.debug("UI: toast 'Advert failed: %s'", error)
                    self._toast_overlay.add_toast(Adw.Toast.new(f"Advert failed: {error}"))
                else:
                    logger.debug("UI: toast 'Sent %s advert'", route_type)
                    self._toast_overlay.add_toast(Adw.Toast.new(f"Sent {route_type} advert"))
                return False

            GLib.idle_add(on_done)

        threading.Thread(target=do_send, daemon=True).start()

    def _wire_style_manager(self) -> None:
        manager = Adw.StyleManager.get_default()
        manager.set_color_scheme(Adw.ColorScheme.FORCE_DARK)
        self._sync_theme_class()

    def _sync_theme_class(self) -> None:
        self.remove_css_class("mc-light")
        self.remove_css_class("mc-dark")
        self.add_css_class("mc-dark")

    def _apply_window_geometry(self) -> None:
        width = 1280
        height = 720
        display = Gdk.Display.get_default()
        if display is not None:
            monitors = display.get_monitors()
            if monitors.get_n_items() > 0:
                monitor = monitors.get_item(0)
                if monitor is not None:
                    geometry = monitor.get_geometry()
                    screen_w, screen_h = geometry.width, geometry.height
                    logger.debug("MainWindow: screen geometry %dx%d", screen_w, screen_h)
                    # On small screens (like uconsole 1280x720), maximize to fit.
                    # Cap at 1280x720 — some compositors report a larger logical
                    # geometry than the physical display which causes overflow.
                    if screen_h <= 720:
                        self._target_width = min(screen_w, 1280)
                        self._target_height = min(screen_h, 720)
                        self.set_default_size(self._target_width, self._target_height)
                        self.maximize()
                        return
                    width = min(width, screen_w)
                    height = min(height, screen_h - 100)  # Reserve for panels
        self._target_width = width
        self._target_height = height
        self.set_default_size(width, height)
        self.set_resizable(True)

    def _wire_surface_debug(self) -> bool:
        if not self._geom_debug:
            return False
        if self._surface_debug_wired:
            return False
        native = self.get_native()
        if native is None:
            return True
        surface = native.get_surface()
        if surface is None:
            return True

        def on_layout(s: Gdk.Surface, width: int, height: int) -> None:
            print(
                f"[ui-geom] surface-layout width={width} height={height} "
                f"surface_now={s.get_width()}x{s.get_height()} "
                f"window_now={self.get_width()}x{self.get_height()}"
            )

        surface.connect("layout", on_layout)
        if isinstance(surface, Gdk.Toplevel):
            toplevel: Gdk.Toplevel = surface

            def on_state_change(obj: object, _pspec: object) -> None:
                try:
                    state = toplevel.get_state()
                    print(f"[ui-geom] toplevel-state changed state={int(state)}")
                except Exception as exc:  # noqa: BLE001
                    print(f"[ui-geom] toplevel-state logging error: {exc}")

            surface.connect("notify::state", on_state_change)

        self._surface_debug_wired = True
        print("[ui-geom] surface debug wired")
        return False

    def _debug_geometry_tick(self) -> bool:
        try:
            win_w = self.get_width()
            win_h = self.get_height()
            visible = self._stack.get_visible_child_name()
            print(f"[ui-geom] window={win_w}x{win_h} visible={visible}")
            print(f"[ui-geom] stack={self._stack.get_width()}x{self._stack.get_height()}")
            for name in ("analyzer", "peers", "messages", "map", "settings"):
                child = self._stack.get_child_by_name(name)
                if child is not None:
                    min_w, nat_w, _min_b, _nat_b = child.measure(Gtk.Orientation.HORIZONTAL, -1)
                    print(f"[ui-geom] {name} pref={min_w}/{nat_w}")
            if win_w != self._last_window_width or win_h != self._last_window_height:
                self._debug_display_state("window-size-changed")
                self._last_window_width = win_w
                self._last_window_height = win_h
        except Exception as exc:  # noqa: BLE001
            print(f"[ui-geom] logging error: {exc}")
        return True

    def _debug_display_state(self, reason: str) -> None:
        display = Gdk.Display.get_default()
        if display is None:
            print(f"[ui-geom] display-state({reason}) no display")
            return
        print(f"[ui-geom] display-state({reason}) name={display.get_name()}")
        monitors = display.get_monitors()
        n = monitors.get_n_items()
        print(f"[ui-geom] display-state({reason}) monitors={n}")
        for i in range(n):
            monitor = monitors.get_item(i)
            if monitor is None:
                continue
            geometry = monitor.get_geometry()
            print(
                f"[ui-geom] monitor[{i}] geom={geometry.width}x{geometry.height}+{geometry.x}+{geometry.y} "
                f"scale={monitor.get_scale_factor()} scale_f={monitor.get_scale()}"
            )
        native = self.get_native()
        if native is None:
            print(f"[ui-geom] display-state({reason}) no native")
            return
        surface = native.get_surface()
        if surface is None:
            print(f"[ui-geom] display-state({reason}) no surface")
            return
        print(
            f"[ui-geom] surface={surface.get_width()}x{surface.get_height()} "
            f"scale={surface.get_scale_factor()} scale_f={surface.get_scale()}"
        )

    def _autoconnect(self) -> bool:
        """Attempt to connect to the radio automatically on startup."""
        logger.info("Autoconnect enabled, connecting...")
        self._connect_button.set_sensitive(False)
        self._connect_button.set_label("Connecting...")

        def do_connect() -> None:
            error: str | None = None
            try:
                self._service.connect()
            except Exception as exc:  # noqa: BLE001
                error = str(exc)
            GLib.idle_add(self._finish_connect_toggle, False, error)

        threading.Thread(target=do_connect, daemon=True).start()
        return False  # Don't repeat idle callback

    def _on_connect_toggle(self, _button: Gtk.Button) -> None:
        # Use button label to determine action (avoids state sync issues)
        current_label = self._connect_button.get_label()
        is_disconnect = current_label == "Disconnect"
        logger.debug(
            "UI: connect toggle clicked, action=%s", "disconnect" if is_disconnect else "connect"
        )

        # Disable button and show pending state
        self._connect_button.set_sensitive(False)
        self._connect_button.set_label("Disconnecting..." if is_disconnect else "Connecting...")

        def do_toggle() -> None:
            error: str | None = None
            try:
                if is_disconnect:
                    self._service.disconnect()
                else:
                    self._service.connect()
            except Exception as exc:  # noqa: BLE001
                error = str(exc)

            # Schedule UI update on main thread
            GLib.idle_add(self._finish_connect_toggle, is_disconnect, error)

        threading.Thread(target=do_toggle, daemon=True).start()

    def _finish_connect_toggle(self, was_disconnect: bool, error: str | None) -> bool:
        self._connect_button.set_sensitive(True)
        if error:
            logger.error("Connection error: %s", error)
            logger.debug("UI: toast 'Connection error: %s'", error)
            self._toast_overlay.add_toast(Adw.Toast.new(f"Connection error: {error}"))
        else:
            msg = "Disconnected" if was_disconnect else "Connected"
            logger.debug("UI: toast '%s'", msg)
            self._toast_overlay.add_toast(Adw.Toast.new(msg))
        self._refresh_connection_state()
        return False  # Don't repeat

    def _refresh_connection_state(self) -> None:
        status = self._service.get_status()
        status_text = "Connected" if status.connected else "Offline"
        self._status_badge.set_text(status_text)
        self._status_badge.set_state("ok" if status.connected else "offline")
        self._status_badge.update_property(
            [Gtk.AccessibleProperty.LABEL],
            [f"Connection status: {status_text}"],
        )
        self._connect_button.set_label("Disconnect" if status.connected else "Connect")
        self._advert_btn.set_sensitive(status.connected)
        self._subtitle.set_text(self._subtitle_text(status.node_id))
        # Refresh the settings public key (only available after connect)
        settings_widget = self._stack.get_child_by_name("settings")
        if settings_widget is not None:
            settings_view = cast("SettingsView", settings_widget)
            settings_view.refresh_public_key()

    def _on_events_available(self, _store: object) -> None:
        """Handle events-available signal from UiEventStore."""
        self._event_cursor, events = self._event_store.since(self._event_cursor, limit=200)
        for event in events:
            etype = event.get("type", "")
            if etype == "settings_updated":
                self._refresh_connection_state()
                break
            if etype in ("session_connected", "session_disconnected"):
                self._refresh_connection_state()
                break
        self._service.flush_stores()

    def _safety_net_pump(self) -> bool:
        """Low-frequency backup flush + pump in case notify was missed."""
        self._service.flush_stores()
        self._event_store.pump()
        return True

    def _wire_keyboard_shortcuts(self) -> None:
        controller = Gtk.EventControllerKey.new()
        controller.connect("key-pressed", self._on_key_pressed)
        self.add_controller(controller)

    def _on_key_pressed(
        self,
        _controller: Gtk.EventControllerKey,
        keyval: int,
        _keycode: int,
        state: Gdk.ModifierType,
    ) -> bool:
        # Escape: close active detail panel in the current view
        if keyval == Gdk.KEY_Escape:
            view = self._stack.get_visible_child()
            close_fn = getattr(view, "close_active_detail", None)
            if close_fn is not None and close_fn():
                return True
            return False

        if not (state & Gdk.ModifierType.CONTROL_MASK):
            return False

        mapping = {
            Gdk.KEY_1: "analyzer",
            Gdk.KEY_2: "peers",
            Gdk.KEY_3: "messages",
            Gdk.KEY_4: "map",
            Gdk.KEY_5: "settings",
        }
        page = mapping.get(keyval)
        if page is None:
            return False

        logger.debug("UI: keyboard shortcut Ctrl+%s → %s", keyval - Gdk.KEY_0, page)
        self._switch_to_page(page)
        # Update nav button states
        if page == "settings":
            for btn in self._nav_buttons.values():
                btn.set_active(False)
        elif page in self._nav_buttons:
            for name, btn in self._nav_buttons.items():
                btn.set_active(name == page)
        self._focus_current_view()
        return True

    def _focus_current_view(self) -> None:
        """Move focus to the default widget in the current view after transition."""

        def do_focus() -> bool:
            view = self._stack.get_visible_child()
            focus_fn = getattr(view, "get_default_focus", None)
            if focus_fn is not None:
                target = focus_fn()
                if target is not None:
                    target.grab_focus()
            return False  # GLib.SOURCE_REMOVE

        GLib.idle_add(do_focus)

    @staticmethod
    def _subtitle_text(node_id: str) -> str:
        return f"Node: {node_id}" if node_id else "Node: unknown"
