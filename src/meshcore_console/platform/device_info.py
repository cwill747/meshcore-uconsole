from dataclasses import dataclass
import platform


@dataclass(slots=True)
class DeviceInfo:
    os_name: str
    machine: str
    is_raspberry_pi: bool


def detect_device() -> DeviceInfo:
    machine = platform.machine().lower()
    return DeviceInfo(
        os_name=platform.system(),
        machine=machine,
        is_raspberry_pi="arm" in machine or "aarch64" in machine,
    )
