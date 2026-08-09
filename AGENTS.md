# AGENTS.md

Project context for AI agents working on meshcore-uconsole.

## Project Overview

GTK4/Libadwaita mesh radio console application targeting Raspberry Pi (uConsole form factor) with Wayland. Connects to MeshCore radio hardware via SPI/GPIO for off-grid mesh communication.

**Target hardware:** Raspberry Pi CM4 in uConsole, 1280x720 display, Wayland compositor.

**Stack:** Python 3.11+, GTK4, Libadwaita, PyGObject, openhop_core (radio driver).

**openhop_core API reference:** https://openhop-dev.github.io/openhop_core/api/core/

## Repository Layout

```text
src/meshcore_console/
  app.py                 # GTK application composition root
  main.py                # Console entrypoint
  core/                  # Domain models + service interfaces
  meshcore/              # openhop_core integration adapter (event_bridge, packet_codec)
  platform/              # Platform helpers (GPIO/SPI/device info)
  ui_gtk/                # GTK views, windows, widgets, CSS
    views/               # Main UI panels (analyzer, messages, settings, etc.)
    widgets/             # Reusable GTK widgets
    windows/             # Top-level window definitions
    state/               # UI state management (UiEventStore)
    resources/           # CSS (tokens.css, theme.css, app.css)
scripts/                 # Bootstrap, run, and package helpers
tests/                   # Unit + integration tests
packaging/deb/           # Debian package metadata
```

## Architecture Patterns

### Event Flow
```
Radio Hardware
    ↓
openhop_core dispatcher callbacks
    ↓
event_bridge.py (attach_dispatcher_callbacks)
    ↓
MeshcoreService.poll_events() queue
    ↓
UiEventStore.pump() (called on GLib timeout)
    ↓
Views call store.since(cursor) to get new events
```

### CSS Token System

Design tokens in `ui_gtk/resources/tokens.css`:

| Token | Value | Usage |
|-------|-------|-------|
| `mc_bg` | `#0f1419` | Main background |
| `mc_surface` | `#171d24` | Card/panel surfaces |
| `mc_surface_alt` | `#1d2530` | Alternate surface |
| `mc_border` | `#2e3a49` | Borders |
| `mc_text` | `#e7eef8` | Primary text |
| `mc_text_muted` | `#aab9cd` | Secondary text |
| `mc_accent` | `#35c29b` | Accent/success |
| `mc_warn` | `#efb93f` | Warning states |
| `mc_danger` | `#ff5f6d` | Error/danger states |

Use `@mc_token` syntax in CSS: `color: @mc_accent;`

## GTK4 Sizing Gotchas

**Critical:** GTK4 calculates widget natural width BEFORE applying wrap/ellipsize constraints.

### Problem Pattern
```python
# BAD: This can cause window to resize to ~7000px!
label = Gtk.Label(label=long_160_char_string)
label.set_wrap(True)
label.set_max_width_chars(42)  # Does NOT constrain natural width!
```

The label reports its natural width as the full unwrapped text width. This propagates up through the widget hierarchy to the toplevel window.

### Solutions

**Option A: ScrolledWindow container** (preferred for variable content)
```python
scroll = Gtk.ScrolledWindow()
scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
scroll.set_max_content_height(120)
scroll.set_propagate_natural_height(True)

label = Gtk.Label(label=long_text)
label.set_wrap(True)
label.set_wrap_mode(Pango.WrapMode.CHAR)
label.set_selectable(True)

scroll.set_child(label)
```

**Option B: Pre-format with line breaks** (for fixed-width content)
```python
formatted = "\n".join(text[i:i+42] for i in range(0, len(text), 42))
label = Gtk.Label(label=formatted)
```

**Option C: set_size_request()** (for fixed layouts)
```python
label.set_size_request(300, -1)  # Explicit pixel width
```

## Development

### Mock Mode

UI development uses mock mode to simulate radio events:
```bash
MESHCORE_MOCK=1 ./scripts/run-dev.sh
```

Mock implementation is in `meshcore/mock_session.py`. It exercises the same adapter paths as production.

### Nix Dev Shell (required)

