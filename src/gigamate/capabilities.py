"""GigaMate — Hardware Capabilities & Feature Detection Engine.

Provides unified probing for all modular hardware subsystems:
1. ACPI thermal & fan profile support (gigamate_acpi / acpi_call)
2. Battery presence & hardware charging threshold support
3. Keyboard USB HID detection & profile mapping status
4. Discrete GPU presence & power management state
5. System DMI identification & Gigabyte laptop verification
"""

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional, Tuple

from . import lamparray
from .acpi import AcpiController
from .battery import get_battery_manager
from .gpu import get_gpu_state
from .power_limits import discover_acpi_profiles


def _read_dmi(filename: str) -> Optional[str]:
    try:
        val = (Path("/sys/class/dmi/id") / filename).read_text().strip()
        return val or None
    except OSError:
        return None


def get_dmi_product_name() -> Optional[str]:
    return _read_dmi("product_name")


def get_dmi_vendor() -> Optional[str]:
    return _read_dmi("sys_vendor")


def get_dmi_chassis_type() -> Optional[int]:
    raw = _read_dmi("chassis_type")
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError:
        return None

# Gigabyte-brand identifiers that may appear in the DMI product name. Vendor
# strings are matched separately via get_dmi_vendor() to avoid false positives
# on non-Gigabyte machines (e.g. a Dell G5 or Acer Aspire A515).
_GIGABYTE_PRODUCT_TOKENS = ("GIGABYTE", "AORUS", "AERO")
_GIGABYTE_VENDOR_TOKENS = ("GIGABYTE", "GIGA-BYTE", "AORUS")

# Capability probing touches USB HID, ACPI and power-supply sysfs, so cache the
# result to avoid re-scanning on every UI refresh tick.
_CAPABILITIES_TTL = 5.0
_cached_capabilities: Optional["HardwareCapabilities"] = None
_cached_at: float = 0.0


def invalidate_capabilities() -> None:
    """Drop the cached capability snapshot so the next probe re-scans hardware."""
    global _cached_capabilities, _cached_at
    _cached_capabilities = None
    _cached_at = 0.0


@dataclass
class HardwareCapabilities:
    """Snapshot of detected hardware support and subsystem availability."""

    # DMI / Machine Identity
    product_name: str
    is_gigabyte_laptop: bool

    # ACPI / Thermal & Fan Control
    acpi_available: bool
    acpi_backend: str  # "module", "acpi_call", "mock", "none"
    acpi_driver_loaded: bool  # True if gigamate_acpi sysfs is active
    acpi_driver_missing: bool  # True if it is a Gigabyte laptop, but ACPI driver is not loaded
    has_power_profiles: bool
    has_temperature: bool
    has_fan_rpm: bool
    fan_count: int
    discovered_profiles: Dict[int, Dict[str, str]] = field(default_factory=dict)

    # Battery
    battery_present: bool = False
    charge_limit_supported: bool = False
    battery_name: str = "None"

    # Keyboard RGB
    keyboard_detected: bool = False
    keyboard_lamparray_supported: bool = False
    keyboard_name: Optional[str] = None
    keyboard_vid_pid: Optional[Tuple[int, int]] = None

    # GPU
    has_dgpu: bool = False
    gpu_name: str = "Integrated Only"
    dgpu_gate_supported: bool = False


def detect_system_capabilities(refresh: bool = False) -> HardwareCapabilities:
    """Return a unified capability snapshot, using the short-lived cache.

    Pass ``refresh=True`` to force a fresh hardware probe (e.g. after loading
    the kernel module or plugging in a keyboard). Use
    :func:`invalidate_capabilities` to drop the cache globally.
    """
    global _cached_capabilities, _cached_at
    now = time.monotonic()
    if (
        not refresh
        and _cached_capabilities is not None
        and (now - _cached_at) < _CAPABILITIES_TTL
    ):
        return _cached_capabilities

    caps = _probe_system_capabilities()
    _cached_capabilities = caps
    _cached_at = now
    return caps


