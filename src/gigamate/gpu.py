"""GigaMate — Discrete GPU (dGPU) power state monitoring.

Reads the power state of the discrete NVIDIA GPU from the kernel's PCI
runtime power-management sysfs interface:

    /sys/bus/pci/devices/<bdf>/power/runtime_status   -> "active" / "suspended"
    /sys/bus/pci/devices/<bdf>/power_state            -> "D0" / "D3hot" / "D3cold"

These are plain kernel bookkeeping reads: they never touch the GPU
hardware, so checking the state does NOT wake the GPU (unlike
`nvidia-smi`, which must not be used here).

The NVIDIA device is discovered by scanning /sys/bus/pci/devices for a
device with vendor 0x10de and PCI class 0x03xxxx (display controller),
so no PCI address is hard-coded.
"""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


PCI_SYSFS = Path("/sys/bus/pci/devices")

# NVIDIA vendor id as printed in /sys/bus/pci/devices/*/vendor
NVIDIA_VENDOR = "0x10de"

# AMD vendor id. Only *discrete* AMD GPUs count: the integrated GPU shares
# this vendor id but is always awake, so it must be excluded via boot_vga.
AMD_VENDOR = "0x1002"


@dataclass
class GpuState:
    """Snapshot of the discrete GPU power state.

    All fields except ``present`` are Optional — partial sysfs support is
    handled gracefully.
    """

    present: bool = False
    status: Optional[str] = None  # "active" or "suspended"
    power_state: Optional[str] = None  # "D0", "D3hot", "D3cold", ...
    vendor: Optional[str] = None  # "nvidia", "amd", or None when absent
    dynamic_boost_supported: bool = False
    dynamic_boost_active: bool = False
    smartshift_supported: bool = False
    smartshift_bias: Optional[int] = None


