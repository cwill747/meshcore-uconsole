from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import GLib, Gtk

from meshcore_console.core.models import Channel, Message
from meshcore_console.core.radio import snr_to_quality
from meshcore_console.core.services import MeshcoreService
from meshcore_console.ui_gtk.widgets import DetailRow


class MessagesView(Gtk.Box):
    def __init__(self, service: MeshcoreService) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self._service = service
        self._selected_channel_id = "public"
        self._last_message_count = 0
        self._last_channel_count = 0
        self._selected_message: Message | None = None

        # Main content area (channels + chat)
        split = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        split.set_vexpand(True)
        self.append(split)

        self._channel_list = Gtk.ListBox.new()
        self._channel_list.add_css_class("panel-card")
        self._channel_list.add_css_class("channel-list")
        self._channel_list.add_css_class("side-list")
        self._channel_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self._channel_list.connect("row-selected", self._on_channel_selected)
        self._channel_list.set_size_request(180, -1)
        split.append(self._channel_list)

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
        scroll = Gtk.ScrolledWindow.new()
        scroll.set_vexpand(True)
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self._chat_panel.append(scroll)

        self._message_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self._message_box.set_margin_start(12)
        self._message_box.set_margin_end(12)
        self._message_box.set_margin_top(8)
        self._message_box.set_margin_bottom(8)
        scroll.set_child(self._message_box)

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
                    bubble = self._create_message_bubble(message)
                    self._message_box.append(bubble)
                self._last_message_count = new_count
            elif new_count < self._last_message_count:
                # Message count decreased (e.g. clear) - full rebuild
                self._reload_messages()
        self._refresh_compose_state()
        return True

    def _refresh_compose_state(self) -> None:
        """Enable/disable compose area based on connection status."""
        connected = self._service.get_status().connected
        self._entry.set_sensitive(connected)
        self._send_button.set_sensitive(connected)
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
            empty = Gtk.Label(label="No messages in this channel yet.")
            empty.add_css_class("panel-muted")
            empty.set_halign(Gtk.Align.CENTER)
            empty.set_valign(Gtk.Align.CENTER)
            empty.set_vexpand(True)
            self._message_box.append(empty)
            return

        for message in messages:
            bubble = self._create_message_bubble(message)
            self._message_box.append(bubble)

    def _create_message_bubble(self, message: Message) -> Gtk.Box:
        """Create an iMessage-style bubble for a message."""
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        row.set_margin_top(2)
        row.set_margin_bottom(2)

        # Create the bubble
        bubble = Gtk.Button.new()
        bubble.add_css_class("message-bubble")
        bubble.set_can_focus(False)

        if message.is_outgoing:
            bubble.add_css_class("message-outgoing")
            row.set_halign(Gtk.Align.END)
        else:
            bubble.add_css_class("message-incoming")
            row.set_halign(Gtk.Align.START)

        # Bubble content
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        content.set_margin_start(10)
        content.set_margin_end(10)
        content.set_margin_top(6)
        content.set_margin_bottom(6)

        # Message body
        body_label = Gtk.Label(label=message.body)
        body_label.set_wrap(True)
        body_label.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        body_label.set_max_width_chars(40)
        body_label.set_xalign(0)
        body_label.add_css_class("message-body")
        content.append(body_label)

        # Meta line (sender for incoming, time for all)
        if message.is_outgoing:
            meta_text = message.created_at.strftime("%H:%M")
        else:
            meta_text = f"{message.sender_id}  {message.created_at.strftime('%H:%M')}"
        meta = Gtk.Label(label=meta_text)
        meta.add_css_class("message-meta")
        meta.set_xalign(1 if message.is_outgoing else 0)
        content.append(meta)

        bubble.set_child(content)
        bubble.connect("clicked", self._on_message_clicked, message)
        row.append(bubble)

        return row

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

        time_label = Gtk.Label(label=message.created_at.strftime("%I:%M %p"))
        time_label.add_css_class("panel-muted")
        header.append(time_label)
        self._details_box.append(header)

        # Message preview
        preview = Gtk.Label(label=message.body[:100])
        preview.add_css_class("panel-muted")
        preview.set_halign(Gtk.Align.START)
        preview.set_wrap(True)
        preview.set_max_width_chars(50)
        self._details_box.append(preview)

        # Details section
        details_header = Gtk.Label(label="Details")
        details_header.add_css_class("message-detail-header")
        details_header.set_halign(Gtk.Align.START)
        details_header.set_margin_top(8)
        self._details_box.append(details_header)

        # Hops
        if message.is_outgoing:
            hops_text = "Sent"
        elif message.path_len == 0:
            hops_text = "Direct"
        else:
            hops_text = f"{message.path_len} hop{'s' if message.path_len != 1 else ''}"
        self._details_box.append(DetailRow("Hops:", hops_text))

        # Time
        time_label = "Sent:" if message.is_outgoing else "Received:"
        time_value = message.created_at.strftime("%b %d, %Y at %I:%M %p")
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
            self._send_status.set_text(f"Send failed: {exc}")
            return

        display = self._get_channel_display_name(self._selected_channel_id)
        self._entry.set_text("")
        self._send_status.set_text(f"Sent to {display}")
        self._reload_messages()

    def select_channel(self, channel_id: str) -> None:
        """Select a channel programmatically (for direct messaging from peers view)."""
        # Ensure the channel exists in the service before trying to select it
        self._service.ensure_channel(channel_id)

        self._selected_channel_id = channel_id

        # Reload channels to include the newly-ensured channel, then select it
        self._reload_channels()
        self._update_thread_title(channel_id)
        self._reload_messages()
