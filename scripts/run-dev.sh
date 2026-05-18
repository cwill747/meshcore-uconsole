#!/usr/bin/env bash
set -euo pipefail

if [[ -z "${IN_NIX_SHELL:-}" ]] && command -v nix >/dev/null 2>&1; then
  exec nix develop path:. --command "$0" "$@"
fi

# Ensure venv exists with system-site-packages (needed for PyGObject from Nix)
if [[ ! -f .venv/pyvenv.cfg ]] || ! grep -q 'include-system-site-packages = true' .venv/pyvenv.cfg; then
  echo "Creating venv with --system-site-packages..."
  uv venv --python "$(which python)" --system-site-packages --clear
  uv sync
fi

export MESHCORE_MOCK="${MESHCORE_MOCK:-1}"
uv run python -m meshcore_console.main
