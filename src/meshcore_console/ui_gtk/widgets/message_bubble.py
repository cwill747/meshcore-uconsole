"""Reusable iMessage-style chat message bubble."""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import Gtk, Pango

from meshcore_console.core.models import Message
from meshcore_console.core.time import to_local
from meshcore_console.ui_gtk.widgets.mention import parse_mentions
from meshcore_console.ui_gtk.widgets.node_badge import STYLE_SELF, NodeBadge

if TYPE_CHECKING:
    from meshcore_console.core.services import MeshcoreService


class MessageBubble(Gtk.Box):
    """Chat message bubble with sender badge and directional alignment.

    Incoming messages align left with the badge on the left.
    Outgoing messages align right with the badge on the right.

    Usage::

        bubble = MessageBubble(message, service, on_clicked=handler)
        message_box.append(bubble)
    """

    def __init__(
        self,
        message: Message,
        service: MeshcoreService,
        on_clicked: Callable[[Gtk.Button, Message], None],
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.set_margin_top(2)
        self.set_margin_bottom(2)

        # Sender badge
        badge = self._make_sender_badge(message, service)
        badge.set_valign(Gtk.Align.END)

        # Create the bubble button
        bubble = Gtk.Button.new()
        bubble.add_css_class("message-bubble")
        bubble.set_can_focus(False)

        if message.is_outgoing:
            bubble.add_css_class("message-outgoing")
            self.set_halign(Gtk.Align.END)
            self.append(bubble)
            self.append(badge)
        else:
            bubble.add_css_class("message-incoming")
            self.set_halign(Gtk.Align.START)
            self.append(badge)
            self.append(bubble)

        # Bubble content
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        content.set_margin_start(10)
        content.set_margin_end(10)
        content.set_margin_top(6)
        content.set_margin_bottom(6)

        # Message body (with @mention markup)
        peers = service.list_peers()
        markup = parse_mentions(message.body, peers)
        body_label = Gtk.Label()
        body_label.set_markup(markup)
        body_label.set_wrap(True)
        body_label.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        body_label.set_max_width_chars(50)
        body_label.set_xalign(0)
        body_label.add_css_class("message-body")
        body_label.connect("activate-link", self._on_mention_clicked)
        content.append(body_label)

        # Meta line (sender for incoming, time for all)
        local_time = to_local(message.created_at).strftime("%H:%M")
        if message.is_outgoing:
            meta_text = local_time
        else:
            path_str = ",".join(message.path_hops) if message.path_hops else ""
            if path_str:
                meta_text = f"{message.sender_id}  {local_time}  {path_str}"
            else:
                meta_text = f"{message.sender_id}  {local_time}"
        meta = Gtk.Label(label=meta_text)
        meta.add_css_class("message-meta")
        meta.set_xalign(1 if message.is_outgoing else 0)
        meta.set_wrap(True)
        meta.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        meta.set_max_width_chars(50)
        content.append(meta)

        bubble.set_child(content)
        bubble.connect("clicked", on_clicked, message)

    def _on_mention_clicked(self, label: Gtk.Label, uri: str) -> bool:
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

        return True  # Prevent default link handler

    @staticmethod
    def _make_sender_badge(message: Message, service: MeshcoreService) -> NodeBadge:
        """Create a NodeBadge for the message sender."""
        from meshcore_console.ui_gtk.widgets.node_badge import find_peer_for_hop

        if message.is_outgoing:
            self_key = service.get_self_public_key()
            prefix = (self_key or "")[:2].upper() or "Me"
            return NodeBadge(prefix, "You", style=STYLE_SELF)

        peers = service.list_peers()
        sender_peer = find_peer_for_hop(peers, message.sender_id)
        if sender_peer and sender_peer.public_key:
            prefix = sender_peer.public_key[:2].upper()
        else:
            prefix = message.sender_id[:2].upper()
        return NodeBadge(prefix, message.sender_id, peer=sender_peer)
