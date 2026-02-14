from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import Gio, Gtk

from meshcore_console.core.services import MeshcoreService
from meshcore_console.meshcore.logging_setup import (
    VALID_LEVELS,
    export_logs_to_path,
    set_stderr_level,
)
from meshcore_console.meshcore.settings import MeshcoreSettings, apply_preset
from meshcore_console.ui_gtk.widgets.qr_dialog import QrCodeDialog


def _abbreviate_key(key: str | None) -> str:
    """Abbreviate a public key for display."""
    if not key:
        return "Not available"
    if len(key) <= 16:
        return key
    return f"{key[:8]}...{key[-8:]}"


class SettingsView(Gtk.Box):
    """Settings view matching official MeshCore app layout."""

    def __init__(self, service: MeshcoreService) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self._service = service
        self._status_label = Gtk.Label(label="")
        self._status_label.add_css_class("panel-muted")
        self._status_label.set_halign(Gtk.Align.START)
        self._entries: dict[str, Gtk.Entry] = {}
        self._switches: dict[str, Gtk.Switch] = {}
        self._public_key_label: Gtk.Label | None = None

        scroll = Gtk.ScrolledWindow.new()
        scroll.set_vexpand(True)
        scroll.set_hexpand(True)
        self.append(scroll)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        content.set_margin_start(8)
        content.set_margin_end(8)
        content.set_margin_top(8)
        scroll.set_child(content)

        # Two-column layout
        columns = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
        columns.set_homogeneous(True)
        content.append(columns)

        # Left column: Public Info + Radio
        left_col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        left_col.append(self._build_public_info_panel())
        left_col.append(self._build_radio_panel())
        columns.append(left_col)

        # Right column: Hardware (advanced) + Logging
        right_col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        right_col.append(self._build_hardware_panel())
        right_col.append(self._build_logging_panel())
        columns.append(right_col)

        # Actions bar at bottom
        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        actions.add_css_class("panel-card")
        save = Gtk.Button.new_with_label("Save Settings")
        save.connect("clicked", self._on_save)
        actions.append(save)

        reload_btn = Gtk.Button.new_with_label("Reload")
        reload_btn.connect("clicked", self._on_reload)
        actions.append(reload_btn)
        actions.append(self._status_label)
        content.append(actions)

        self._load_from_service()

    def _build_public_info_panel(self) -> Gtk.Box:
        panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        panel.add_css_class("panel-card")

        title = Gtk.Label(label="Public Info")
        title.add_css_class("panel-title")
        title.set_halign(Gtk.Align.START)
        panel.append(title)

        grid = Gtk.Grid()
        grid.set_row_spacing(8)
        grid.set_column_spacing(8)

        # Row 0: Name
        grid.attach(self._grid_label("Name"), 0, 0, 1, 1)
        grid.attach(self._grid_entry("node_name", 18), 1, 0, 2, 1)

        # Row 1: Public Key (display only)
        grid.attach(self._grid_label("Public Key"), 0, 1, 1, 1)
        self._public_key_label = Gtk.Label(label="Loading...")
        self._public_key_label.add_css_class("panel-muted")
        self._public_key_label.set_halign(Gtk.Align.START)
        self._public_key_label.set_selectable(True)
        grid.attach(self._public_key_label, 1, 1, 1, 1)

        qr_btn = Gtk.Button.new_with_label("QR")
        qr_btn.set_tooltip_text("Show QR code")
        qr_btn.connect("clicked", self._on_show_qr)
        grid.attach(qr_btn, 2, 1, 1, 1)

        # Row 2: Latitude / Longitude
        grid.attach(self._grid_label("Latitude"), 0, 2, 1, 1)
        grid.attach(self._grid_entry("latitude", 10), 1, 2, 1, 1)
        grid.attach(self._grid_label("Longitude"), 0, 3, 1, 1)
        grid.attach(self._grid_entry("longitude", 10), 1, 3, 1, 1)

        # Row 4: Share Position in Advert
        grid.attach(self._grid_label("Share GPS Position"), 0, 4, 1, 1)
        grid.attach(self._grid_switch("share_position"), 1, 4, 1, 1)

        # Row 5: Allow Telemetry
        grid.attach(self._grid_label("Allow Telemetry"), 0, 5, 1, 1)
        grid.attach(self._grid_switch("allow_telemetry"), 1, 5, 1, 1)

        # Row 6: Autoconnect
        grid.attach(self._grid_label("Autoconnect"), 0, 6, 1, 1)
        grid.attach(self._grid_switch("autoconnect"), 1, 6, 1, 1)

        panel.append(grid)
        return panel

    def _build_radio_panel(self) -> Gtk.Box:
        panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        panel.add_css_class("panel-card")

        title = Gtk.Label(label="Radio Settings")
        title.add_css_class("panel-title")
        title.set_halign(Gtk.Align.START)
        panel.append(title)

        grid = Gtk.Grid()
        grid.set_row_spacing(8)
        grid.set_column_spacing(8)

        # Row 0: Preset
        grid.attach(self._grid_label("Preset"), 0, 0, 1, 1)
        self._preset = Gtk.ComboBoxText.new()
        self._preset.append("meshcore-us", "USA/Canada")
        self._preset.append("meshcore-eu", "Europe")
        self._preset.append("custom", "Custom")
        self._preset.set_active_id("meshcore-us")
        self._preset.connect("changed", self._on_preset_changed)
        grid.attach(self._preset, 1, 0, 3, 1)

        # Row 1: Frequency (MHz)
        grid.attach(self._grid_label("Frequency"), 0, 1, 1, 1)
        grid.attach(self._grid_entry("frequency", 10), 1, 1, 1, 1)
        mhz_label = Gtk.Label(label="MHz")
        mhz_label.add_css_class("panel-muted")
        mhz_label.set_halign(Gtk.Align.START)
        grid.attach(mhz_label, 2, 1, 1, 1)

        # Row 2: Bandwidth (kHz)
        grid.attach(self._grid_label("Bandwidth"), 0, 2, 1, 1)
        grid.attach(self._grid_entry("bandwidth", 8), 1, 2, 1, 1)
        khz_label = Gtk.Label(label="kHz")
        khz_label.add_css_class("panel-muted")
        khz_label.set_halign(Gtk.Align.START)
        grid.attach(khz_label, 2, 2, 1, 1)

        # Row 3: Spreading Factor
        grid.attach(self._grid_label("Spreading Factor"), 0, 3, 1, 1)
        grid.attach(self._grid_entry("spreading_factor", 4), 1, 3, 1, 1)

        # Row 4: Coding Rate
        grid.attach(self._grid_label("Coding Rate"), 0, 4, 1, 1)
        grid.attach(self._grid_entry("coding_rate", 4), 1, 4, 1, 1)

        # Row 5: TX Power
        grid.attach(self._grid_label("TX Power"), 0, 5, 1, 1)
        grid.attach(self._grid_entry("tx_power", 4), 1, 5, 1, 1)
        dbm_label = Gtk.Label(label="dBm")
        dbm_label.add_css_class("panel-muted")
        dbm_label.set_halign(Gtk.Align.START)
        grid.attach(dbm_label, 2, 5, 1, 1)

        # Row 6: Preamble Length
        grid.attach(self._grid_label("Preamble"), 0, 6, 1, 1)
        grid.attach(self._grid_entry("preamble_length", 4), 1, 6, 1, 1)

        panel.append(grid)
        return panel

    def _build_hardware_panel(self) -> Gtk.Box:
        panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        panel.add_css_class("panel-card")
        panel.set_valign(Gtk.Align.START)

        title = Gtk.Label(label="Hardware")
        title.add_css_class("panel-title")
        title.set_halign(Gtk.Align.START)
        panel.append(title)

        subtitle = Gtk.Label(label="SPI and GPIO pin configuration")
        subtitle.add_css_class("panel-muted")
        subtitle.set_halign(Gtk.Align.START)
        panel.append(subtitle)

        grid = Gtk.Grid()
        grid.set_row_spacing(8)
        grid.set_column_spacing(8)

        # SPI Bus row
        grid.attach(self._grid_label("SPI Bus"), 0, 0, 1, 1)
        grid.attach(self._grid_entry("bus_id", 3), 1, 0, 1, 1)
        grid.attach(self._grid_label("CS ID"), 2, 0, 1, 1)
        grid.attach(self._grid_entry("cs_id", 3), 3, 0, 1, 1)
        grid.attach(self._grid_label("CS Pin"), 4, 0, 1, 1)
        grid.attach(self._grid_entry("cs_pin", 3), 5, 0, 1, 1)

        # GPIO Pins row 1
        grid.attach(self._grid_label("Reset"), 0, 1, 1, 1)
        grid.attach(self._grid_entry("reset_pin", 3), 1, 1, 1, 1)
        grid.attach(self._grid_label("Busy"), 2, 1, 1, 1)
        grid.attach(self._grid_entry("busy_pin", 3), 3, 1, 1, 1)
        grid.attach(self._grid_label("IRQ"), 4, 1, 1, 1)
        grid.attach(self._grid_entry("irq_pin", 3), 5, 1, 1, 1)

        # GPIO Pins row 2
        grid.attach(self._grid_label("TXEN"), 0, 2, 1, 1)
        grid.attach(self._grid_entry("txen_pin", 3), 1, 2, 1, 1)
        grid.attach(self._grid_label("RXEN"), 2, 2, 1, 1)
        grid.attach(self._grid_entry("rxen_pin", 3), 3, 2, 1, 1)

        # Mode switches row
        grid.attach(self._grid_label("Waveshare"), 0, 3, 1, 1)
        grid.attach(self._grid_switch("is_waveshare"), 1, 3, 1, 1)
        grid.attach(self._grid_label("DIO2 RF"), 2, 3, 1, 1)
        grid.attach(self._grid_switch("use_dio2_rf"), 3, 3, 1, 1)
        grid.attach(self._grid_label("DIO3 TCXO"), 4, 3, 1, 1)
        grid.attach(self._grid_switch("use_dio3_tcxo"), 5, 3, 1, 1)

        panel.append(grid)
        return panel

    def _build_logging_panel(self) -> Gtk.Box:
        panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        panel.add_css_class("panel-card")
        panel.set_valign(Gtk.Align.START)

        title = Gtk.Label(label="Logging")
        title.add_css_class("panel-title")
        title.set_halign(Gtk.Align.START)
        panel.append(title)

        grid = Gtk.Grid()
        grid.set_row_spacing(8)
        grid.set_column_spacing(8)

        # Console log level dropdown
        grid.attach(self._grid_label("Console Level"), 0, 0, 1, 1)
        self._log_level_combo = Gtk.ComboBoxText.new()
        for level in VALID_LEVELS:
            self._log_level_combo.append(level, level)
        self._log_level_combo.set_active_id("INFO")
        self._log_level_combo.connect("changed", self._on_log_level_changed)
        grid.attach(self._log_level_combo, 1, 0, 1, 1)

        # Export logs button
        export_btn = Gtk.Button.new_with_label("Export Logs")
        export_btn.connect("clicked", self._on_export_logs)
        grid.attach(export_btn, 0, 1, 2, 1)

        panel.append(grid)
        return panel

    def _on_log_level_changed(self, combo: Gtk.ComboBoxText) -> None:
        level = combo.get_active_id()
        if level:
            set_stderr_level(level)

    def _on_export_logs(self, _button: Gtk.Button) -> None:
        from datetime import datetime, timezone

        ts = datetime.now(tz=timezone.utc).strftime("%Y%m%d-%H%M%S")
        dialog = Gtk.FileDialog()
        dialog.set_title("Export Logs")
        dialog.set_initial_name(f"meshcore-logs-{ts}.txt")
        txt_filter = Gtk.FileFilter()
        txt_filter.set_name("Text files")
        txt_filter.add_pattern("*.txt")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(txt_filter)
        dialog.set_filters(filters)

        window = self.get_root()
        parent = window if isinstance(window, Gtk.Window) else None
        dialog.save(parent, None, self._on_export_save_done)

    def _on_export_save_done(self, dialog: Gtk.FileDialog, result: Gio.AsyncResult) -> None:
        try:
            gfile = dialog.save_finish(result)
        except Exception:  # noqa: BLE001
            return  # user cancelled
        dest = gfile.get_path()
        if not dest:
            return
        try:
            export_logs_to_path(dest)
            self._status_label.set_text(f"Logs exported to {dest}")
        except Exception as exc:  # noqa: BLE001
            self._status_label.set_text(f"Export failed: {exc}")

    def _grid_label(self, text: str) -> Gtk.Label:
        label = Gtk.Label(label=text)
        label.add_css_class("panel-muted")
        label.set_halign(Gtk.Align.END)
        label.set_valign(Gtk.Align.CENTER)
        return label

    def _grid_entry(self, key: str, width_chars: int) -> Gtk.Entry:
        entry = Gtk.Entry.new()
        entry.set_width_chars(width_chars)
        entry.set_max_width_chars(width_chars)
        entry.set_halign(Gtk.Align.START)
        self._entries[key] = entry
        return entry

    def _grid_switch(self, key: str) -> Gtk.Switch:
        switch = Gtk.Switch.new()
        switch.set_halign(Gtk.Align.START)
        switch.set_valign(Gtk.Align.CENTER)
        self._switches[key] = switch
        return switch

    def refresh_public_key(self) -> None:
        """Re-read the public key from the service and update the display."""
        public_key = self._service.get_self_public_key()
        if self._public_key_label:
            self._public_key_label.set_text(_abbreviate_key(public_key))

    def _on_show_qr(self, _button: Gtk.Button) -> None:
        """Show QR code dialog."""
        node_name = self._entries["node_name"].get_text().strip()
        if not node_name:
            node_name = self._service.get_settings().node_name
        public_key = self._service.get_self_public_key()

        window = self.get_root()
        parent = window if isinstance(window, Gtk.Window) else None
        dialog = QrCodeDialog(parent, node_name, public_key)
        dialog.present()

    def _on_preset_changed(self, _combo: Gtk.ComboBoxText) -> None:
        preset = self._preset.get_active_id() or "custom"
        if preset == "custom":
            return
        current = self._collect_settings(allow_partial=True)
        updated = apply_preset(current, preset)
        self._set_entry_float("frequency", updated.frequency / 1_000_000)
        self._set_entry_float("bandwidth", updated.bandwidth / 1_000)
        self._set_entry_int("spreading_factor", updated.spreading_factor)
        self._set_entry_int("coding_rate", updated.coding_rate)
        self._set_entry_int("preamble_length", updated.preamble_length)
        self._set_entry_int("tx_power", updated.tx_power)

    def _on_reload(self, _button: Gtk.Button) -> None:
        self._load_from_service()
        self._status_label.set_text("Reloaded from persisted settings.")

    def _on_save(self, _button: Gtk.Button) -> None:
        try:
            settings = self._collect_settings()
            self._service.update_settings(settings)
            self._status_label.set_text("Settings saved.")
        except ValueError as exc:
            self._status_label.set_text(f"Invalid: {exc}")
        except Exception as exc:  # noqa: BLE001
            msg = str(exc) or type(exc).__name__
            self._status_label.set_text(f"Save failed: {msg}")

    def _load_from_service(self) -> None:
        settings = self._service.get_settings()

        # Public Info
        self._set_entry("node_name", settings.node_name)
        self._set_entry_float("latitude", settings.latitude)
        self._set_entry_float("longitude", settings.longitude)
        self._set_switch("share_position", settings.share_position)
        self._set_switch("allow_telemetry", settings.allow_telemetry)
        self._set_switch("autoconnect", settings.autoconnect)

        # Update public key display
        public_key = self._service.get_self_public_key()
        if self._public_key_label:
            self._public_key_label.set_text(_abbreviate_key(public_key))

        # Radio
        self._preset.set_active_id(settings.radio_preset)
        self._set_entry_float("frequency", settings.frequency / 1_000_000)
        self._set_entry_float("bandwidth", settings.bandwidth / 1_000)
        self._set_entry_int("spreading_factor", settings.spreading_factor)
        self._set_entry_int("coding_rate", settings.coding_rate)
        self._set_entry_int("tx_power", settings.tx_power)
        self._set_entry_int("preamble_length", settings.preamble_length)

        # Hardware
        for key in (
            "bus_id",
            "cs_id",
            "cs_pin",
            "reset_pin",
            "busy_pin",
            "irq_pin",
            "txen_pin",
            "rxen_pin",
        ):
            self._set_entry_int(key, getattr(settings, key))
        self._set_switch("is_waveshare", settings.is_waveshare)
        self._set_switch("use_dio2_rf", settings.use_dio2_rf)
        self._set_switch("use_dio3_tcxo", settings.use_dio3_tcxo)

        # Logging
        self._log_level_combo.set_active_id(settings.log_level)

    def _collect_settings(self, allow_partial: bool = False) -> MeshcoreSettings:
        current = self._service.get_settings()
        out = current.clone()

        # Public Info
        out.node_name = self._entries["node_name"].get_text().strip() or current.node_name
        out.latitude = self._parse_float("latitude", allow_partial) or current.latitude
        out.longitude = self._parse_float("longitude", allow_partial) or current.longitude
        out.share_position = self._switches["share_position"].get_active()
        out.allow_telemetry = self._switches["allow_telemetry"].get_active()
        out.autoconnect = self._switches["autoconnect"].get_active()

        # Radio
        out.radio_preset = self._preset.get_active_id() or "custom"
        freq = self._parse_float("frequency", allow_partial)
        if freq is not None:
            out.frequency = int(freq * 1_000_000)
        bw = self._parse_float("bandwidth", allow_partial)
        if bw is not None:
            out.bandwidth = int(bw * 1_000)

        for key in (
            "spreading_factor",
            "coding_rate",
            "tx_power",
            "preamble_length",
            "bus_id",
            "cs_id",
            "cs_pin",
            "reset_pin",
            "busy_pin",
            "irq_pin",
            "txen_pin",
            "rxen_pin",
        ):
            val = self._parse_int(key, allow_partial)
            if val is not None:
                setattr(out, key, val)

        # Hardware switches
        out.is_waveshare = self._switches["is_waveshare"].get_active()
        out.use_dio2_rf = self._switches["use_dio2_rf"].get_active()
        out.use_dio3_tcxo = self._switches["use_dio3_tcxo"].get_active()

        # Logging
        out.log_level = self._log_level_combo.get_active_id() or "INFO"

        return out

    def _parse_float(self, key: str, allow_partial: bool) -> float | None:
        text = self._entries[key].get_text().strip()
        if not text:
            if allow_partial:
                return None
            raise ValueError(f"{key} cannot be empty")
        try:
            return float(text)
        except ValueError as exc:
            raise ValueError(f"{key} must be a number") from exc

    def _parse_int(self, key: str, allow_partial: bool) -> int | None:
        text = self._entries[key].get_text().strip()
        if not text:
            if allow_partial:
                return None
            raise ValueError(f"{key} cannot be empty")
        try:
            return int(text)
        except ValueError as exc:
            raise ValueError(f"{key} must be an integer") from exc

    def _set_entry(self, key: str, value: str) -> None:
        self._entries[key].set_text(value)

    def _set_entry_int(self, key: str, value: int) -> None:
        self._entries[key].set_text(str(value))

    def _set_entry_float(self, key: str, value: float) -> None:
        formatted = f"{value:.6f}".rstrip("0").rstrip(".")
        self._entries[key].set_text(formatted)

    def _set_switch(self, key: str, value: bool) -> None:
        self._switches[key].set_active(bool(value))
