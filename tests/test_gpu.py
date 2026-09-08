"""Tests for the discrete GPU power state monitor (gpu.py)."""

from gigamate.gpu import (
    GpuState,
    NvidiaGpuMonitor,
    gpu_status_text,
    gpu_short_status_text,
    get_gpu_state,
)
from gigamate import gpu as gpu_module
from gigamate.cli import cmd_gpu_status
from gigamate import cli as cli_module


def _build_pci_tree(tmp_path, devices):
    """Create a fake PCI sysfs root.

    Args:
        devices: dict mapping BDF -> dict of relative path -> file content.
    """
    root = tmp_path / "pci"
    for bdf, files in devices.items():
        dev = root / bdf
        dev.mkdir(parents=True)
        for rel, content in files.items():
            p = dev / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content)
    return root


def _nvidia_gpu(tmp_path, status="suspended", power_state="D3cold"):
    return _build_pci_tree(tmp_path, {
        "0000:64:00.0": {
            "vendor": "0x10de\n",
            "class": "0x030000\n",
            "power/runtime_status": f"{status}\n",
            "power_state": f"{power_state}\n",
        },
    })


def _amd_tree(tmp_path, boot_vga="0", status="active", power_state="D0",
              bdf="0000:65:00.0", cls="0x030000\n", extra=None):
    files = {
        "vendor": "0x1002\n",
        "class": cls,
        "boot_vga": f"{boot_vga}\n",
        "power/runtime_status": f"{status}\n",
        "power_state": f"{power_state}\n",
    }
    if extra:
        files.update(extra)
    return _build_pci_tree(tmp_path, {bdf: files})


class TestNvidiaGpuMonitor:
    def test_detect_nvidia(self, tmp_path):
        root = _nvidia_gpu(tmp_path)
        mon = NvidiaGpuMonitor(pci_sysfs=root)
        assert mon.detect() is True
        assert mon.is_available is True

    def test_read_state_suspended(self, tmp_path):
        root = _nvidia_gpu(tmp_path, status="suspended", power_state="D3cold")
        mon = NvidiaGpuMonitor(pci_sysfs=root)
        state = mon.read_state()
        assert state.present is True
        assert state.status == "suspended"
        assert state.power_state == "D3cold"

    def test_read_state_active(self, tmp_path):
        root = _nvidia_gpu(tmp_path, status="active", power_state="D0")
        mon = NvidiaGpuMonitor(pci_sysfs=root)
        state = mon.read_state()
        assert state.present is True
        assert state.status == "active"
        assert state.power_state == "D0"

    def test_picks_display_not_audio(self, tmp_path):
        # Same vendor, but the audio function (class 0x04) must be skipped.
        root = _build_pci_tree(tmp_path, {
            "0000:64:00.0": {"vendor": "0x10de\n", "class": "0x030000\n"},
            "0000:64:00.1": {"vendor": "0x10de\n", "class": "0x040300\n"},
        })
        mon = NvidiaGpuMonitor(pci_sysfs=root)
        assert mon.is_available is True
        dev = mon._device
        assert dev is not None
        assert dev.name == "0000:64:00.0"

    def test_no_nvidia(self, tmp_path):
        root = _build_pci_tree(tmp_path, {
            "0000:65:00.0": {"vendor": "0x1002\n", "class": "0x030000\n"},  # AMD iGPU
        })
        mon = NvidiaGpuMonitor(pci_sysfs=root)
        assert mon.is_available is False
        assert mon.read_state().present is False

    def test_missing_power_files_graceful(self, tmp_path):
        root = _build_pci_tree(tmp_path, {
            "0000:64:00.0": {"vendor": "0x10de\n", "class": "0x030000\n"},
        })
        mon = NvidiaGpuMonitor(pci_sysfs=root)
        state = mon.read_state()
        assert state.present is True
        assert state.status is None
        assert state.power_state is None

    def test_empty_sysfs(self, tmp_path):
        root = tmp_path / "empty"
        root.mkdir()
        mon = NvidiaGpuMonitor(pci_sysfs=root)
        assert mon.is_available is False

    def test_nonexistent_sysfs(self, tmp_path):
        mon = NvidiaGpuMonitor(pci_sysfs=tmp_path / "nope")
        assert mon.is_available is False


