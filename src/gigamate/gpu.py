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


@dataclass
class GpuState:
    """Snapshot of the discrete GPU power state.

    All fields except ``present`` are Optional — partial sysfs support is
    handled gracefully.
    """

    present: bool = False
    status: Optional[str] = None  # "active" or "suspended"
    power_state: Optional[str] = None  # "D0", "D3hot", "D3cold", ...


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

    @property
    def _sysfs_root(self) -> Path:
        return self._pci_sysfs or PCI_SYSFS

    def detect(self) -> bool:
        """Find the NVIDIA display device. Returns True if found."""
        self._device = self._find_device()
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

        state = GpuState(present=True)
        assert self._device is not None  # guaranteed by is_available
        state.status = self._read_text(self._device / "power" / "runtime_status")
        state.power_state = self._read_text(self._device / "power_state")
        return state

    def _find_device(self) -> Optional[Path]:
        """Scan the PCI sysfs root for the NVIDIA display controller."""
        try:
            root = self._sysfs_root
            if not root.is_dir():
                return None
            for entry in root.iterdir():
                if not entry.is_dir():
                    continue
                try:
                    vendor = (entry / "vendor").read_text().strip()
                    cls = (entry / "class").read_text().strip()
                except OSError:
                    continue
                if vendor == NVIDIA_VENDOR and cls.startswith("0x03"):
                    return entry
        except OSError:
            return None
        return None

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


def get_gpu_state() -> GpuState:
    """Read the current discrete GPU power state (sysfs only, never wakes it)."""
    return _monitor.read_state()
