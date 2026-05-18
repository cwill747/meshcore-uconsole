from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import Gdk, GLib, Gtk, Pango

from meshcore_console.core.models import Peer
from meshcore_console.core.radio import format_rssi, format_snr
from meshcore_console.core.services import MeshcoreService
from meshcore_console.core.time import to_local
from meshcore_console.ui_gtk.helpers import clear_children
from meshcore_console.ui_gtk.layout import Layout
from meshcore_console.ui_gtk.state import UiEventStore
from meshcore_console.ui_gtk.widgets import DetailRow

if TYPE_CHECKING:
    from meshcore_console.core.models import RepeaterLoginState

logger = logging.getLogger(__name__)

_MAX_HISTORY = 50


class AdminView(Gtk.Box):
    def __init__(self, service: MeshcoreService, event_store: UiEventStore, layout: Layout) -> None:
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._service = service
        self._event_store = event_store
        self._event_cursor = 0
        self._selected_repeater: Peer | None = None
        self._command_history: list[str] = []
        self._history_index = -1
        self._busy = False
        self._refreshing_list = False

        # -- Left: repeater list -----------------------------------------------
        list_col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        list_col.add_css_class("panel-card")
        list_col.set_size_request(layout.admin_list_width, -1)

        header = Gtk.Label(label="Repeaters")
        header.add_css_class("panel-title")
        header.set_halign(Gtk.Align.START)
        header.set_margin_start(12)
        header.set_margin_top(10)
        header.set_margin_bottom(8)
        list_col.append(header)

        scroll = Gtk.ScrolledWindow.new()
        scroll.set_vexpand(True)
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        self._repeater_list = Gtk.ListBox.new()
        self._repeater_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self._repeater_list.connect("row-selected", self._on_repeater_selected)
        scroll.set_child(self._repeater_list)
        list_col.append(scroll)
        self.append(list_col)

        # -- Right: panel with notebook ----------------------------------------
        panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        panel.add_css_class("panel-card")
        panel.set_hexpand(True)

        self._panel_stack = Gtk.Stack.new()
        self._panel_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self._panel_stack.set_transition_duration(150)

        # Empty state
        empty = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        empty.set_valign(Gtk.Align.CENTER)
        empty.set_halign(Gtk.Align.CENTER)
        empty_label = Gtk.Label(label="Select a repeater")
        empty_label.add_css_class("panel-muted")
        empty.append(empty_label)
        self._panel_stack.add_named(empty, "empty")

        # Admin panel with title + notebook
        admin_outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)

        self._admin_title = Gtk.Label(label="")
        self._admin_title.add_css_class("panel-title")
        self._admin_title.set_halign(Gtk.Align.START)
        self._admin_title.set_margin_start(12)
        self._admin_title.set_margin_top(10)
        self._admin_title.set_margin_bottom(4)
        admin_outer.append(self._admin_title)

        # Login bar (always visible at top of admin panel)
        self._login_box = self._build_login_bar()
        admin_outer.append(self._login_box)

        sep = Gtk.Separator.new(Gtk.Orientation.HORIZONTAL)
        sep.set_margin_start(12)
        sep.set_margin_end(12)
        admin_outer.append(sep)

        # Notebook with Status and Console tabs
        self._notebook = Gtk.Notebook.new()
        self._notebook.set_vexpand(True)
        self._notebook.set_margin_start(4)
        self._notebook.set_margin_end(4)
        self._notebook.set_margin_bottom(4)

        # Status tab
        self._status_scroll = Gtk.ScrolledWindow.new()
        self._status_scroll.set_vexpand(True)
        self._status_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self._status_content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self._status_content.set_margin_start(12)
        self._status_content.set_margin_end(12)
        self._status_content.set_margin_top(8)
        self._status_content.set_margin_bottom(8)
        self._status_scroll.set_child(self._status_content)
        self._notebook.append_page(self._status_scroll, Gtk.Label(label="Status"))

        # Console tab
        console_page = self._build_console_tab()
        self._notebook.append_page(console_page, Gtk.Label(label="Console"))

        admin_outer.append(self._notebook)
        self._panel_stack.add_named(admin_outer, "admin")

        self._panel_stack.set_visible_child_name("empty")
        panel.append(self._panel_stack)
        self.append(panel)

        # Wire events
        event_store.connect("events-available", self._on_events)
        GLib.idle_add(self._refresh_list)

    # -- Build login bar -------------------------------------------------------

    def _build_login_bar(self) -> Gtk.Box:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.set_margin_start(12)
        box.set_margin_end(12)
        box.set_margin_bottom(8)

        # Status line
        self._status_label = Gtk.Label(label="Not logged in")
        self._status_label.add_css_class("login-status-offline")
        self._status_label.set_halign(Gtk.Align.START)
        box.append(self._status_label)

        # Password row
        pw_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self._password_entry = Gtk.PasswordEntry.new()
        self._password_entry.set_property("placeholder-text", "Password")
        self._password_entry.set_show_peek_icon(True)
        self._password_entry.set_hexpand(True)
        self._password_entry.connect("activate", self._on_login_activate)
        pw_row.append(self._password_entry)

        self._login_btn = Gtk.Button.new_with_label("Login")
        self._login_btn.connect("clicked", self._on_login_clicked)
        pw_row.append(self._login_btn)

        self._guest_btn = Gtk.Button.new_with_label("Guest")
        self._guest_btn.connect("clicked", self._on_guest_clicked)
        pw_row.append(self._guest_btn)
        box.append(pw_row)

        # Save password / forget / logout row
        save_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._save_check = Gtk.CheckButton.new_with_label("Save password")
        save_row.append(self._save_check)

        self._forget_btn = Gtk.Button.new_with_label("Forget saved")
        self._forget_btn.connect("clicked", self._on_forget_clicked)
        self._forget_btn.set_visible(False)
        save_row.append(self._forget_btn)

        self._logout_btn = Gtk.Button.new_with_label("Logout")
        self._logout_btn.connect("clicked", self._on_logout_clicked)
        self._logout_btn.set_sensitive(False)
        save_row.append(self._logout_btn)
        box.append(save_row)

        return box

    # -- Build console tab -----------------------------------------------------

    def _build_console_tab(self) -> Gtk.Box:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)

        # Quick command buttons
        quick_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        quick_row.set_margin_start(8)
        quick_row.set_margin_end(8)
        quick_row.set_margin_top(8)
        quick_row.set_margin_bottom(4)
        for cmd in ("status", "ver", "neighbors", "help", "log"):
            btn = Gtk.Button.new_with_label(cmd)
            btn.add_css_class("quick-cmd-btn")
            btn.connect("clicked", self._on_quick_cmd, cmd)
            quick_row.append(btn)
        self._quick_row = quick_row
        box.append(quick_row)

        # Console output
        console_scroll = Gtk.ScrolledWindow.new()
        console_scroll.set_vexpand(True)
        console_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        console_scroll.set_margin_start(8)
        console_scroll.set_margin_end(8)

        self._console_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self._console_box.add_css_class("cli-console")
        console_scroll.set_child(self._console_box)
        self._console_scroll = console_scroll
        box.append(console_scroll)

        # Command entry
        entry_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        entry_row.set_margin_start(8)
        entry_row.set_margin_end(8)
        entry_row.set_margin_top(4)
        entry_row.set_margin_bottom(8)

        self._cmd_entry = Gtk.Entry.new()
        self._cmd_entry.add_css_class("cli-entry")
        self._cmd_entry.set_placeholder_text("Type a command...")
        self._cmd_entry.set_hexpand(True)
        self._cmd_entry.connect("activate", self._on_cmd_activate)

        key_ctrl = Gtk.EventControllerKey.new()
        key_ctrl.connect("key-pressed", self._on_cmd_key)
        self._cmd_entry.add_controller(key_ctrl)

        entry_row.append(self._cmd_entry)

        self._send_btn = Gtk.Button.new_with_label("Send")
        self._send_btn.connect("clicked", self._on_send_clicked)
        entry_row.append(self._send_btn)
        box.append(entry_row)

        self._set_cli_sensitive(False)
        return box

    # -- Build status tab content ----------------------------------------------

    def _populate_status(self, peer: Peer) -> None:
        clear_children(self._status_content)

        # Signal section
        self._add_section_header("Signal")

        if peer.rssi is not None:
            self._status_content.append(DetailRow("RSSI:", format_rssi(peer.rssi)))
        if peer.snr is not None:
            self._status_content.append(DetailRow("SNR:", format_snr(peer.snr)))
        if peer.signal_quality is not None:
            self._status_content.append(DetailRow("Quality:", f"{peer.signal_quality}%"))

        if peer.rssi is None and peer.snr is None and peer.signal_quality is None:
            no_signal = Gtk.Label(label="No signal data available")
            no_signal.add_css_class("panel-muted")
            no_signal.set_halign(Gtk.Align.START)
            self._status_content.append(no_signal)

        # Location section
        self._add_section_header("Location")

        if peer.latitude is not None and peer.longitude is not None:
            lat_dir = "N" if peer.latitude >= 0 else "S"
            lon_dir = "E" if peer.longitude >= 0 else "W"
            coords = f"{abs(peer.latitude):.5f}° {lat_dir}, {abs(peer.longitude):.5f}° {lon_dir}"
            self._status_content.append(DetailRow("Coordinates:", coords))

            if peer.location_updated:
                loc_time = to_local(peer.location_updated).strftime("%b %d at %H:%M")
                self._status_content.append(DetailRow("Updated:", loc_time))
        else:
            no_loc = Gtk.Label(label="No location reported")
            no_loc.add_css_class("panel-muted")
            no_loc.set_halign(Gtk.Align.START)
            self._status_content.append(no_loc)

        # Activity section
        self._add_section_header("Activity")

        if peer.last_advert_time:
            time_str = to_local(peer.last_advert_time).strftime("%b %d, %Y at %I:%M %p")
            self._status_content.append(DetailRow("Last Seen:", time_str))
        else:
            self._status_content.append(DetailRow("Last Seen:", "Unknown"))

        # Login state section
        state = self._service.get_repeater_login_state(peer.display_name)
        self._add_section_header("Login")

        if state is not None:
            role = "Admin" if state.is_admin else "Guest"
            self._status_content.append(DetailRow("Role:", role))
            if state.firmware_ver_level is not None:
                self._status_content.append(DetailRow("Firmware:", f"v{state.firmware_ver_level}"))
            if state.keep_alive_interval:
                self._status_content.append(
                    DetailRow("Keep-alive:", f"{state.keep_alive_interval}s")
                )
        else:
            not_logged = Gtk.Label(label="Not logged in")
            not_logged.add_css_class("panel-muted")
            not_logged.set_halign(Gtk.Align.START)
            self._status_content.append(not_logged)

        # Public key section
        if peer.public_key:
            self._add_section_header("Public Key")
            chunks = [peer.public_key[i : i + 4] for i in range(0, len(peer.public_key), 4)]
            key_label = Gtk.Label(label=" ".join(chunks))
            key_label.add_css_class("analyzer-raw")
            key_label.set_halign(Gtk.Align.START)
            key_label.set_wrap(True)
            key_label.set_wrap_mode(Pango.WrapMode.CHAR)
            key_label.set_selectable(True)
            self._status_content.append(key_label)

    def _add_section_header(self, title: str) -> None:
        header = Gtk.Label(label=title)
        header.add_css_class("message-detail-header")
        header.set_halign(Gtk.Align.START)
        header.set_margin_top(12)
        self._status_content.append(header)

    # -- List management -------------------------------------------------------

    def _refresh_list(self) -> bool:
        peers = self._service.list_peers()
        repeaters = sorted(
            [p for p in peers if p.is_repeater],
            key=lambda p: (not p.is_favorite, p.display_name),
        )
        logged_in = set(self._service.list_logged_in_repeaters())

        # Suppress selection handler during rebuild
        self._refreshing_list = True

        child = self._repeater_list.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self._repeater_list.remove(child)
            child = nxt

        for rp in repeaters:
            row = self._make_repeater_row(rp, rp.display_name in logged_in)
            self._repeater_list.append(row)

        # Re-select if still valid, and update the peer object
        if self._selected_repeater:
            for i, rp in enumerate(repeaters):
                if rp.display_name == self._selected_repeater.display_name:
                    self._selected_repeater = rp
                    row_widget = self._repeater_list.get_row_at_index(i)
                    if row_widget:
                        self._repeater_list.select_row(row_widget)
                    break

        self._refreshing_list = False
        return False

    def _make_repeater_row(self, peer: Peer, logged_in: bool) -> Gtk.ListBoxRow:
        row = Gtk.ListBoxRow.new()
        row._peer = peer  # type: ignore[attr-defined]
        hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        hbox.add_css_class("admin-repeater-row")

        dot = Gtk.Label(label="●")
        dot.add_css_class("admin-login-dot" if logged_in else "admin-login-dot-off")
        hbox.append(dot)

        name = Gtk.Label(label=peer.display_name)
        name.set_halign(Gtk.Align.START)
        name.set_hexpand(True)
        name.set_ellipsize(Pango.EllipsizeMode.END)
        name.set_max_width_chars(20)
        hbox.append(name)

        if peer.is_favorite:
            star = Gtk.Image.new_from_icon_name("starred-symbolic")
            star.add_css_class("panel-muted")
            hbox.append(star)

        if peer.rssi is not None:
            rssi = Gtk.Label(label=f"{peer.rssi} dBm")
            rssi.add_css_class("panel-muted")
            hbox.append(rssi)

        row.set_child(hbox)
        return row

    # -- Selection -------------------------------------------------------------

    def _on_repeater_selected(self, _listbox: Gtk.ListBox, row: Gtk.ListBoxRow | None) -> None:
        if self._refreshing_list:
            return
        if row is None:
            self._selected_repeater = None
            self._panel_stack.set_visible_child_name("empty")
            return
        peer: Peer = row._peer  # type: ignore[attr-defined]
        self._selected_repeater = peer
        self._show_admin_panel(peer)

    def _show_admin_panel(self, peer: Peer) -> None:
        self._admin_title.set_text(peer.display_name)
        self._panel_stack.set_visible_child_name("admin")

        # Update login state UI
        state = self._service.get_repeater_login_state(peer.display_name)
        self._update_login_ui(state)

        # Pre-fill saved password
        saved = self._service.get_saved_repeater_password(peer.display_name)
        if saved:
            self._password_entry.set_text(saved)
            self._forget_btn.set_visible(True)
            self._save_check.set_active(True)
        else:
            self._password_entry.set_text("")
            self._forget_btn.set_visible(False)
            self._save_check.set_active(False)

        # Populate status tab
        self._populate_status(peer)

        # Clear console for new repeater
        clear_children(self._console_box)

    def _update_login_ui(self, state: RepeaterLoginState | None) -> None:
        if state is None:
            self._status_label.set_text("Not logged in")
            self._status_label.remove_css_class("login-status-admin")
            self._status_label.remove_css_class("login-status-guest")
            self._status_label.add_css_class("login-status-offline")
            self._login_btn.set_sensitive(True)
            self._guest_btn.set_sensitive(True)
            self._password_entry.set_sensitive(True)
            self._logout_btn.set_sensitive(False)
            self._set_cli_sensitive(False)
        else:
            if state.is_admin:
                self._status_label.set_text("Logged in (admin)")
                self._status_label.remove_css_class("login-status-offline")
                self._status_label.remove_css_class("login-status-guest")
                self._status_label.add_css_class("login-status-admin")
            else:
                self._status_label.set_text("Logged in (guest)")
                self._status_label.remove_css_class("login-status-offline")
                self._status_label.remove_css_class("login-status-admin")
                self._status_label.add_css_class("login-status-guest")
            if state.firmware_ver_level is not None:
                self._status_label.set_text(
                    self._status_label.get_text() + f"  FW: v{state.firmware_ver_level}"
                )
            self._login_btn.set_sensitive(False)
            self._guest_btn.set_sensitive(False)
            self._password_entry.set_sensitive(False)
            self._logout_btn.set_sensitive(True)
            self._set_cli_sensitive(True)

    def _set_cli_sensitive(self, sensitive: bool) -> None:
        self._cmd_entry.set_sensitive(sensitive)
        self._send_btn.set_sensitive(sensitive)
        child = self._quick_row.get_first_child()
        while child is not None:
            child.set_sensitive(sensitive)
            child = child.get_next_sibling()

    # -- Login actions ---------------------------------------------------------

    def _on_login_activate(self, _entry: Gtk.PasswordEntry) -> None:
        self._do_login(self._password_entry.get_text())

    def _on_login_clicked(self, _btn: Gtk.Button) -> None:
        self._do_login(self._password_entry.get_text())

    def _on_guest_clicked(self, _btn: Gtk.Button) -> None:
        self._do_login("")

    def _do_login(self, password: str) -> None:
        if self._selected_repeater is None or self._busy:
            return
        peer_name = self._selected_repeater.display_name
        save = self._save_check.get_active()
        self._busy = True
        self._login_btn.set_sensitive(False)
        self._guest_btn.set_sensitive(False)
        self._status_label.set_text("Logging in...")

        def work() -> None:
            error: str | None = None
            result: dict = {}
            try:
                result = self._service.login_to_repeater(peer_name, password, save_password=save)
            except Exception as exc:
                error = str(exc) or type(exc).__name__

            def done() -> bool:
                self._busy = False
                if error:
                    self._append_console_error(f"Login failed: {error}")
                    self._login_btn.set_sensitive(True)
                    self._guest_btn.set_sensitive(True)
                    self._update_login_ui(None)
                elif not result.get("success"):
                    reason = result.get("reason", "Login failed")
                    self._append_console_error(reason)
                    self._login_btn.set_sensitive(True)
                    self._guest_btn.set_sensitive(True)
                    self._update_login_ui(None)
                else:
                    state = self._service.get_repeater_login_state(peer_name)
                    self._update_login_ui(state)
                    self._append_console_response("Login successful")
                    # Refresh status tab to show login info
                    if self._selected_repeater:
                        self._populate_status(self._selected_repeater)
                    self._refresh_list()
                return False

            GLib.idle_add(done)

        threading.Thread(target=work, daemon=True).start()

    def _on_logout_clicked(self, _btn: Gtk.Button) -> None:
        if self._selected_repeater is None:
            return
        peer_name = self._selected_repeater.display_name
        self._service.logout_from_repeater(peer_name)
        self._update_login_ui(None)
        self._append_console_response("Logged out")
        if self._selected_repeater:
            self._populate_status(self._selected_repeater)
        self._refresh_list()

    def _on_forget_clicked(self, _btn: Gtk.Button) -> None:
        if self._selected_repeater is None:
            return
        self._service.delete_saved_repeater_password(self._selected_repeater.display_name)
        self._password_entry.set_text("")
        self._forget_btn.set_visible(False)
        self._save_check.set_active(False)

    # -- CLI actions -----------------------------------------------------------

    def _on_quick_cmd(self, _btn: Gtk.Button, command: str) -> None:
        self._send_command(command)

    def _on_cmd_activate(self, _entry: Gtk.Entry) -> None:
        text = self._cmd_entry.get_text().strip()
        if text:
            self._send_command(text)

    def _on_send_clicked(self, _btn: Gtk.Button) -> None:
        text = self._cmd_entry.get_text().strip()
        if text:
            self._send_command(text)

    def _on_cmd_key(
        self,
        _ctrl: Gtk.EventControllerKey,
        keyval: int,
        _keycode: int,
        _state: Gdk.ModifierType,
    ) -> bool:
        if keyval == Gdk.KEY_Up:
            if self._command_history and self._history_index < len(self._command_history) - 1:
                self._history_index += 1
                self._cmd_entry.set_text(self._command_history[-(self._history_index + 1)])
                self._cmd_entry.set_position(-1)
            return True
        if keyval == Gdk.KEY_Down:
            if self._history_index > 0:
                self._history_index -= 1
                self._cmd_entry.set_text(self._command_history[-(self._history_index + 1)])
                self._cmd_entry.set_position(-1)
            elif self._history_index == 0:
                self._history_index = -1
                self._cmd_entry.set_text("")
            return True
        return False

    def _send_command(self, command: str) -> None:
        if self._selected_repeater is None or self._busy:
            return
        peer_name = self._selected_repeater.display_name

        # History
        self._command_history.append(command)
        if len(self._command_history) > _MAX_HISTORY:
            self._command_history = self._command_history[-_MAX_HISTORY:]
        self._history_index = -1

        self._cmd_entry.set_text("")
        self._append_console_command(command)
        self._busy = True
        self._send_btn.set_sensitive(False)

        def work() -> None:
            error: str | None = None
            result: dict = {}
            try:
                result = self._service.send_repeater_command(peer_name, command)
            except Exception as exc:
                error = str(exc) or type(exc).__name__

            def done() -> bool:
                self._busy = False
                self._send_btn.set_sensitive(True)
                if error:
                    self._append_console_error(f"Error: {error}")
                elif not result.get("success"):
                    reason = result.get("reason", "No response")
                    self._append_console_error(reason)
                else:
                    text = result.get("response_text", "")
                    if text:
                        self._append_console_response(text)
                    else:
                        self._append_console_response("(no response)")
                return False

            GLib.idle_add(done)

        threading.Thread(target=work, daemon=True).start()

    # -- Console output --------------------------------------------------------

    def _append_console_command(self, text: str) -> None:
        label = Gtk.Label(label=f"> {text}")
        label.add_css_class("cli-command")
        label.set_halign(Gtk.Align.START)
        label.set_wrap(True)
        label.set_wrap_mode(Pango.WrapMode.CHAR)
        label.set_selectable(True)
        self._console_box.append(label)
        self._scroll_console()

    def _append_console_response(self, text: str) -> None:
        label = Gtk.Label(label=text)
        label.add_css_class("cli-response")
        label.set_halign(Gtk.Align.START)
        label.set_wrap(True)
        label.set_wrap_mode(Pango.WrapMode.CHAR)
        label.set_selectable(True)
        self._console_box.append(label)
        self._scroll_console()

    def _append_console_error(self, text: str) -> None:
        label = Gtk.Label(label=text)
        label.add_css_class("cli-error")
        label.set_halign(Gtk.Align.START)
        label.set_wrap(True)
        label.set_wrap_mode(Pango.WrapMode.CHAR)
        label.set_selectable(True)
        self._console_box.append(label)
        self._scroll_console()

    def _scroll_console(self) -> None:
        def _do_scroll() -> bool:
            adj = self._console_scroll.get_vadjustment()
            adj.set_value(adj.get_upper())
            return False

        GLib.idle_add(_do_scroll)

    # -- Events ----------------------------------------------------------------

    def _on_events(self, _store: object) -> None:
        self._event_cursor, events = self._event_store.since(self._event_cursor, limit=200)
        needs_refresh = False
        for event in events:
            etype = event.get("type", "")
            if etype in (
                "repeater_login",
                "repeater_logout",
                "advert_received",
                "contact_received",
            ):
                needs_refresh = True
        if needs_refresh:
            self._refresh_list()

    # -- Public API for MainWindow.navigate_to ---------------------------------

    def get_default_focus(self) -> Gtk.Widget | None:
        return self._repeater_list
