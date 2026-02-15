#!/usr/bin/env python3
"""Capture screenshots of each view from the mock-mode GTK app.

Usage:
    MESHCORE_MOCK=1 python scripts/capture_screenshots.py [output_dir]

Output directory defaults to docs/.
"""

from __future__ import annotations

import os
import random
import signal
import sys
from pathlib import Path

# Seed before any mock imports so data is deterministic
random.seed(42)

os.environ["MESHCORE_MOCK"] = "1"

# Add src to path so we can import without install
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from meshcore_console.meshcore.logging_setup import configure_logging

import gi

configure_logging()

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, GLib, Gio, Gtk

from meshcore_console.app import APP_ID, _load_css
from meshcore_console.ui_gtk.windows.main_window import MainWindow

# Views to capture in order: (stack page name, output filename)
VIEWS = [
    ("analyzer", "analyzer.png"),
    ("peers", "peers.png"),
    ("messages", "channels.png"),
    ("map", "map.png"),
]


class ScreenshotApp(Adw.Application):
    """Headless app that captures a screenshot of each view then exits."""

    def __init__(self, out_dir: Path) -> None:
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.FLAGS_NONE)
        self._out_dir = out_dir
        self._capture_index = 0

        use_mock = os.environ.get("MESHCORE_MOCK", "0") == "1"
        if use_mock:
            from meshcore_console.mock import MockMeshcoreClient

            self.service = MockMeshcoreClient()
        else:
            from meshcore_console.meshcore.client import MeshcoreClient

            self.service = MeshcoreClient()

    def do_activate(self) -> None:
        _load_css()
        window = MainWindow(application=self, service=self.service)
        window.present()

        # Wait for mock data to populate (event pump is 1s, views poll at 1.5-2s)
        GLib.timeout_add(4000, self._start_captures)

    def _start_captures(self) -> bool:
        self._capture_next()
        return False  # one-shot

    def _capture_next(self) -> None:
        if self._capture_index >= len(VIEWS):
            print(f"All {len(VIEWS)} screenshots captured to {self._out_dir}/")
            self.quit()
            return

        page_name, filename = VIEWS[self._capture_index]
        window = self.props.active_window

        # Switch view
        window._stack.set_visible_child_name(page_name)
        # Update nav button active states
        for name, btn in window._nav_buttons.items():
            btn.set_active(name == page_name)

        # Wait for render to settle, then capture
        GLib.timeout_add(1500, self._do_capture, filename)

    def _do_capture(self, filename: str) -> bool:
        window = self.props.active_window
        out_path = self._out_dir / filename

        paintable = Gtk.WidgetPaintable.new(window)
        width = paintable.get_intrinsic_width()
        height = paintable.get_intrinsic_height()

        if width <= 0 or height <= 0:
            print(f"Warning: paintable reports {width}x{height}, retrying...")
            GLib.timeout_add(500, self._do_capture, filename)
            return False

        snapshot = Gtk.Snapshot.new()
        paintable.snapshot(snapshot, width, height)
        node = snapshot.to_node()

        if node is None:
            print(f"Warning: snapshot returned no render node for {filename}, retrying...")
            GLib.timeout_add(500, self._do_capture, filename)
            return False

        native = window.get_native()
        renderer = native.get_renderer()
        texture = renderer.render_texture(node, None)
        texture.save_to_png(str(out_path))

        print(f"  Captured {filename} ({width}x{height})")

        self._capture_index += 1
        GLib.timeout_add(200, lambda: self._capture_next() or False)
        return False  # one-shot


def main() -> None:
    out_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("docs")
    out_dir.mkdir(parents=True, exist_ok=True)

    app = ScreenshotApp(out_dir)
    GLib.unix_signal_add(GLib.PRIORITY_HIGH, signal.SIGINT, lambda: app.quit() or True)
    status = app.run([])
    sys.exit(status)


if __name__ == "__main__":
    main()
