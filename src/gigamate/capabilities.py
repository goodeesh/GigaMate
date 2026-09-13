"""GigaMate — Hardware Capabilities & Feature Detection Engine.

Provides unified probing for all modular hardware subsystems:
1. ACPI thermal & fan profile support (gigamate_acpi / acpi_call)
2. Battery presence & hardware charging threshold support
3. Keyboard USB HID detection & profile mapping status
4. Discrete GPU presence & power management state
5. System DMI identification & Gigabyte laptop verification
"""

import time
from dataclasses import dataclass
from typing import Optional, Tuple

from .acpi import AcpiController
from .battery import get_battery_manager
from .gpu import get_gpu_state
from .profiles import (
    GIGABYTE_VIDS,
    detect_device,
    get_dmi_product_name,
    get_dmi_vendor,
    resolve_profile,
)

# Gigabyte-brand identifiers that may appear in the DMI product name. Vendor
# strings are matched separately via get_dmi_vendor() to avoid false positives
# on non-Gigabyte machines (e.g. a Dell G5 or Acer Aspire A515).
_GIGABYTE_PRODUCT_TOKENS = ("GIGABYTE", "AORUS", "AERO")
_GIGABYTE_VENDOR_TOKENS = ("GIGABYTE", "AORUS")

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

    # Battery
    battery_present: bool
    charge_limit_supported: bool
    battery_name: str

    # Keyboard RGB
    keyboard_detected: bool
    keyboard_profile_loaded: bool
    keyboard_profile_name: Optional[str]
    keyboard_vid_pid: Optional[Tuple[int, int]]

    # GPU
    has_dgpu: bool
    gpu_name: str


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

    # 2. ACPI Subsystem
    acpi_ctrl = AcpiController()
    acpi_avail = acpi_ctrl.available
    acpi_caps = acpi_ctrl.capabilities
    acpi_backend = acpi_caps.backend
    driver_loaded = acpi_backend == "module"

    # The gigamate_acpi module only binds on genuine Gigabyte ECs.
    if acpi_backend == "module":
        is_gigabyte = True

    # If keyboard has a Gigabyte VID, it's definitely a Gigabyte laptop
    detected_kbd = detect_device()
    if detected_kbd and detected_kbd[0] in GIGABYTE_VIDS:
        is_gigabyte = True

    acpi_missing = is_gigabyte and not acpi_avail

    # 3. Battery Subsystem
    bat_mgr = get_battery_manager()
    bat_info = bat_mgr.get_battery_info()
    battery_present = bat_mgr.is_available
    charge_limit_supported = bat_mgr.is_charge_limit_supported()
    battery_name = bat_info.name if battery_present else "None"

    # 4. Keyboard RGB Subsystem
    keyboard_detected = detected_kbd is not None
    keyboard_profile_loaded = False
    keyboard_profile_name = None
    if detected_kbd:
        prof = resolve_profile(detected_kbd[0], detected_kbd[1])
        if prof is not None:
            keyboard_profile_loaded = True
            keyboard_profile_name = prof.name

    # 5. GPU Subsystem
    gpu_state = get_gpu_state()
    has_dgpu = gpu_state.present
    gpu_vendor = (gpu_state.vendor or "Discrete").upper()
    gpu_name = f"{gpu_vendor} GPU" if has_dgpu else "Integrated Only"

    return HardwareCapabilities(
        product_name=product_name,
        is_gigabyte_laptop=is_gigabyte,
        acpi_available=acpi_avail,
        acpi_backend=acpi_backend,
        acpi_driver_loaded=driver_loaded,
        acpi_driver_missing=acpi_missing,
        has_power_profiles=acpi_caps.has_power_profiles,
        has_temperature=acpi_caps.has_temperature,
        has_fan_rpm=acpi_caps.has_fan_rpm,
        fan_count=acpi_caps.fan_count,
        battery_present=battery_present,
        charge_limit_supported=charge_limit_supported,
        battery_name=battery_name,
        keyboard_detected=keyboard_detected,
        keyboard_profile_loaded=keyboard_profile_loaded,
        keyboard_profile_name=keyboard_profile_name,
        keyboard_vid_pid=detected_kbd,
        has_dgpu=has_dgpu,
        gpu_name=gpu_name,
    )
