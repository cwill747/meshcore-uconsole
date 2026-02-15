#!/usr/bin/env bash
set -euo pipefail

# Re-enter Nix shell if available and not already inside one
if [[ -z "${IN_NIX_SHELL:-}" ]] && command -v nix >/dev/null 2>&1; then
  exec nix develop --command "$0" "$@"
fi

export MESHCORE_MOCK=1
export GTK_A11Y=none

# Compile GSettings schemas from Nix-provided packages into a temp dir.
# Nix packages ship schema XML but not always the compiled gschemas.compiled
# that GLib requires at runtime.
SCHEMA_DIR="$(mktemp -d)"
trap 'rm -rf "$SCHEMA_DIR"' EXIT
for dir in $(echo "${XDG_DATA_DIRS:-}" | tr ':' ' '); do
  if [[ -d "$dir/glib-2.0/schemas" ]]; then
    cp "$dir/glib-2.0/schemas/"*.xml "$SCHEMA_DIR/" 2>/dev/null || true
  fi
done
if command -v glib-compile-schemas >/dev/null 2>&1; then
  glib-compile-schemas "$SCHEMA_DIR"
fi
export GSETTINGS_SCHEMA_DIR="$SCHEMA_DIR"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CAPTURE_SCRIPT="$SCRIPT_DIR/capture_screenshots.py"

# Use xvfb if available and no display server is running
if [[ -z "${DISPLAY:-}" && -z "${WAYLAND_DISPLAY:-}" ]] && command -v xvfb-run >/dev/null 2>&1; then
  echo "No display detected, using xvfb (1920x1080)..."
  exec xvfb-run --auto-servernum --server-args="-screen 0 1920x1080x24" \
    python "$CAPTURE_SCRIPT" "$@"
fi

exec python "$CAPTURE_SCRIPT" "$@"
