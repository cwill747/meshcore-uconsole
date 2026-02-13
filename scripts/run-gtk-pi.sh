#!/usr/bin/env bash
# Runs the GTK app on Raspberry Pi. Works fully offline once bootstrapped.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

# Bootstrap mode (requires network)
if [[ "${1:-}" == "--bootstrap" ]]; then
  if ! command -v uv >/dev/null 2>&1; then
    echo "uv is required. Install it first: https://docs.astral.sh/uv/getting-started/installation/"
    exit 1
  fi
  if [[ ! -d .venv ]]; then
    echo "Creating .venv with system site packages for PyGObject..."
    uv venv --python python3 --system-site-packages
  fi
  uv sync
  echo "Bootstrap complete. Run without --bootstrap to start the app."
  exit 0
fi

# Normal run (fully offline)
if [[ ! -d .venv ]] || [[ ! -f .venv/bin/python ]]; then
  echo "No .venv found. Run with --bootstrap first (requires network)."
  exit 1
fi

mock_mode=0
if [[ "${1:-}" == "--mock" ]]; then
  mock_mode=1
  shift
fi

export MESHCORE_MOCK="$mock_mode"
export GTK_A11Y="${GTK_A11Y:-none}"
export PYTHONPATH=src
exec .venv/bin/python -m meshcore_console.main "$@"
