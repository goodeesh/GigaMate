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
