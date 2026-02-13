"""Radio signal utilities for SNR and RSSI interpretation."""

from __future__ import annotations


def snr_to_quality(snr: float) -> str:
    """Convert SNR (dB) to human-readable quality description.

    Typical LoRa SNR ranges:
      >= 10 dB: Excellent signal
      >= 5 dB:  Good signal
      >= 0 dB:  Fair signal
      >= -5 dB: Poor signal (near sensitivity limit)
      < -5 dB:  Very poor (packet loss likely)
    """
    if snr >= 10:
        return "Excellent"
    if snr >= 5:
        return "Good"
    if snr >= 0:
        return "Fair"
    if snr >= -5:
        return "Poor"
    return "Very Poor"


def rssi_to_signal_percent(rssi: int) -> int:
    """Convert RSSI (dBm) to 0-100 signal percentage.

    Maps typical LoRa RSSI range (-120 to -40 dBm) to 0-100%.
    """
    return max(0, min(100, (rssi + 120) * 100 // 80))


def format_snr(snr: float, *, include_quality: bool = True) -> str:
    """Format SNR value with optional quality indicator."""
    if include_quality:
        return f"{snr:.1f} dB ({snr_to_quality(snr)})"
    return f"{snr:.1f} dB"


def format_rssi(rssi: int) -> str:
    """Format RSSI value."""
    return f"{rssi} dBm"
