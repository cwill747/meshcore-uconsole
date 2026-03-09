"""Pre-flight conflict detection for radio hardware.

Detects processes (e.g. meshtasticd) or permission issues that would prevent
pyMC_core from initialising SPI/GPIO.  Runs *before* any radio access so the
UI can show actionable guidance instead of a cryptic exit-code toast.
"""

from __future__ import annotations

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
        return Conflict(
            kind=ConflictType.SPI_DEVICE,
            summary=f"{path} not found",
            detail=(
                f"The SPI device {path} does not exist. SPI may not be enabled in /boot/config.txt."
            ),
            remediation="sudo raspi-config nonint do_spi 0",
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


def _check_gpio_pin(pin: int) -> Conflict | None:
    """Probe a GPIO pin for availability using periphery."""
    try:
        from periphery import GPIO, GPIOError  # type: ignore[import-not-found]

        try:
            gpio = GPIO("/dev/gpiochip0", pin, "in")
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

    # 3. GPIO pin checks — only probe pins that are actually configured
    # (pins set to -1 are unused and should not be probed)
    pin_attrs = ["reset_pin", "busy_pin", "irq_pin", "cs_pin", "txen_pin", "rxen_pin"]
    for attr in pin_attrs:
        pin = getattr(hardware, attr, -1)
        if pin == -1:
            continue
        conflict = _check_gpio_pin(pin)
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
