"""Pre-flight detection of a missing/misconfigured GPIO chip (#85)."""

from __future__ import annotations

from meshcore_console.meshcore.config import HardwareRadioConfig
from meshcore_console.platform import conflicts as mod
from meshcore_console.platform.conflicts import ConflictType, run_preflight_checks


def _fake_chips(monkeypatch, present: list[int]) -> None:
    """Pretend /dev holds exactly the given gpiochip devices."""
    paths = {f"/dev/gpiochip{n}" for n in present}
    monkeypatch.setattr(mod.os.path, "exists", lambda p: p in paths)
    monkeypatch.setattr(mod, "available_gpio_chips", lambda: sorted(present))


def test_missing_chip_is_reported_with_available_alternatives(monkeypatch) -> None:
    monkeypatch.setattr(mod.sys, "platform", "linux")
    monkeypatch.setattr(mod, "_check_service", lambda _name: None)
    monkeypatch.setattr(mod, "_check_spi_device", lambda _b, _c: None)
    _fake_chips(monkeypatch, [11, 12, 13, 14, 15])

    report = run_preflight_checks(HardwareRadioConfig(gpio_chip=0))

    assert report.has_conflicts
    conflict = report.conflicts[0]
    assert conflict.kind is ConflictType.GPIO_CHIP
    assert "/dev/gpiochip0" in conflict.summary
    # The reporter needs to be told what to switch to.
    assert "15" in conflict.remediation


def test_missing_chip_suppresses_noisy_pin_probes(monkeypatch) -> None:
    """Every pin probe would fail for the same reason; report the cause once."""
    monkeypatch.setattr(mod.sys, "platform", "linux")
    monkeypatch.setattr(mod, "_check_service", lambda _name: None)
    monkeypatch.setattr(mod, "_check_spi_device", lambda _b, _c: None)
    _fake_chips(monkeypatch, [15])

    probed: list[int] = []
    monkeypatch.setattr(mod, "_check_gpio_pin", lambda pin, chip=0: probed.append(pin))

    report = run_preflight_checks(HardwareRadioConfig(gpio_chip=0))

    assert len(report.conflicts) == 1
    assert probed == []


def test_configured_chip_present_allows_pin_probes(monkeypatch) -> None:
    monkeypatch.setattr(mod.sys, "platform", "linux")
    monkeypatch.setattr(mod, "_check_service", lambda _name: None)
    monkeypatch.setattr(mod, "_check_spi_device", lambda _b, _c: None)
    _fake_chips(monkeypatch, [15])

    probed: list[tuple[int, int]] = []
    monkeypatch.setattr(
        mod, "_check_gpio_pin", lambda pin, chip=0: probed.append((pin, chip)) or None
    )

    config = HardwareRadioConfig(gpio_chip=15, en_pins=(16,))
    report = run_preflight_checks(config)

    assert not report.has_conflicts
    # Pins are probed against the configured chip, not a hardcoded 0.
    assert probed, "expected pin probes to run"
    assert {chip for _pin, chip in probed} == {15}
    # Unused pins (-1) are skipped; configured enable pins are included.
    assert -1 not in [pin for pin, _chip in probed]
    assert 16 in [pin for pin, _chip in probed]


def test_no_chips_at_all_reports_kernel_problem(monkeypatch) -> None:
    monkeypatch.setattr(mod.sys, "platform", "linux")
    monkeypatch.setattr(mod, "_check_service", lambda _name: None)
    monkeypatch.setattr(mod, "_check_spi_device", lambda _b, _c: None)
    _fake_chips(monkeypatch, [])

    report = run_preflight_checks(HardwareRadioConfig(gpio_chip=0))

    conflict = report.conflicts[0]
    assert conflict.kind is ConflictType.GPIO_CHIP
    assert "No GPIO chips found" in conflict.remediation


def test_non_linux_hosts_skip_all_checks(monkeypatch) -> None:
    monkeypatch.setattr(mod.sys, "platform", "darwin")
    report = run_preflight_checks(HardwareRadioConfig(gpio_chip=99))
    assert not report.has_conflicts
