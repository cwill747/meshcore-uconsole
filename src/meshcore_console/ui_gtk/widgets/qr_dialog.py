"""QR code dialog for sharing node identity."""

from __future__ import annotations

import io
from typing import TYPE_CHECKING

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("GdkPixbuf", "2.0")

from gi.repository import Gdk, GdkPixbuf, Gtk

if TYPE_CHECKING:
    pass


def _generate_qr_pixbuf(data: str, scale: int = 8) -> GdkPixbuf.Pixbuf | None:
    """Generate a QR code as a GdkPixbuf."""
    try:
        import segno

        qr = segno.make(data, error="L")
        buffer = io.BytesIO()
        # Use simple black/white colors that don't require extra dependencies
        qr.save(buffer, kind="png", scale=scale, border=2)
        buffer.seek(0)
        png_data = buffer.read()

        loader = GdkPixbuf.PixbufLoader.new_with_type("png")
        loader.write(png_data)
        loader.close()
        return loader.get_pixbuf()
    except ImportError as e:
        print(f"[QR] Import error: {e}")
        return None
    except Exception as e:
        print(f"[QR] Generation error: {e}")
        return None


def _get_initials(name: str) -> str:
    """Get initials from a name (up to 2 characters)."""
    parts = name.split()
    if len(parts) >= 2:
        return (parts[0][0] + parts[1][0]).upper()
    if name:
        return name[:2].upper()
    return "??"


def _abbreviate_key(key: str) -> str:
    """Abbreviate a public key for display (e.g., 6b547fd1...3df0a619)."""
    if len(key) <= 16:
        return key
    return f"{key[:8]}...{key[-8:]}"


class QrCodeDialog(Gtk.Dialog):
    """Dialog showing node identity QR code."""

    def __init__(
        self,
        parent: Gtk.Window | None,
        node_name: str,
        public_key: str | None,
    ) -> None:
        super().__init__(
            title="Share Node",
            transient_for=parent,
            modal=True,
        )
        self.set_default_size(320, 480)

        content = self.get_content_area()
        content.set_orientation(Gtk.Orientation.VERTICAL)
        content.set_spacing(16)
        content.set_margin_top(24)
        content.set_margin_bottom(24)
        content.set_margin_start(24)
        content.set_margin_end(24)

        # Avatar circle with initials
        avatar = Gtk.DrawingArea()
        avatar.set_size_request(64, 64)
        avatar.set_halign(Gtk.Align.CENTER)
        initials = _get_initials(node_name)
        avatar.set_draw_func(self._draw_avatar, initials)
        content.append(avatar)

        # Node name
        name_label = Gtk.Label(label=node_name)
        name_label.add_css_class("title-2")
        name_label.set_halign(Gtk.Align.CENTER)
        content.append(name_label)

        # Abbreviated public key
        if public_key:
            key_label = Gtk.Label(label=f"<{_abbreviate_key(public_key)}>")
            key_label.add_css_class("panel-muted")
            key_label.set_halign(Gtk.Align.CENTER)
            content.append(key_label)

        # QR code
        qr_data = self._build_qr_data(node_name, public_key)
        pixbuf = _generate_qr_pixbuf(qr_data, scale=6)
        if pixbuf:
            texture = Gdk.Texture.new_for_pixbuf(pixbuf)
            qr_image = Gtk.Picture.new_for_paintable(texture)
            qr_image.set_size_request(200, 200)
            qr_image.set_halign(Gtk.Align.CENTER)
            content.append(qr_image)
        else:
            error_label = Gtk.Label(label="QR code generation unavailable")
            error_label.add_css_class("panel-muted")
            error_label.set_halign(Gtk.Align.CENTER)
            content.append(error_label)

        # Instructions
        instructions = Gtk.Label(
            label="Scan QR code to add this contact.\nMenu \u2192 Add Contact \u2192 Scan QR Code"
        )
        instructions.add_css_class("panel-muted")
        instructions.set_halign(Gtk.Align.CENTER)
        instructions.set_justify(Gtk.Justification.CENTER)
        instructions.set_margin_top(16)
        content.append(instructions)

        # Close button
        self.add_button("Close", Gtk.ResponseType.CLOSE)
        self.connect("response", lambda d, r: d.destroy())

    def _build_qr_data(self, node_name: str, public_key: str | None) -> str:
        """Build the QR code data string."""
        # Format: meshcore://contact?name=NAME&key=PUBKEY
        if public_key:
            return f"meshcore://contact?name={node_name}&key={public_key}"
        return f"meshcore://contact?name={node_name}"

    def _draw_avatar(
        self,
        area: Gtk.DrawingArea,
        cr: object,  # cairo.Context
        width: int,
        height: int,
        initials: str,
    ) -> None:
        """Draw the avatar circle with initials."""
        import cairo

        ctx = cr  # type: cairo.Context

        # Draw circle
        radius = min(width, height) / 2
        cx, cy = width / 2, height / 2
        ctx.arc(cx, cy, radius, 0, 2 * 3.14159)
        ctx.set_source_rgb(0.21, 0.76, 0.61)  # mc_accent color
        ctx.fill()

        # Draw initials
        ctx.set_source_rgb(1, 1, 1)
        ctx.select_font_face("sans-serif", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
        ctx.set_font_size(radius * 0.8)

        extents = ctx.text_extents(initials)
        x = cx - extents.width / 2 - extents.x_bearing
        y = cy - extents.height / 2 - extents.y_bearing
        ctx.move_to(x, y)
        ctx.show_text(initials)