def _probe_system_capabilities() -> HardwareCapabilities:
    """Perform a unified probe of all system hardware and return capabilities."""
    # 1. DMI & Machine Identity
    raw_dmi = get_dmi_product_name()
    product_name = raw_dmi or "Standard PC"

    # Match the OEM vendor separately from the product name so that generic
    # model numbers such as "Dell G5 5500" are not misclassified as Gigabyte.
    vendor_upper = (get_dmi_vendor() or "").upper()
    is_gigabyte = any(token in vendor_upper for token in _GIGABYTE_VENDOR_TOKENS)
    if raw_dmi:
        dmi_upper = raw_dmi.upper()
        if any(token in dmi_upper for token in _GIGABYTE_PRODUCT_TOKENS):
            is_gigabyte = True

    # 2. Battery Subsystem (queried early for chassis laptop vs desktop deduction)
    bat_mgr = get_battery_manager()
    bat_info = bat_mgr.get_battery_info()
    battery_present = bat_mgr.is_available
    charge_limit_supported = bat_mgr.is_charge_limit_supported()
    battery_name = bat_info.name if battery_present else "None"

    # A Gigabyte *desktop* motherboard is not a Gigabyte laptop: chassis types
    # 3..7, 17, 23, 24 are desktop/server variants. Unknown/Other (1, 2) without battery are also desktop.
    if is_gigabyte:
        chassis = get_dmi_chassis_type()
        if chassis is not None and (3 <= chassis <= 7 or chassis in (17, 23, 24)):
            is_gigabyte = False
        elif chassis in (1, 2) and not battery_present:
            is_gigabyte = False

    # 3. ACPI Subsystem
    acpi_ctrl = AcpiController()
    acpi_avail = acpi_ctrl.available
    acpi_caps = acpi_ctrl.capabilities
    acpi_backend = acpi_caps.backend
    driver_loaded = acpi_backend == "module"

    # A Gigabyte keyboard VID alone does not make the *laptop* Gigabyte (an
    # external keyboard on a desktop must not trigger thermal-control advice).
    kbd_info = lamparray.detect_gigabyte_hidraw_keyboard()
    detected_kbd = (kbd_info[0], kbd_info[1]) if kbd_info else None

    acpi_missing = is_gigabyte and not acpi_avail
    discovered_profiles = discover_acpi_profiles() if acpi_avail else {}
    has_power_profiles = bool(acpi_avail and len(discovered_profiles) >= 2)

    # 4. Keyboard RGB Subsystem
    keyboard_detected = kbd_info is not None
    keyboard_name = kbd_info[2] if kbd_info else None
    keyboard_lamparray_supported = False
    lamp_info = lamparray.detect_lamparray_keyboard()
    if lamp_info is not None:
        _lvid, _lpid, node, _iface = lamp_info
        try:
            if lamparray.LampArrayDevice(node).lamp_count() >= 1:
                keyboard_lamparray_supported = True
        except (OSError, ValueError):
            pass

    # 5. GPU Subsystem
    gpu_state = get_gpu_state()
    has_dgpu = gpu_state.present
    gpu_vendor = (gpu_state.vendor or "Discrete").upper()
    gpu_name = f"{gpu_vendor} GPU" if has_dgpu else "Integrated Only"
    try:
        from . import dgpu_gate

        dgpu_gate_supported = bool(dgpu_gate.is_supported())
    except Exception:  # noqa: BLE001
        dgpu_gate_supported = False

    return HardwareCapabilities(
        product_name=product_name,
        is_gigabyte_laptop=is_gigabyte,
        acpi_available=acpi_avail,
        acpi_backend=acpi_backend,
        acpi_driver_loaded=driver_loaded,
        acpi_driver_missing=acpi_missing,
        has_power_profiles=has_power_profiles,
        discovered_profiles=discovered_profiles,
        has_temperature=acpi_caps.has_temperature,
        has_fan_rpm=acpi_caps.has_fan_rpm,
        fan_count=acpi_caps.fan_count,
        battery_present=battery_present,
        charge_limit_supported=charge_limit_supported,
        battery_name=battery_name,
        keyboard_detected=keyboard_detected,
        keyboard_lamparray_supported=keyboard_lamparray_supported,
        keyboard_name=keyboard_name,
        keyboard_vid_pid=detected_kbd,
        has_dgpu=has_dgpu,
        gpu_name=gpu_name,
        dgpu_gate_supported=dgpu_gate_supported,
    )
