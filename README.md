# meshcore-uconsole

GTK/PyGObject meshcore console scaffold targeting Raspberry Pi.

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

If you already created `.venv` without system site packages, recreate it:
```bash
rm -rf .venv
nix develop --command sh -lc 'uv venv --python "$(which python)" --system-site-packages'
nix develop --command uv sync
```

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
uv run meshcore-radio doctor
```

4. Start listening for mesh events:
```bash
uv run meshcore-radio listen
```

5. Send a message:
```bash
uv run meshcore-radio send --peer "<contact-name>" --message "hello"
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
| `meshcore-radio doctor` | Validate SPI/GPIO configuration and radio hardware connectivity |
| `meshcore-radio listen` | Listen for mesh packets and print decoded events to stdout |
| `meshcore-radio send` | Send a direct message to a named peer |

### Hardware Troubleshooting

If `doctor` fails on SPI/GPIO, confirm these before retrying:
- SPI is enabled (`sudo raspi-config nonint do_spi 0`)
- `/boot/firmware/config.txt` contains `dtoverlay=spi1-1cs`
- You rebooted after bootstrap

Hardware overrides can be supplied via env vars when running `meshcore-radio`.
Notable radio bring-up flags:
- `MESHCORE_USE_DIO2_RF=1` (default in this repo)
- `MESHCORE_USE_DIO3_TCXO=1` (default in this repo)

## Notes

- UI development on macOS uses mock mode by default (`MESHCORE_MOCK=1`).
  Mocking is implemented at the session/runtime layer (`MockPyMCCoreSession`) so UI and
  `MeshcoreClient` still exercise production adapter paths.
- UI settings are persisted to `~/.config/meshcore-uconsole/settings.json`.
- Packaging is intended to run on Raspberry Pi first (`./scripts/package-pi.sh`).
- Debian packaging now installs a stable launcher at `/usr/bin/meshcore-console`
  and desktop entry startup uses that launcher.
- System GTK/GObject libraries are expected from the OS package manager on Pi.
- `PyGObject` is intentionally not a default pip dependency in this repo; it is expected to come from Nix/OS packages.
- `pymc-core[all]` is installed on Linux targets via `uv sync` and backs `meshcore-radio`.
