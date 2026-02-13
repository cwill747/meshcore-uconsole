#!/usr/bin/env bash
set -euo pipefail

# Determine libgpiod package name (bookworm: libgpiod2, trixie: libgpiod3)
sudo apt-get update
if apt-cache show libgpiod3 >/dev/null 2>&1; then
  LIBGPIOD_PKG=libgpiod3
elif apt-cache show libgpiod2 >/dev/null 2>&1; then
  LIBGPIOD_PKG=libgpiod2
else
  echo "Could not find libgpiod2 or libgpiod3 in apt repositories."
  exit 1
fi

sudo apt-get install -y \
  python3-venv \
  python3-dev \
  python3-pip \
  python3-setuptools \
  python3-gi \
  gir1.2-gtk-4.0 \
  gir1.2-adw-1 \
  gir1.2-shumate-1.0 \
  libgtk-4-1 \
  libadwaita-1-0 \
  libshumate-1.0-1 \
  python3-rpi.lgpio \
  python3-serial \
  gobject-introspection \
  pkg-config \
  raspi-config \
  dpkg-dev \
  debhelper \
  dh-virtualenv \
  virtualenv \
  libgpiod-dev \
  "$LIBGPIOD_PKG"

if ! command -v uv >/dev/null 2>&1; then
  echo "uv is not installed."
  echo "Install uv with one of:"
  echo "  curl -LsSf https://astral.sh/uv/install.sh | sh"
  echo "  pipx install uv"
  echo "Then re-run this script."
  exit 1
fi

sudo raspi-config nonint do_spi 0 || true

BOOT_CONFIG=""
if [[ -f /boot/firmware/config.txt ]]; then
  BOOT_CONFIG="/boot/firmware/config.txt"
elif [[ -f /boot/config.txt ]]; then
  BOOT_CONFIG="/boot/config.txt"
else
  echo "Could not find Raspberry Pi boot config (tried /boot/firmware/config.txt and /boot/config.txt)."
  exit 1
fi

if ! grep -q '^dtoverlay=spi1-1cs$' "$BOOT_CONFIG"; then
  echo "Enabling SPI1 overlay (dtoverlay=spi1-1cs)"
  echo 'dtoverlay=spi1-1cs' | sudo tee -a "$BOOT_CONFIG" >/dev/null
fi

if ! grep -q '^dtparam=spi=on$' "$BOOT_CONFIG"; then
  echo "Enabling SPI dtparam (dtparam=spi=on)"
  echo 'dtparam=spi=on' | sudo tee -a "$BOOT_CONFIG" >/dev/null
fi

if systemctl list-unit-files | grep -q '^devterm-printer.service'; then
  sudo systemctl disable --now devterm-printer.service || true
fi

echo "Bootstrap complete."
echo "Next:"
echo "  1) Reboot"
echo "  2) uv venv"
echo "  3) uv sync"
echo "  4) uv run meshcore-radio doctor"
