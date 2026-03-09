"""Full-screen conflict display shown when pre-flight checks fail."""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import Gtk

if TYPE_CHECKING:
    from meshcore_console.platform.conflicts import ConflictReport


class ConflictScreen(Gtk.Box):
    """Prominent error screen listing hardware conflicts with action buttons."""

    def __init__(
        self,
        report: ConflictReport,
        *,
        on_retry: Callable[[], None],
        on_stop_service: Callable[[str], None] | None = None,
        on_settings: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        self.set_halign(Gtk.Align.CENTER)
        self.set_valign(Gtk.Align.CENTER)
        self.set_vexpand(True)
        self.set_hexpand(True)
        self.add_css_class("conflict-screen")

        # Icon
        icon = Gtk.Image.new_from_icon_name("dialog-error-symbolic")
        icon.set_pixel_size(64)
        icon.add_css_class("conflict-icon")
        self.append(icon)

        # Title
        title = Gtk.Label(label="Hardware Conflict Detected")
        title.add_css_class("conflict-title")
        self.append(title)

        # Subtitle
        n = len(report.conflicts)
        subtitle_text = (
            "A conflict is preventing the radio from starting."
            if n == 1
            else f"{n} conflicts are preventing the radio from starting."
        )
        subtitle = Gtk.Label(label=subtitle_text)
        subtitle.add_css_class("conflict-subtitle")
        self.append(subtitle)

        # Conflict detail cards
        details_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        details_box.set_margin_top(8)
        details_box.set_size_request(500, -1)
        for conflict in report.conflicts:
            card = self._build_conflict_card(conflict)
            details_box.append(card)
        self.append(details_box)

        # Action buttons
        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        btn_box.set_halign(Gtk.Align.CENTER)
        btn_box.set_margin_top(16)

        if report.has_service_conflict and on_stop_service is not None:
            for svc in report.service_names:
                stop_btn = Gtk.Button.new_with_label(f"Stop {svc}")
                stop_btn.add_css_class("destructive-action")
                stop_btn.connect("clicked", lambda _b, s=svc: on_stop_service(s))
                btn_box.append(stop_btn)

        retry_btn = Gtk.Button.new_with_label("Retry Connection")
        retry_btn.add_css_class("suggested-action")
        retry_btn.connect("clicked", lambda _b: on_retry())
        btn_box.append(retry_btn)

        if on_settings is not None:
            settings_btn = Gtk.Button.new_with_label("Settings")
            settings_btn.connect("clicked", lambda _b: on_settings())
            btn_box.append(settings_btn)

        self.append(btn_box)

        # Manual command hint
        remediation_cmds = [c.remediation for c in report.conflicts if c.remediation]
        if remediation_cmds:
            cmd_label = Gtk.Label(label="Manual fix:\n" + "\n".join(remediation_cmds))
            cmd_label.add_css_class("conflict-command")
            cmd_label.set_selectable(True)
            cmd_label.set_wrap(True)
            cmd_label.set_margin_top(12)
            self.append(cmd_label)

    @staticmethod
    def _build_conflict_card(conflict: object) -> Gtk.Box:
        from meshcore_console.platform.conflicts import Conflict

        c: Conflict = conflict  # type: ignore[assignment]
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        card.add_css_class("conflict-detail-row")

        summary = Gtk.Label(label=c.summary)
        summary.set_halign(Gtk.Align.START)
        summary.add_css_class("conflict-detail-summary")
        card.append(summary)

        detail = Gtk.Label(label=c.detail)
        detail.set_halign(Gtk.Align.START)
        detail.set_wrap(True)
        detail.add_css_class("conflict-detail-text")
        card.append(detail)

        return card
