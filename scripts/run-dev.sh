#!/usr/bin/env bash
set -euo pipefail

if [[ -z "${IN_NIX_SHELL:-}" ]] && command -v nix >/dev/null 2>&1; then
  exec nix develop --command "$0" "$@"
fi

export MESHCORE_MOCK="${MESHCORE_MOCK:-1}"
PYTHONPATH=src python -m meshcore_console.main
