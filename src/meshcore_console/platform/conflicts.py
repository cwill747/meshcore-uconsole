"""Pre-flight conflict detection for radio hardware.

Detects processes (e.g. meshtasticd) or permission issues that would prevent
openhop_core from initialising SPI/GPIO.  Runs *before* any radio access so the
UI can show actionable guidance instead of a cryptic exit-code toast.
"""

from __future__ import annotations

import glob
import logging
import os
import subprocess
import sys
from dataclasses import dataclass, field
from enum import Enum, auto

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


class ConflictType(Enum):
    SERVICE = auto()
    GPIO_PIN = auto()
    GPIO_CHIP = auto()
    SPI_DEVICE = auto()
    PERMISSION = auto()


@dataclass(slots=True)
class Conflict:
    kind: ConflictType
    summary: str
    detail: str
    remediation: str
    service_name: str | None = None
    pin: int | None = None


@dataclass(slots=True)
class ConflictReport:
    conflicts: list[Conflict] = field(default_factory=list)

    @property
    def has_conflicts(self) -> bool:
        return len(self.conflicts) > 0

    @property
    def has_service_conflict(self) -> bool:
        return any(c.kind == ConflictType.SERVICE for c in self.conflicts)

    @property
    def service_names(self) -> list[str]:
        return [c.service_name for c in self.conflicts if c.service_name]


class ConflictError(RuntimeError):
    """Raised when pre-flight checks detect hardware conflicts."""

    def __init__(self, report: ConflictReport) -> None:
        self.report = report
        summaries = "; ".join(c.summary for c in report.conflicts)
        super().__init__(f"Hardware conflict: {summaries}")


# ---------------------------------------------------------------------------
# Detection helpers
# ---------------------------------------------------------------------------


def _check_service(name: str) -> Conflict | None:
    """Check whether a systemd service is active (no root required)."""
    try:
        result = subprocess.run(
            ["systemctl", "is-active", "--quiet", name],
            capture_output=True,
            timeout=5,
        )
        if result.returncode == 0:
            return Conflict(
                kind=ConflictType.SERVICE,
                summary=f"{name} is running",
                detail=(
                    f"The {name} service is currently active and holds the SPI bus "
                    f"and GPIO pins that MeshCore needs."
                ),
                remediation=f"sudo systemctl stop {name}",
                service_name=name,
            )
    except FileNotFoundError:
        # Not a systemd system (e.g. macOS) — skip
        pass
    except subprocess.TimeoutExpired:
        logger.debug("Timeout checking service %s", name)
    return None


def _check_spi_device(bus_id: int, cs_id: int) -> Conflict | None:
    """Probe /dev/spidevX.Y for availability."""
    path = f"/dev/spidev{bus_id}.{cs_id}"
    try:
        fd = os.open(path, os.O_RDWR)
        os.close(fd)
    except FileNotFoundError:
        if bus_id >= 1:
            detail_text = (
                f"The SPI device {path} does not exist. "
                f"The dtoverlay=spi{bus_id}-1cs overlay may not be enabled "
                f"in /boot/firmware/config.txt."
            )
            remediation_text = (
                f"sudo sh -c 'echo dtoverlay=spi{bus_id}-1cs "
                f">> /boot/firmware/config.txt'\n"
                f"sudo reboot\n"
                f"\n"
                f"WARNING: Do NOT use 'raspi-config nonint do_spi 0' — that\n"
                f"enables SPI0, not SPI{bus_id}, and can disable the uConsole\n"
                f"internal display on CM5 + Trixie."
            )
        else:
            detail_text = (
                f"The SPI device {path} does not exist. "
                f"SPI may not be enabled in /boot/firmware/config.txt."
            )
            remediation_text = (
                "sudo raspi-config nonint do_spi 0\n"
                "sudo reboot\n"
                "\n"
                "NOTE: On uConsole CM5 + Trixie this command can disable the\n"
                "internal display. If that happens, remove 'dtparam=spi=on'\n"
                "from /boot/firmware/config.txt via SSH and reboot."
            )
        return Conflict(
            kind=ConflictType.SPI_DEVICE,
            summary=f"{path} not found",
            detail=detail_text,
            remediation=remediation_text,
        )
    except PermissionError:
        return Conflict(
            kind=ConflictType.PERMISSION,
            summary=f"Permission denied on {path}",
            detail=(f"Cannot open {path}. Your user may need to be in the 'spi' group."),
            remediation="sudo usermod -aG spi $USER && newgrp spi",
        )
    except OSError as exc:
        if exc.errno == 16:  # EBUSY
            return Conflict(
                kind=ConflictType.SPI_DEVICE,
                summary=f"{path} is busy",
                detail=(
                    f"The SPI device {path} is held by another process. "
                    f"Another radio application may be running."
                ),
                remediation="Check for other processes using lsof " + path,
            )
        logger.debug("SPI probe %s: %s", path, exc)
    return None


