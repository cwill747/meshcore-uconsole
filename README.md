# meshcore-uconsole

A GTK-based native desktop application for interacting with
[MeshCore](https://meshcore.co.uk/) radios on Raspberry Pi. Supports all
hardware that [openhop-core](https://github.com/openhop-dev/openhop_core) supports,
including the [HackerGadgets AIO](https://hackergadgets.com/products/uconsole-aio-v2)
(uConsole), Waveshare LoRa HATs, and meshadv-mini boards. Built-in hardware
presets let you switch between boards without manually configuring every pin.

Inspired by [YAMPA](https://github.com/guax/YAMPA), and built on top of the
great [openhop-core](https://github.com/openhop-dev/openhop_core) library.

You can run a Mock version of the application on anything that supports Nix, and
then you can run the real application on the uConsole either by cloning the repo
and following the below instructions, or installing from the APT repository.

## Install on Raspberry Pi

### APT Repository (recommended)

Packages are built for **Bookworm** (Debian 12 / Raspberry Pi OS) and **Trixie** (Debian 13). Check your version with `lsb_release -cs`.

```bash
# Add signing key
curl -fsSL https://cwill747.github.io/meshcore-uconsole/KEY.gpg \
  | sudo gpg --dearmor -o /usr/share/keyrings/meshcore.gpg

# Add repository — replace DISTRO with bookworm or trixie
echo "deb [signed-by=/usr/share/keyrings/meshcore.gpg arch=arm64] \
  https://cwill747.github.io/meshcore-uconsole DISTRO main" \
  | sudo tee /etc/apt/sources.list.d/meshcore.list

# Install
sudo apt update && sudo apt install meshcore-uconsole
```

Future updates are available via `sudo apt update && sudo apt upgrade`.

### Manual install

Download the `.deb` for your distro from [Releases](https://github.com/cwill747/meshcore-uconsole/releases) and install:

```bash
# Bookworm (Debian 12)
sudo apt install ./meshcore-uconsole_*~bookworm_arm64.deb
# Trixie (Debian 13)
sudo apt install ./meshcore-uconsole_*~trixie_arm64.deb
```

## Screenshots

| Analyzer | Peers |
|----------|-------|
| ![Analyzer](docs/analyzer.png) | ![Peers](docs/peers.png) |

| Channels | Map |
|----------|-----|
| ![Channels](docs/channels.png) | ![Map](docs/map.png) |

## Prerequisites

| Platform | Requirements |
|----------|-------------|
| **macOS** | [Nix package manager](https://nixos.org/download.html) |
| **Raspberry Pi** | Python 3.11+, [`uv`](https://docs.astral.sh/uv/getting-started/installation/) |

**Why `--system-site-packages`?** PyGObject cannot be installed via pip on most platforms. It must come from system packages (Raspberry Pi) or Nix (macOS). The `--system-site-packages` flag allows the virtual environment to access these system-installed packages.

## macOS Development (with Nix)

1. Create and sync the local Python environment:
```bash
nix develop --command sh -lc 'uv venv --python "$(which python)" --system-site-packages'
nix develop --command uv sync
```

2. Run the app in mock mode:
```bash
./scripts/run-dev.sh
```

3. Run smoke checks:
```bash
uv run pytest
```

**Note:** `run-dev.sh` always enables mock mode (`MESHCORE_MOCK=1`) since macOS has no radio hardware.

## Raspberry Pi Setup (no Nix)

1. On the Pi, run base bootstrap and reboot:
```bash
./scripts/bootstrap-pi.sh
sudo reboot
```

2. Create/sync the local Python environment:
```bash
uv venv --python python3 --system-site-packages
uv sync
```

3. Validate host + hardware:
```bash
uv run meshcore-console doctor
```

4. Start listening for mesh events:
```bash
uv run meshcore-console listen
```

5. Send a message:
```bash
uv run meshcore-console send --peer "<contact-name>" --message "hello"
```

6. Launch GTK UI on Pi:
```bash
./scripts/run-gtk-pi.sh
```

Use mock mode on Pi if desired:
```bash
MESHCORE_MOCK=1 ./scripts/run-gtk-pi.sh
```

### CLI Commands

| Command | Description |
|---------|-------------|
| `meshcore-console doctor` | Validate SPI/GPIO configuration and radio hardware connectivity |
| `meshcore-console listen` | Listen for mesh packets and print decoded events to stdout |
| `meshcore-console send` | Send a direct message to a named peer |

### Hardware Troubleshooting

If `doctor` fails on SPI/GPIO, confirm these before retrying:
- `/boot/firmware/config.txt` contains `dtoverlay=spi1-1cs` (enables SPI1 for the radio)
- You rebooted after adding the overlay

> **Warning:** Do not run `sudo raspi-config nonint do_spi 0` — this enables
> SPI0 (`dtparam=spi=on`), not SPI1, and on CM5 + Trixie it can disable the
> uConsole internal display. If this has happened, remove `dtparam=spi=on`
> from `/boot/firmware/config.txt` via SSH and reboot.

Hardware overrides can be supplied via env vars when running `meshcore-console`
or the GTK app. An env var wins over the value saved in Settings > Hardware, so
a saved setting that stops the radio from starting stays recoverable from the
command line. Notable radio bring-up flags:
- `MESHCORE_USE_DIO2_RF=1` (default in this repo)
- `MESHCORE_USE_DIO3_TCXO=1` (default in this repo)
- `MESHCORE_GPIO_CHIP=15` — which `/dev/gpiochipN` carries the 40-pin header.
  It is 0 on CM4, but 15 on CM5 and Pi 5 kernels, where the header hangs off
  the RP1. `doctor` lists the chips your host actually has.
- `MESHCORE_EN_PINS=27` — GPIO pins driven HIGH at init to power the radio.
  The HackerGadgets AIOv2 enable pin is 27; the **uConsole HG AIOv2** board
  preset sets this for you.
- `MESHCORE_USE_GPIOD_BACKEND=1` — poll for IRQ edges instead of asking the
  kernel for edge interrupts. Try this if the radio connects but receives
  nothing, which happens on kernels that reject the edge request.
