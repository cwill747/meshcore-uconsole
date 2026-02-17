from __future__ import annotations

import logging

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, GLib, Gtk, Pango

from meshcore_console.core.models import Channel, Message
from meshcore_console.core.radio import snr_to_quality
from meshcore_console.core.services import MeshcoreService
from meshcore_console.core.time import to_local
from meshcore_console.ui_gtk.widgets import (
    DaySeparator,
    DetailRow,
    EmptyState,
    MessageBubble,
    PathVisualization,
)
from meshcore_console.ui_gtk.widgets.mention import parse_mentions
from meshcore_console.ui_gtk.widgets.node_badge import STYLE_DEFAULT, STYLE_SELF, find_peer_for_hop

logger = logging.getLogger(__name__)


class MessagesView(Gtk.Box):
    def __init__(self, service: MeshcoreService) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self._service = service
        self._selected_channel_id = "public"
        self._last_message_count = 0
        self._last_channel_count = 0
        self._last_message_date: str | None = None
        self._selected_message: Message | None = None

        # Main content area (channels + chat)
        split = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        split.set_vexpand(True)
        self.append(split)

        channel_panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        channel_panel.set_size_request(160, -1)

        self._channel_list = Gtk.ListBox.new()
        self._channel_list.add_css_class("panel-card")
        self._channel_list.add_css_class("channel-list")
        self._channel_list.add_css_class("side-list")
        self._channel_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self._channel_list.connect("row-selected", self._on_channel_selected)
        self._channel_list.set_vexpand(True)
        channel_panel.append(self._channel_list)

        add_channel_btn = Gtk.Button.new()
        add_channel_btn.set_child(Gtk.Label(label="+ Add Channel"))
        add_channel_btn.add_css_class("flat")
        add_channel_btn.set_margin_start(4)
        add_channel_btn.set_margin_end(4)
        add_channel_btn.set_margin_top(4)
        add_channel_btn.set_margin_bottom(4)
        add_channel_btn.connect("clicked", self._on_add_channel_clicked)
        channel_panel.append(add_channel_btn)

        split.append(channel_panel)

        # Chat area with scrolling
        self._chat_panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self._chat_panel.add_css_class("panel-card")
        self._chat_panel.add_css_class("chat-panel")
        self._chat_panel.set_hexpand(True)
        self._chat_panel.set_vexpand(True)

        self._thread_title = Gtk.Label(label="#public")
        self._thread_title.add_css_class("panel-title")
        self._thread_title.set_halign(Gtk.Align.START)
        self._thread_title.set_margin_start(12)
        self._thread_title.set_margin_top(8)
        self._thread_title.set_margin_bottom(8)
        self._chat_panel.append(self._thread_title)

        # Scrolled window for messages
        self._scroll = Gtk.ScrolledWindow.new()
        self._scroll.set_vexpand(True)
        self._scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self._chat_panel.append(self._scroll)

        self._message_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self._message_box.set_margin_start(12)
        self._message_box.set_margin_end(12)
        self._message_box.set_margin_top(8)
        self._message_box.set_margin_bottom(8)
        self._scroll.set_child(self._message_box)

        # Message details revealer
        self._details_revealer = Gtk.Revealer.new()
        self._details_revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_UP)
        self._details_revealer.set_reveal_child(False)
        self._chat_panel.append(self._details_revealer)

        self._details_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self._details_box.add_css_class("message-details")
        self._details_box.set_margin_start(12)
        self._details_box.set_margin_end(12)
        self._details_box.set_margin_top(8)
        self._details_box.set_margin_bottom(8)
        self._details_revealer.set_child(self._details_box)

        split.append(self._chat_panel)

        # Compose area - inside chat panel at bottom
        compose = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        compose.add_css_class("composer-box")
        compose.set_margin_start(12)
        compose.set_margin_end(12)
        compose.set_margin_top(8)
        compose.set_margin_bottom(8)
        # Emoji picker button (popover created lazily to avoid Pango emoji
        # font lookup which crashes in headless / Nix environments)
        emoji_btn = Gtk.MenuButton.new()
        emoji_btn.set_icon_name("face-smile-symbolic")
        emoji_btn.set_tooltip_text("Insert emoji")
        emoji_btn.add_css_class("flat")
        emoji_btn.connect("notify::active", self._ensure_emoji_chooser)
        self._emoji_btn = emoji_btn
        compose.append(emoji_btn)

        self._entry = Gtk.Entry.new()
        self._entry.set_placeholder_text("Type message and press Send")
        self._entry.set_hexpand(True)
        self._entry.connect("activate", self._on_send)
        compose.append(self._entry)

        self._send_button = Gtk.Button.new_with_label("Send")
        self._send_button.connect("clicked", self._on_send)
        compose.append(self._send_button)
        self._chat_panel.append(compose)

        self._send_status = Gtk.Label(label="")
        self._send_status.add_css_class("panel-muted")
        self._send_status.set_halign(Gtk.Align.START)
        self._send_status.set_margin_start(12)
        self._send_status.set_margin_bottom(4)
        self._chat_panel.append(self._send_status)

        self._reload_channels()
        self._refresh_compose_state()
        GLib.timeout_add(2000, self._poll_messages)

    def _poll_messages(self) -> bool:
        """Check for new messages/channels and refresh if changed."""
        channels = self._service.list_channels()
        if len(channels) != self._last_channel_count:
            self._reload_channels()
        else:
            messages = self._service.list_messages_for_channel(self._selected_channel_id, limit=100)
            new_count = len(messages)
            if new_count > self._last_message_count:
                # Append only the new messages instead of full rebuild
                new_messages = messages[self._last_message_count :]
                # Remove the "no messages" placeholder if it was showing
                if self._last_message_count == 0:
                    while True:
                        child = self._message_box.get_first_child()
                        if child is None:
                            break
                        self._message_box.remove(child)
                for message in new_messages:
                    msg_date = to_local(message.created_at).strftime("%Y-%m-%d")
                    if self._last_message_date and msg_date != self._last_message_date:
                        self._message_box.append(DaySeparator(msg_date))
                    self._last_message_date = msg_date
                    self._message_box.append(
                        MessageBubble(message, self._service, self._on_message_clicked)
                    )
                self._last_message_count = new_count
                GLib.idle_add(self._scroll_to_bottom)
            elif new_count < self._last_message_count:
                # Message count decreased (e.g. clear) - full rebuild
                self._reload_messages()
        self._refresh_compose_state()
        return True

    def _scroll_to_bottom(self) -> bool:
        adj = self._scroll.get_vadjustment()
        adj.set_value(adj.get_upper() - adj.get_page_size())
        return False  # GLib.SOURCE_REMOVE

    def _ensure_emoji_chooser(self, btn: Gtk.MenuButton, _pspec: object) -> None:
        """Lazily create the EmojiChooser on first activation."""
        if not btn.get_active() or btn.get_popover() is not None:
            return
        chooser = Gtk.EmojiChooser.new()
        chooser.connect("emoji-picked", self._on_emoji_picked)
        btn.set_popover(chooser)
        chooser.popup()

    def _on_emoji_picked(self, _chooser: Gtk.EmojiChooser, emoji: str) -> None:
        """Insert the chosen emoji at the cursor position in the entry."""
        pos = self._entry.get_position()
        self._entry.insert_text(emoji, pos)
        self._entry.set_position(pos + len(emoji))
        self._entry.grab_focus()

    def _refresh_compose_state(self) -> None:
        """Enable/disable compose area based on connection status."""
        connected = self._service.get_status().connected
        self._entry.set_sensitive(connected)
        self._send_button.set_sensitive(connected)
        self._emoji_btn.set_sensitive(connected)
        if connected:
            self._entry.set_placeholder_text("Type message and press Send")
        else:
            self._entry.set_placeholder_text("Connect to radio to send messages")

    def _reload_channels(self) -> None:
        while True:
            row = self._channel_list.get_row_at_index(0)
            if row is None:
                break
            self._channel_list.remove(row)

        channels = self._service.list_channels()
        self._last_channel_count = len(channels)
        if not channels:
            channels = [Channel(channel_id="public", display_name="#public")]

        selected_row: Gtk.ListBoxRow | None = None
        for index, channel in enumerate(channels):
            row = Gtk.ListBoxRow.new()
            row.add_css_class("side-list-row")
            setattr(row, "channel_id", channel.channel_id)

            body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            body.set_margin_top(8)
            body.set_margin_bottom(8)
            body.set_margin_start(10)
            body.set_margin_end(10)

            title = Gtk.Label(label=channel.display_name)
            title.set_halign(Gtk.Align.START)
            body.append(title)

            meta = Gtk.Label(label=f"{channel.unread_count} unread")
            meta.add_css_class("panel-muted")
            meta.set_halign(Gtk.Align.START)
            body.append(meta)

            row.set_child(body)

            # Right-click gesture for channel removal (not on #public)
            if channel.channel_id != "public":
                gesture = Gtk.GestureClick.new()
                gesture.set_button(Gdk.BUTTON_SECONDARY)
                gesture.connect("pressed", self._on_channel_right_click, row)
                row.add_controller(gesture)

            self._channel_list.append(row)

            if channel.channel_id == self._selected_channel_id:
                selected_row = row
            if selected_row is None and index == 0:
                selected_row = row

        if selected_row is not None:
            self._channel_list.select_row(selected_row)
            self._selected_channel_id = getattr(
                selected_row, "channel_id", self._selected_channel_id
            )

        self._reload_messages()

    def _reload_messages(self) -> None:
        # Clear existing messages
        while True:
            child = self._message_box.get_first_child()
            if child is None:
                break
            self._message_box.remove(child)

        # Hide details when reloading
        self._details_revealer.set_reveal_child(False)
        self._selected_message = None

        messages = self._service.list_messages_for_channel(self._selected_channel_id, limit=100)
        self._last_message_count = len(messages)

        if not messages:
            self._last_message_date = None
            self._message_box.append(EmptyState("No messages in this channel yet.", vexpand=True))
            return

        today = GLib.DateTime.new_now_local().format("%Y-%m-%d")
        prev_date: str | None = None
        for message in messages:
            msg_date = to_local(message.created_at).strftime("%Y-%m-%d")
            if msg_date != prev_date and not (prev_date is None and msg_date == today):
                self._message_box.append(DaySeparator(msg_date))
            prev_date = msg_date
            self._message_box.append(
                MessageBubble(message, self._service, self._on_message_clicked)
            )
        self._last_message_date = prev_date
        GLib.idle_add(self._scroll_to_bottom)

    def _on_message_clicked(self, _button: Gtk.Button, message: Message) -> None:
        """Show message details when clicked."""
        # Toggle if same message clicked again
        if self._selected_message and self._selected_message.message_id == message.message_id:
            self._details_revealer.set_reveal_child(False)
            self._selected_message = None
            return

        self._selected_message = message
        self._show_message_details(message)
        self._details_revealer.set_reveal_child(True)

    def _show_message_details(self, message: Message) -> None:
        """Populate the details panel for a message."""
        # Clear existing details
        while True:
            child = self._details_box.get_first_child()
            if child is None:
                break
            self._details_box.remove(child)

        # Separator
        sep = Gtk.Separator.new(Gtk.Orientation.HORIZONTAL)
        self._details_box.append(sep)

        # Header with sender and time
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        sender = Gtk.Label(label=message.sender_id)
        sender.add_css_class("message-detail-sender")
        sender.set_halign(Gtk.Align.START)
        sender.set_hexpand(True)
        header.append(sender)

        time_label = Gtk.Label(label=to_local(message.created_at).strftime("%I:%M %p"))
        time_label.add_css_class("panel-muted")
        header.append(time_label)
        self._details_box.append(header)

        # Message preview (with @mention markup)
        peers = self._service.list_peers()
        markup = parse_mentions(message.body, peers)
        preview = Gtk.Label()
        preview.set_markup(markup)
        preview.add_css_class("message-body")
        preview.add_css_class("panel-muted")
        preview.set_halign(Gtk.Align.START)
        preview.set_wrap(True)
        preview.set_max_width_chars(50)
        preview.set_ellipsize(Pango.EllipsizeMode.END)
        preview.set_lines(2)
        preview.connect("activate-link", self._on_mention_clicked)
        self._details_box.append(preview)

        # Details section
        details_header = Gtk.Label(label="Details")
        details_header.add_css_class("message-detail-header")
        details_header.set_halign(Gtk.Align.START)
        details_header.set_margin_top(8)
        self._details_box.append(details_header)

        # Route / Hops
        if message.is_outgoing:
            self._details_box.append(DetailRow("Hops:", "Sent"))
        elif message.path_hops:
            route_label = Gtk.Label(label="Route:")
            route_label.add_css_class("detail-label")
            route_label.set_halign(Gtk.Align.START)
            self._details_box.append(route_label)

            all_peers = self._service.list_peers()
            sender_name = message.sender_id
            sender_peer = find_peer_for_hop(all_peers, sender_name)
            sender_prefix = (sender_name or "??")[:2].upper()

            path = PathVisualization(
                hops=message.path_hops,
                peers=all_peers,
                arrow="\u2190",
                start=("Me", "You (this node)", None, STYLE_SELF),
                end=(sender_prefix, sender_name, sender_peer, STYLE_DEFAULT),
            )
            path.set_margin_top(4)
            self._details_box.append(path)
        elif message.path_len == 0:
            self._details_box.append(DetailRow("Hops:", "Direct"))
        else:
            hops_text = f"{message.path_len} hop{'s' if message.path_len != 1 else ''}"
            self._details_box.append(DetailRow("Hops:", hops_text))

        # Time
        time_label = "Sent:" if message.is_outgoing else "Received:"
        time_value = to_local(message.created_at).strftime("%b %d, %Y at %I:%M %p")
        self._details_box.append(DetailRow(time_label, time_value))

        # SNR (only for incoming)
        if not message.is_outgoing and message.snr is not None:
            quality = snr_to_quality(message.snr)
            self._details_box.append(DetailRow("SNR:", f"{message.snr:.1f} dB ({quality})"))

        # RSSI (only for incoming)
        if not message.is_outgoing and message.rssi is not None:
            self._details_box.append(DetailRow("RSSI:", f"{message.rssi} dBm"))

        # Close button
        close_btn = Gtk.Button.new_with_label("Close")
        close_btn.set_halign(Gtk.Align.END)
        close_btn.set_margin_top(8)
        close_btn.connect("clicked", self._on_close_details)
        self._details_box.append(close_btn)

    def _on_mention_clicked(self, _label: Gtk.Label, uri: str) -> bool:
        """Handle clicks on @mention links — navigate to the peer."""
        if not uri.startswith("mention:"):
            return False
        peer_id = uri[len("mention:") :]
        root = self.get_root()
        if root is None:
            return True
        stack = getattr(root, "_stack", None)
        if stack is None:
            return True
        stack.set_visible_child_name("peers")
        nav_buttons = getattr(root, "_nav_buttons", None)
        if nav_buttons:
            for name, btn in nav_buttons.items():
                btn.set_active(name == "peers")
        peers_widget = stack.get_child_by_name("peers")
        if peers_widget is not None:
            select_fn = getattr(peers_widget, "select_peer", None)
            if select_fn:
                select_fn(peer_id)
        return True

    def _on_close_details(self, _button: Gtk.Button) -> None:
        self._details_revealer.set_reveal_child(False)
        self._selected_message = None

    def _on_channel_selected(self, _listbox: Gtk.ListBox, row: Gtk.ListBoxRow | None) -> None:
        if row is None:
            return
        channel_id = getattr(row, "channel_id", None)
        if not channel_id:
            return
        self._selected_channel_id = channel_id
        self._update_thread_title(channel_id)
        self._reload_messages()

    def _on_channel_right_click(
        self,
        gesture: Gtk.GestureClick,
        _n_press: int,
        x: float,
        y: float,
        row: Gtk.ListBoxRow,
    ) -> None:
        """Show context menu on right-click for channel removal."""
        channel_id = getattr(row, "channel_id", None)
        if not channel_id or channel_id == "public":
            return

        # Build a popover menu
        menu = Gtk.PopoverMenu.new_from_model(None)
        action_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)

        remove_btn = Gtk.Button.new_with_label("Remove channel")
        remove_btn.add_css_class("flat")
        remove_btn.connect("clicked", self._on_remove_channel_clicked, channel_id, menu)
        action_box.append(remove_btn)

        popover = Gtk.Popover.new()
        popover.set_child(action_box)
        popover.set_parent(row)
        popover.set_position(Gtk.PositionType.RIGHT)
        popover.set_pointing_to(Gdk.Rectangle())
        popover.popup()

    def _on_remove_channel_clicked(
        self, _button: Gtk.Button, channel_id: str, popover: Gtk.Popover
    ) -> None:
        """Show confirmation dialog before removing a channel."""
        popover.popdown()

        display_name = self._get_channel_display_name(channel_id)
        dialog = Adw.AlertDialog.new(
            f"Remove {display_name}?",
            "This will remove the channel and all its messages. This cannot be undone.",
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("remove", "Remove")
        dialog.set_response_appearance("remove", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect("response", self._on_remove_confirmed, channel_id)
        dialog.present(self.get_root())

    def _on_remove_confirmed(
        self, _dialog: Adw.AlertDialog, response: str, channel_id: str
    ) -> None:
        """Handle confirmation dialog response."""
        if response != "remove":
            return
        if self._service.remove_channel(channel_id):
            # Switch to #public if we removed the currently selected channel
            if self._selected_channel_id == channel_id:
                self._selected_channel_id = "public"
            self._reload_channels()

    def _on_add_channel_clicked(self, _button: Gtk.Button) -> None:
        """Show a dialog to add a new hashtag channel by name."""
        dialog = Adw.AlertDialog.new("Add Channel", None)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("add", "Add")
        dialog.set_response_appearance("add", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("add")
        dialog.set_close_response("cancel")

        entry = Gtk.Entry.new()
        entry.set_placeholder_text("channel-name")

        prefix = Gtk.Label(label="#")
        prefix.add_css_class("panel-muted")

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        row.set_margin_start(12)
        row.set_margin_end(12)
        row.set_margin_top(8)
        row.set_margin_bottom(8)
        row.append(prefix)
        row.append(entry)
        entry.set_hexpand(True)
        entry.connect("activate", lambda _e: dialog.response("add"))

        dialog.set_extra_child(row)
        dialog.connect("response", self._on_add_channel_response, entry)
        dialog.present(self.get_root())
        entry.grab_focus()

    def _on_add_channel_response(
        self, _dialog: Adw.AlertDialog, response: str, entry: Gtk.Entry
    ) -> None:
        if response != "add":
            return
        raw = entry.get_text().strip().lstrip("#").strip()
        if not raw:
            return
        channel_id = raw.lower()
        display_name = f"#{channel_id}"
        self._service.ensure_channel(channel_id, display_name)
        self._selected_channel_id = channel_id
        self._reload_channels()

    def _get_channel_display_name(self, channel_id: str) -> str:
        """Return the display name for a channel, falling back to channel_id."""
        channels = self._service.list_channels()
        for ch in channels:
            if ch.channel_id == channel_id:
                return ch.display_name
        return channel_id

    def _update_thread_title(self, channel_id: str) -> None:
        self._thread_title.set_text(self._get_channel_display_name(channel_id))

    def _on_send(self, *_args: object) -> None:
        body = self._entry.get_text().strip()
        if not body:
            self._send_status.set_text("Enter a message before sending.")
            return
        try:
            self._service.send_message(peer_id=self._selected_channel_id, body=body)
        except Exception as exc:  # noqa: BLE001
            logger.error("Send failed: %s", exc)
            self._send_status.set_text(f"Send failed: {exc}")
            return

        display = self._get_channel_display_name(self._selected_channel_id)
        self._entry.set_text("")
        self._send_status.set_text(f"Sent to {display}")
        self._reload_messages()

    def select_channel(self, channel_id: str) -> None:
        """Select a channel programmatically (for direct messaging from peers view)."""
        # Ensure the channel exists in the service before trying to select it
        channel = self._service.ensure_channel(channel_id)

        # Use the normalized channel_id (lowercase for DMs)
        self._selected_channel_id = channel.channel_id

        # Reload channels to include the newly-ensured channel, then select it
        self._reload_channels()
        self._update_thread_title(channel.channel_id)
        self._reload_messages()