def _check_gpio_pin(pin: int, gpio_chip: int = 0) -> Conflict | None:
    """Probe a GPIO pin for availability using periphery."""
    try:
        from periphery import GPIO, GPIOError  # type: ignore[import-not-found]

        chip_path = f"/dev/gpiochip{gpio_chip}"
        try:
            gpio = GPIO(chip_path, pin, "in")
            gpio.close()
        except GPIOError as exc:
            if "Device or resource busy" in str(exc):
                return Conflict(
                    kind=ConflictType.GPIO_PIN,
                    summary=f"GPIO pin {pin} is busy",
                    detail=(
                        f"GPIO pin {pin} is held by another process. "
                        f"Another application may be using the radio hardware."
                    ),
                    remediation=f"Check /sys/kernel/debug/gpio for pin {pin} owner",
                    pin=pin,
                )
            logger.debug("GPIO probe pin %d: %s", pin, exc)
        except PermissionError:
            return Conflict(
                kind=ConflictType.PERMISSION,
                summary=f"Permission denied on GPIO pin {pin}",
                detail=(
                    f"Cannot access GPIO pin {pin}. Your user may need to be in the 'gpio' group."
                ),
                remediation="sudo usermod -aG gpio $USER && newgrp gpio",
                pin=pin,
            )
        except OSError as exc:
            logger.debug("GPIO probe pin %d: %s", pin, exc)
    except ImportError:
        logger.debug("periphery not available, skipping GPIO check for pin %d", pin)
    return None


def available_gpio_chips() -> list[int]:
    """Return the numbers of the GPIO chips present on this host, ascending."""
    chips: list[int] = []
    for path in glob.glob("/dev/gpiochip*"):
        suffix = path[len("/dev/gpiochip") :]
        if suffix.isdigit():
            chips.append(int(suffix))
    return sorted(chips)


def _check_gpio_chip(gpio_chip: int) -> Conflict | None:
    """Verify the configured GPIO chip device exists."""
    chip_path = f"/dev/gpiochip{gpio_chip}"
    if os.path.exists(chip_path):
        return None

    found = available_gpio_chips()
    if found:
        available = ", ".join(str(c) for c in found)
        remediation = (
            f"Set the GPIO chip in Settings > Hardware to one of: {available} "
            f"(or set MESHCORE_GPIO_CHIP). On CM5/Pi 5 kernels the 40-pin "
            f"header is usually {found[-1]}, not 0."
        )
    else:
        available = "none"
        remediation = "No GPIO chips found — check that the kernel exposes /dev/gpiochip*."

    return Conflict(
        kind=ConflictType.GPIO_CHIP,
        summary=f"GPIO chip {chip_path} not found",
        detail=(
            f"The configured GPIO chip {chip_path} does not exist. Available chips: {available}."
        ),
        remediation=remediation,
    )


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def run_preflight_checks(hardware: object) -> ConflictReport:
    """Run all pre-flight conflict checks.

    *hardware* is a ``HardwareRadioConfig`` instance.  Skips everything on
    non-Linux platforms (macOS dev machines).

    Returns a ``ConflictReport`` (which may be empty = no conflicts).
    """
    report = ConflictReport()

    if sys.platform != "linux":
        return report

    # 1. Service check
    conflict = _check_service("meshtasticd")
    if conflict is not None:
        report.conflicts.append(conflict)

    # 2. SPI device check
    bus_id = getattr(hardware, "bus_id", 1)
    cs_id = getattr(hardware, "cs_id", 0)
    conflict = _check_spi_device(bus_id, cs_id)
    if conflict is not None:
        report.conflicts.append(conflict)

    # 3. GPIO chip check — a missing chip makes every pin probe below
    # meaningless, so report it alone and skip them (#85)
    gpio_chip = getattr(hardware, "gpio_chip", 0)
    conflict = _check_gpio_chip(gpio_chip)
    if conflict is not None:
        report.conflicts.append(conflict)
        logger.warning("Pre-flight: %s", conflict.summary)
        return report

    # 4. GPIO pin checks — only probe pins that are actually configured
    # (pins set to -1 are unused and should not be probed)
    pin_attrs = ["reset_pin", "busy_pin", "irq_pin", "cs_pin", "txen_pin", "rxen_pin"]
    pins = [getattr(hardware, attr, -1) for attr in pin_attrs]
    pins.extend(getattr(hardware, "en_pins", ()))
    for pin in pins:
        if pin == -1:
            continue
        conflict = _check_gpio_pin(pin, gpio_chip)
        if conflict is not None:
            report.conflicts.append(conflict)

    if report.has_conflicts:
        logger.warning(
            "Pre-flight: %d conflict(s) detected: %s",
            len(report.conflicts),
            ", ".join(c.summary for c in report.conflicts),
        )
    else:
        logger.debug("Pre-flight: no conflicts detected")

    return report
