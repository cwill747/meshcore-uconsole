#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

# Verify build dependencies (installed by bootstrap-pi.sh)
MISSING=()
command -v dh_virtualenv >/dev/null 2>&1 || MISSING+=(dh-virtualenv)
command -v virtualenv >/dev/null 2>&1 || MISSING+=(virtualenv)

if [[ ${#MISSING[@]} -gt 0 ]]; then
  echo "Missing build dependencies: ${MISSING[*]}"
  echo "Run ./scripts/bootstrap-pi.sh first."
  exit 1
fi

dpkg-buildpackage -us -uc -b

# Move output to build/
mkdir -p build
mv ../meshcore-uconsole_*.deb build/
mv ../meshcore-uconsole_*.buildinfo build/ 2>/dev/null || true
mv ../meshcore-uconsole_*.changes build/ 2>/dev/null || true

echo "Built packages in build/"
ls -la build/meshcore-uconsole_*.deb