class NvidiaGpuMonitor:
    """Monitor for the power state and dynamic power features of a discrete GPU.

    Reads are sysfs-only and never wake the GPU.
    """

    def __init__(
        self,
        pci_sysfs: Optional[Path] = None,
        proc_nvidia: Optional[Path] = None,
    ) -> None:
        """Initialise monitor.

        Args:
            pci_sysfs: Override the PCI sysfs root (mainly for tests).
                        If None, the module-level ``PCI_SYSFS`` is used.
            proc_nvidia: Override the NVIDIA proc path (mainly for tests).
                         If None, Path("/proc/driver/nvidia") is used.
        """
        self._pci_sysfs: Optional[Path] = pci_sysfs
        self._proc_nvidia: Optional[Path] = proc_nvidia
        self._device: Optional[Path] = None
        self._vendor: Optional[str] = None

    @property
    def _sysfs_root(self) -> Path:
        return self._pci_sysfs or PCI_SYSFS

    def detect(self) -> bool:
        """Find the discrete GPU device. Returns True if found."""
        self._device, self._vendor = self._find_device()
        return self._device is not None

    @property
    def is_available(self) -> bool:
        """Whether a discrete GPU is present."""
        if self._device is None:
            self.detect()
        return self._device is not None

    def read_state(self) -> GpuState:
        """Read the current GPU power state (sysfs only, never wakes it)."""
        if not self.is_available:
            return GpuState(present=False)

        state = GpuState(present=True, vendor=self._vendor)
        assert self._device is not None  # guaranteed by is_available
        state.status = self._read_text(self._device / "power" / "runtime_status")
        state.power_state = self._read_text(self._device / "power_state")

        if self._vendor == "nvidia":
            state.dynamic_boost_supported = self._check_dynamic_boost_supported()
            if state.dynamic_boost_supported:
                state.dynamic_boost_active = self._check_dynamic_boost_active()
        elif self._vendor == "amd":
            state.smartshift_supported = self._check_smartshift_supported()
            if state.smartshift_supported:
                state.smartshift_bias = self._read_smartshift_bias()

        return state

    def _find_device(self) -> tuple:
        """Scan the PCI sysfs root for a discrete GPU.

        Returns (device_path_or_None, vendor_or_None). NVIDIA matches take
        priority. AMD matches must not be the boot display (boot_vga=1),
        which excludes integrated graphics. A missing boot_vga file is
        treated conservatively as "not a dGPU" to avoid a permanently
        lit indicator on iGPU-only machines.
        """
        nvidia: Optional[Path] = None
        amd: Optional[Path] = None
        try:
            root = self._sysfs_root
            if not root.is_dir():
                return None, None
            for entry in root.iterdir():
                if not entry.is_dir():
                    continue
                try:
                    vendor = (entry / "vendor").read_text().strip()
                    cls = (entry / "class").read_text().strip()
                except OSError:
                    continue
                if not cls.startswith("0x03"):
                    continue
                if vendor == NVIDIA_VENDOR and nvidia is None:
                    nvidia = entry
                elif vendor == AMD_VENDOR and amd is None:
                    # Only accept AMD devices explicitly flagged as
                    # non-boot (boot_vga=0). This excludes integrated
                    # graphics (boot_vga=1); a missing flag is treated
                    # conservatively as "not a dGPU".
                    if self._read_text(entry / "boot_vga") == "0":
                        amd = entry
        except OSError:
            pass
        if nvidia is not None:
            return nvidia, "nvidia"
        if amd is not None:
            return amd, "amd"
        return None, None

    def _read_text(self, path: Path) -> Optional[str]:
        """Read a sysfs text file, returning None on any failure."""
        try:
            text = path.read_text().strip()
            return text or None
        except (OSError, IOError):
            return None

    def _check_dynamic_boost_supported(self) -> bool:
        """Check if discrete NVIDIA GPU supports Dynamic Boost."""
        if self._vendor != "nvidia":
            return False

        # 1. Check /proc/driver/nvidia/gpus/*/power
        proc_root = self._proc_nvidia or Path("/proc/driver/nvidia")
        gpus_dir = proc_root / "gpus" if proc_root.name != "gpus" else proc_root
        if gpus_dir.is_dir():
            try:
                for p in gpus_dir.iterdir():
                    power_file = p / "power"
                    if power_file.is_file():
                        content = power_file.read_text(errors="ignore")
                        if "Notebook Dynamic Boost:     Supported" in content or "Notebook Dynamic Boost: Supported" in content:
                            return True
            except (OSError, IOError):
                pass

        # 2. Check if nvidia-powerd service unit or binary exists
        for unit_dir in [Path("/usr/lib/systemd/system"), Path("/etc/systemd/system")]:
            if (unit_dir / "nvidia-powerd.service").is_file():
                return True
        if Path("/usr/bin/nvidia-powerd").is_file():
            return True

        return False

    def _check_dynamic_boost_active(self) -> bool:
        """Check if nvidia-powerd.service is active."""
        if self._vendor != "nvidia":
            return False

        # 1. Try DBus query
        try:
            import gi
            gi.require_version("Gio", "2.0")
            from gi.repository import Gio
            bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
            proxy = Gio.DBusProxy.new_sync(
                bus,
                Gio.DBusProxyFlags.NONE,
                None,
                "org.freedesktop.systemd1",
                "/org/freedesktop/systemd1/unit/nvidia_2dpowerd_2eservice",
                "org.freedesktop.DBus.Properties",
                None,
            )
            val = proxy.call_sync(
                "Get",
                gi.repository.GLib.Variant("(ss)", ("org.freedesktop.systemd1.Unit", "ActiveState")),
                Gio.DBusCallFlags.NONE,
                300,
                None,
            )
            if val and len(val) > 0:
                res = val[0]
                if isinstance(res, str):
                    return res == "active"
                if hasattr(res, "get_string"):
                    return res.get_string() == "active"
                return str(res) == "active"
        except Exception:
            pass

        # 2. Fallback to systemctl is-active
        try:
            import subprocess
            res = subprocess.run(
                ["systemctl", "is-active", "--quiet", "nvidia-powerd.service"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=1,
            )
            return res.returncode == 0
        except Exception:
            return False

    def _start_nvidia_powerd(self) -> bool:
        """Attempt to start nvidia-powerd.service silently."""
        # 1. Try DBus systemd Manager StartUnit
        try:
            import gi
            gi.require_version("Gio", "2.0")
            gi.require_version("GLib", "2.0")
            from gi.repository import Gio, GLib
            bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
            proxy = Gio.DBusProxy.new_sync(
                bus,
                Gio.DBusProxyFlags.NONE,
                None,
                "org.freedesktop.systemd1",
                "/org/freedesktop/systemd1",
                "org.freedesktop.systemd1.Manager",
                None,
            )
            proxy.call_sync(
                "StartUnit",
                GLib.Variant("(ss)", ("nvidia-powerd.service", "replace")),
                Gio.DBusCallFlags.NONE,
                1000,
                None,
            )
            return True
        except Exception:
            pass

        # 2. Fallback to systemctl start
        try:
            import subprocess
            res = subprocess.run(
                ["systemctl", "start", "nvidia-powerd.service"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=2,
            )
            return res.returncode == 0
        except Exception:
            return False

    def _check_smartshift_supported(self) -> bool:
        """Check if discrete AMD GPU supports SmartShift."""
        if self._vendor != "amd" or not self._device:
            return False
        return (
            (self._device / "smartshift_bias").exists()
            or (self._device / "smartshift_apu_power").exists()
            or (self._device / "smartshift_dgpu_power").exists()
        )

    def _read_smartshift_bias(self) -> Optional[int]:
        """Read smartshift_bias from AMD dGPU sysfs."""
        if self._vendor != "amd" or not self._device:
            return None
        path = self._device / "smartshift_bias"
        try:
            text = path.read_text().strip()
            return int(text)
        except (OSError, ValueError, IOError):
            return None

    def sync_power(self, fan_profile_id: int) -> bool:
        """Apply GPU power tuning matching the selected fan profile."""
        if not self.is_available:
            return True

        # 1. NVIDIA Dynamic Boost automation
        if self._vendor == "nvidia":
            # For Gaming (3) or Performance (2), ensure nvidia-powerd is running
            if fan_profile_id in (2, 3):
                if self._check_dynamic_boost_supported() and not self._check_dynamic_boost_active():
                    return self._start_nvidia_powerd()
            return True

        # 2. AMD SmartShift automation
        if self._vendor == "amd":
            if not self._device:
                return True
            bias_path = self._device / "smartshift_bias"
            if bias_path.exists() and os.access(str(bias_path), os.W_OK):
                # 3 (Gaming) -> 100, 2 (Performance) -> 50, 1 (Balanced) -> 0, 0 (Quiet) -> -50
                bias_val = {3: 100, 2: 50, 1: 0, 0: -50}.get(fan_profile_id, 0)
                try:
                    bias_path.write_text(f"{bias_val}\n")
                    return True
                except (OSError, IOError):
                    return False
            return True

        return True


# Global helper instance
_monitor = NvidiaGpuMonitor()


def gpu_status_text(state: GpuState) -> str:
    """Return a descriptive status string for a GpuState, e.g. 'Asleep (D3cold)'."""
    if not state.present:
        return "Not present"
    status = state.status
    power = state.power_state
    if status == "suspended":
        return f"Asleep ({power})" if power else "Asleep"
    if status == "active":
        return f"Awake ({power})" if power else "Awake"
    if power:
        return f"Unknown ({power})"
    return "Unknown"


def gpu_short_status_text(state: GpuState) -> str:
    """Return a short status label: 'Asleep', 'Awake', 'Unknown', or 'Not present'."""
    if not state.present:
        return "Not present"
    return {"suspended": "Asleep", "active": "Awake"}.get(state.status or "", "Unknown")


# Tray icon keys per (vendor, awake). Distinct files (not runtime rewrites)
# so indicator hosts refresh reliably. Unknown vendors fall back to plain.
GPU_ICON_KEYS = {
    ("nvidia", True): "gigamate-nvidia",
    ("amd", True): "gigamate-amd",
}


def gpu_icon_key(state: GpuState) -> str:
    """Map a GpuState to a tray icon key (plain when not awake/absent)."""
    try:
        if not state.present or state.status != "active" or not state.vendor:
            return "gigamate"
        return GPU_ICON_KEYS.get((state.vendor, True), "gigamate")
    except Exception:
        return "gigamate"


def get_gpu_state() -> GpuState:
    """Read the current discrete GPU power state (sysfs only, never wakes it)."""
    return _monitor.read_state()


def sync_gpu_power(fan_profile_id: int) -> bool:
    """Synchronize GPU power features (Dynamic Boost / SmartShift) with the profile.

    Args:
        fan_profile_id: 0 (Quiet), 1 (Balanced), 2 (Performance), 3 (Gaming)

    Returns:
        True on success or if no action required; False on failure.
        Never raises exceptions (graceful degradation).
    """
    try:
        return _monitor.sync_power(fan_profile_id)
    except Exception:
        return False
