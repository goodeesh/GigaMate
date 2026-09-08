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


class NvidiaGpuMonitor:
    """Monitor for the power state of an NVIDIA discrete GPU.

    Reads are sysfs-only and never wake the GPU.
    """

    def __init__(self, pci_sysfs: Optional[Path] = None) -> None:
        """Initialise monitor.

        Args:
            pci_sysfs: Override the PCI sysfs root (mainly for tests).
                        If None, the module-level ``PCI_SYSFS`` is used.
        """
        self._pci_sysfs: Optional[Path] = pci_sysfs
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
        """Whether an NVIDIA discrete GPU is present."""
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