**All builds, tests, and commands MUST run inside `nix develop`.** The flake provides Python, GTK4, Libadwaita, PyGObject, and all native dependencies. Do not install tooling with pip, brew, or apt — if something is missing, add it to `flake.nix`.

In a git bare-repo worktree the flake isn't at the repo root, so use `nix develop path:.` to point at the current directory's flake.

Prefix every command with `nix develop path:. --command` (or run inside an existing `nix develop` shell):

```bash
nix develop path:. --command uv run pytest
./scripts/run-dev.sh  # enters nix develop automatically
```

### Common Commands

All commands below assume you are inside `nix develop`.

| Command | Description |
|---------|-------------|
| `./scripts/run-dev.sh` | Run GTK app in mock mode (macOS) |
| `./scripts/run-gtk-pi.sh` | Run GTK app on Raspberry Pi |
| `uv run pytest` | Run tests |
| `uv run meshcore-console doctor` | Validate radio hardware |
| `uv run meshcore-console listen` | Listen for mesh events (headless) |

### Environment Variables

| Variable | Description |
|----------|-------------|
| `MESHCORE_MOCK=1` | Enable mock radio mode |
| `MESHCORE_UI_GEOM_DEBUG=1` | Enable geometry debugging logs |
| `MESHCORE_USE_DIO2_RF=1` | Radio hardware flag (default on) |
| `MESHCORE_USE_DIO3_TCXO=1` | Radio hardware flag (default on) |
| `MESHCORE_GPSD_DISABLE=1` | Skip gpsd detection, use direct serial |
| `MESHCORE_GPSD_HOST` | gpsd hostname (default: 127.0.0.1) |
| `MESHCORE_GPSD_PORT` | gpsd port (default: 2947) |
| `MESHCORE_GPS_DEVICE` | GPS serial port; overrides auto-detection and gpsd (also in Settings > GPS Device) |

### Initial Setup (macOS)

`run-dev.sh` auto-creates the venv with `--system-site-packages` if missing or misconfigured. To set up manually:

```bash
nix develop path:. --command bash -c 'uv venv --python "$(which python)" --system-site-packages && uv sync'
```

**Important:** If tests or other tools recreate `.venv` without `--system-site-packages`, PyGObject (`gi`) will be missing and the GTK app will fail to import. Run `./scripts/run-dev.sh` to auto-fix, or re-run the command above.

### Raspberry Pi Development

```bash
# Initial setup
./scripts/bootstrap-pi.sh && sudo reboot
uv venv --python python3 --system-site-packages
uv sync

# Run
./scripts/run-gtk-pi.sh
```

## Common Gotchas

1. **PyGObject not in pyproject.toml** - It comes from Nix (macOS) or system packages (Pi), not pip.

2. **Window unexpectedly resizes** - Check for labels with long text and no width constraint. See "GTK4 Sizing Gotchas" above.

3. **Events not appearing in UI** - Verify `UiEventStore.pump()` is being called on a GLib timeout. Check that event_bridge callbacks are registered.

4. **CSS not applying** - Ensure CSS file is loaded in app.py and class names match. GTK4 uses `add_css_class()` not `get_style_context().add_class()`.

5. **Wayland-specific issues** - Test on actual Pi hardware. Some behaviors differ between XWayland (macOS) and native Wayland.

6. **openhop_core API calls** - openhop_core is a known dependency. Call its APIs directly without defensive `getattr`/`hasattr` fallbacks or manual reimplementations. If an openhop_core method exists (e.g. `packet.get_raw_length()`), call it and let exceptions propagate naturally. Do not duplicate its logic as a fallback — if the API breaks, we want to know immediately, not silently use a stale copy.

## UI Framework Assessment

GTK4/Libadwaita is appropriate for this use case:

**Pros:**
- First-class Wayland support
- Libadwaita provides adaptive UI patterns
- System packages available on Raspberry Pi OS
- Hardware-accelerated rendering

**Acceptable tradeoffs:**
- ~80-120MB memory footprint (acceptable for dedicated device)
- 2-4 second startup (can optimize with lazy widget loading)
- Wayland sizing quirks (mitigated by careful widget construction)