class TestAmdDiscreteGpu:
    def test_detect_amd_dgpu(self, tmp_path):
        root = _amd_tree(tmp_path)
        mon = NvidiaGpuMonitor(pci_sysfs=root)
        assert mon.is_available is True
        state = mon.read_state()
        assert state.present is True
        assert state.vendor == "amd"
        assert state.status == "active"

    def test_amd_dgpu_suspended(self, tmp_path):
        root = _amd_tree(tmp_path, status="suspended", power_state="D3cold")
        mon = NvidiaGpuMonitor(pci_sysfs=root)
        state = mon.read_state()
        assert (state.present, state.vendor, state.status) == (True, "amd", "suspended")

    def test_amd_igpu_ignored(self, tmp_path):
        # Boot display (integrated graphics) must never count as dGPU.
        root = _amd_tree(tmp_path, boot_vga="1")
        mon = NvidiaGpuMonitor(pci_sysfs=root)
        assert mon.is_available is False
        assert mon.read_state().present is False

    def test_amd_missing_boot_vga_conservative(self, tmp_path):
        # No boot_vga flag -> do not claim (avoids false red dot).
        root = _build_pci_tree(tmp_path, {
            "0000:65:00.0": {"vendor": "0x1002\n", "class": "0x030000\n"},
        })
        mon = NvidiaGpuMonitor(pci_sysfs=root)
        assert mon.is_available is False

    def test_mixed_igpu_picks_dgpu(self, tmp_path):
        root = _build_pci_tree(tmp_path, {
            "0000:65:00.0": {"vendor": "0x1002\n", "class": "0x030000\n",
                             "boot_vga": "1\n",
                             "power/runtime_status": "active\n"},
            "0000:66:00.0": {"vendor": "0x1002\n", "class": "0x030000\n",
                             "boot_vga": "0\n",
                             "power/runtime_status": "suspended\n",
                             "power_state": "D3cold\n"},
        })
        mon = NvidiaGpuMonitor(pci_sysfs=root)
        state = mon.read_state()
        assert state.present is True
        assert state.vendor == "amd"
        assert state.status == "suspended"
        assert mon._device is not None
        assert mon._device.name == "0000:66:00.0"

    def test_nvidia_wins_over_amd_igpu(self, tmp_path):
        root = _build_pci_tree(tmp_path, {
            "0000:64:00.0": {"vendor": "0x10de\n", "class": "0x030000\n",
                             "power/runtime_status": "active\n",
                             "power_state": "D0\n"},
            "0000:65:00.0": {"vendor": "0x1002\n", "class": "0x038000\n",
                             "boot_vga": "1\n"},
        })
        mon = NvidiaGpuMonitor(pci_sysfs=root)
        state = mon.read_state()
        assert (state.present, state.vendor) == (True, "nvidia")

    def test_amd_audio_skipped(self, tmp_path):
        root = _build_pci_tree(tmp_path, {
            "0000:65:00.0": {"vendor": "0x1002\n", "class": "0x030000\n",
                             "boot_vga": "0\n"},
            "0000:65:00.1": {"vendor": "0x1002\n", "class": "0x040300\n"},
        })
        mon = NvidiaGpuMonitor(pci_sysfs=root)
        assert mon.is_available is True
        assert mon._device is not None
        assert mon._device.name == "0000:65:00.0"

    def test_nvidia_state_has_vendor(self, tmp_path):
        root = _nvidia_gpu(tmp_path, status="active", power_state="D0")
        mon = NvidiaGpuMonitor(pci_sysfs=root)
        assert mon.read_state().vendor == "nvidia"


class TestGpuIconKey:
    def test_mapping(self):
        from gigamate.gpu import gpu_icon_key
        assert gpu_icon_key(GpuState(present=False)) == "gigamate"
        assert gpu_icon_key(
            GpuState(present=True, status="suspended", vendor="nvidia")) == "gigamate"
        assert gpu_icon_key(
            GpuState(present=True, status="active", vendor="nvidia")) == "gigamate-nvidia"
        assert gpu_icon_key(
            GpuState(present=True, status="active", vendor="amd")) == "gigamate-amd"
        assert gpu_icon_key(
            GpuState(present=True, status="active", vendor="intel")) == "gigamate"
        assert gpu_icon_key(
            GpuState(present=True, vendor="nvidia")) == "gigamate"

    def test_icon_paths_resolve(self):
        from gigamate.paths import ICON_PATHS
        for key, path in ICON_PATHS.items():
            assert path, f"empty path for {key}"
            if key == "gigamate":
                continue
            # Variants must resolve to distinct files so hosts refresh.
            assert path != ICON_PATHS["gigamate"]


class TestGpuStatusText:
    def test_not_present(self):
        assert gpu_status_text(GpuState(present=False)) == "Not present"

    def test_suspended(self):
        state = GpuState(present=True, status="suspended", power_state="D3cold")
        assert gpu_status_text(state) == "Asleep (D3cold)"

    def test_suspended_no_power(self):
        assert gpu_status_text(GpuState(present=True, status="suspended")) == "Asleep"

    def test_active(self):
        state = GpuState(present=True, status="active", power_state="D0")
        assert gpu_status_text(state) == "Awake (D0)"

    def test_active_no_power(self):
        assert gpu_status_text(GpuState(present=True, status="active")) == "Awake"

    def test_unknown_with_power(self):
        state = GpuState(present=True, power_state="D3hot")
        assert gpu_status_text(state) == "Unknown (D3hot)"

    def test_unknown(self):
        assert gpu_status_text(GpuState(present=True)) == "Unknown"


class TestGpuShortStatusText:
    def test_labels(self):
        assert gpu_short_status_text(GpuState(present=False)) == "Not present"
        assert gpu_short_status_text(GpuState(present=True, status="suspended")) == "Asleep"
        assert gpu_short_status_text(GpuState(present=True, status="active")) == "Awake"
        assert gpu_short_status_text(GpuState(present=True)) == "Unknown"


class TestGetGpuState:
    def test_uses_module_monitor(self, tmp_path, monkeypatch):
        root = _nvidia_gpu(tmp_path, status="active", power_state="D0")
        monkeypatch.setattr(gpu_module, "_monitor", NvidiaGpuMonitor(pci_sysfs=root))
        state = get_gpu_state()
        assert state.present is True
        assert state.status == "active"
        assert state.power_state == "D0"


class TestCmdGpuStatus:
    def test_no_gpu_prints_nothing(self, capsys, monkeypatch):
        monkeypatch.setattr(cli_module, "get_gpu_state", lambda: GpuState(present=False))
        cmd_gpu_status(None)
        out = capsys.readouterr().out
        assert out == ""

    def test_gpu_present_prints_state(self, capsys, monkeypatch):
        state = GpuState(present=True, status="suspended", power_state="D3cold")
        monkeypatch.setattr(cli_module, "get_gpu_state", lambda: state)
        cmd_gpu_status(None)
        out = capsys.readouterr().out
        assert "Asleep (D3cold)" in out
