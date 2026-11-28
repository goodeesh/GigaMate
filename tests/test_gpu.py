"""Tests for the discrete GPU power state monitor (gpu.py)."""

from unittest.mock import patch

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

    def test_amd_apu_igpu_ignored_despite_boot_vga_zero(self, tmp_path):
        """Regression: an APU iGPU also reports boot_vga=0 on hybrid laptops.

        When the dGPU is the primary display, the iGPU is flagged non-boot too,
        so boot_vga alone would claim it as "the discrete GPU" — lighting a
        permanent indicator on a machine that has no discrete AMD GPU. amdgpu's
        own ``uma/`` marker is authoritative.
        """
        root = _amd_tree(tmp_path, boot_vga="0", cls="0x038000\n", extra={
            "mem_info_vram_total": "536870912\n",  # 512 MiB APU carve-out
        })
        (root / "0000:65:00.0" / "uma").mkdir()  # amdgpu APU marker
        mon = NvidiaGpuMonitor(pci_sysfs=root)
        assert mon.is_available is False
        assert mon.read_state().present is False

    def test_amd_small_vram_aperture_treated_as_integrated(self, tmp_path):
        """A carve-out sized VRAM aperture also means integrated, without uma/."""
        root = _amd_tree(tmp_path, boot_vga="0", extra={
            "mem_info_vram_total": "536870912\n",
        })
        mon = NvidiaGpuMonitor(pci_sysfs=root)
        assert mon.is_available is False

    def test_amd_dgpu_with_dedicated_vram_still_detected(self, tmp_path):
        """The hardening must not reject a real discrete Radeon board."""
        root = _amd_tree(tmp_path, boot_vga="0", extra={
            "mem_info_vram_total": str(8 * 1024 * 1024 * 1024),  # 8 GiB
        })
        mon = NvidiaGpuMonitor(pci_sysfs=root)
        assert mon.is_available is True
        assert mon.read_state().vendor == "amd"

    def test_amd_igpu_and_dgpu_together_picks_dgpu(self, tmp_path):
        """APU iGPU plus a real dGPU: the dGPU is the one that gets claimed."""
        root = _build_pci_tree(tmp_path, {
            "0000:08:00.0": {"vendor": "0x1002\n", "class": "0x038000\n",
                             "boot_vga": "1\n",
                             "mem_info_vram_total": "536870912\n",
                             "power/runtime_status": "active\n"},
            "0000:66:00.0": {"vendor": "0x1002\n", "class": "0x030000\n",
                             "boot_vga": "0\n",
                             "mem_info_vram_total": str(8 * 1024 * 1024 * 1024),
                             "power/runtime_status": "suspended\n",
                             "power_state": "D3cold\n"},
        })
        (root / "0000:08:00.0" / "uma").mkdir()
        mon = NvidiaGpuMonitor(pci_sysfs=root)
        state = mon.read_state()
        assert state.present is True
        assert mon._device is not None
        assert mon._device.name == "0000:66:00.0"

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
    def test_no_gpu_prints_integrated_message(self, capsys, monkeypatch):
        monkeypatch.setattr(cli_module, "get_gpu_state", lambda: GpuState(present=False))
        cmd_gpu_status(None)
        out = capsys.readouterr().out
        assert "No discrete GPU detected" in out

    def test_gpu_present_prints_state(self, capsys, monkeypatch):
        state = GpuState(present=True, status="suspended", power_state="D3cold")
        monkeypatch.setattr(cli_module, "get_gpu_state", lambda: state)
        cmd_gpu_status(None)
        out = capsys.readouterr().out
        assert "Asleep (D3cold)" in out

    def test_gpu_dynamic_boost_active_reporting(self, capsys, monkeypatch):
        state = GpuState(
            present=True,
            vendor="nvidia",
            status="active",
            power_state="D0",
            dynamic_boost_supported=True,
            dynamic_boost_active=True,
        )
        monkeypatch.setattr(cli_module, "get_gpu_state", lambda: state)
        cmd_gpu_status(None)
        out = capsys.readouterr().out
        assert "Dynamic Boost: Active (nvidia-powerd)" in out

    def test_gpu_dynamic_boost_inactive_reporting(self, capsys, monkeypatch):
        state = GpuState(
            present=True,
            vendor="nvidia",
            status="active",
            power_state="D0",
            dynamic_boost_supported=True,
            dynamic_boost_active=False,
        )
        monkeypatch.setattr(cli_module, "get_gpu_state", lambda: state)
        cmd_gpu_status(None)
        out = capsys.readouterr().out
        assert "Dynamic Boost: Inactive (run: systemctl enable --now nvidia-powerd)" in out

    def test_gpu_smartshift_reporting(self, capsys, monkeypatch):
        state = GpuState(
            present=True,
            vendor="amd",
            status="active",
            power_state="D0",
            smartshift_supported=True,
            smartshift_bias=100,
        )
        monkeypatch.setattr(cli_module, "get_gpu_state", lambda: state)
        cmd_gpu_status(None)
        out = capsys.readouterr().out
        assert "SmartShift:   Supported (bias: +100)" in out


class TestDynamicBoost:
    def test_dynamic_boost_supported_from_proc(self, tmp_path):
        root = _nvidia_gpu(tmp_path)
        proc = tmp_path / "proc" / "nvidia"
        power_file = proc / "gpus" / "0000:64:00.0" / "power"
        power_file.parent.mkdir(parents=True)
        power_file.write_text("Notebook Dynamic Boost:     Supported\n")

        mon = NvidiaGpuMonitor(pci_sysfs=root, proc_nvidia=proc)
        state = mon.read_state()
        assert state.dynamic_boost_supported is True

    def test_dynamic_boost_not_supported(self, tmp_path):
        root = _nvidia_gpu(tmp_path)
        proc = tmp_path / "proc" / "nvidia"
        proc.mkdir(parents=True)

        mon = NvidiaGpuMonitor(pci_sysfs=root, proc_nvidia=proc)
        # No /proc power file and no service unit/binary anywhere → unsupported.
        with patch("pathlib.Path.is_file", return_value=False), \
             patch("pathlib.Path.exists", return_value=False):
            assert mon._check_dynamic_boost_supported() is False

    def test_sync_power_nvidia_gaming_starts_powerd(self, tmp_path, monkeypatch):
        root = _nvidia_gpu(tmp_path)
        mon = NvidiaGpuMonitor(pci_sysfs=root)
        mon.detect()

        started = []
        monkeypatch.setattr(mon, "_check_dynamic_boost_supported", lambda: True)
        monkeypatch.setattr(mon, "_check_dynamic_boost_active", lambda: False)
        monkeypatch.setattr(mon, "_start_nvidia_powerd", lambda: started.append(True) or True)

        res = mon.sync_power(3)  # Gaming
        assert res is True
        assert len(started) == 1

    def test_sync_power_nvidia_already_active_noop(self, tmp_path, monkeypatch):
        root = _nvidia_gpu(tmp_path)
        mon = NvidiaGpuMonitor(pci_sysfs=root)
        mon.detect()

        started = []
        monkeypatch.setattr(mon, "_check_dynamic_boost_supported", lambda: True)
        monkeypatch.setattr(mon, "_check_dynamic_boost_active", lambda: True)
        monkeypatch.setattr(mon, "_start_nvidia_powerd", lambda: started.append(True) or True)

        res = mon.sync_power(3)  # Gaming
        assert res is True
        assert len(started) == 0

    def test_sync_power_quiet_noop(self, tmp_path, monkeypatch):
        root = _nvidia_gpu(tmp_path)
        mon = NvidiaGpuMonitor(pci_sysfs=root)
        mon.detect()

        started = []
        monkeypatch.setattr(mon, "_start_nvidia_powerd", lambda: started.append(True) or True)
        res = mon.sync_power(0)  # Quiet
        assert res is True
        assert len(started) == 0


class TestAmdSmartShift:
    def test_smartshift_supported_and_bias_read(self, tmp_path):
        extra = {"smartshift_bias": "25\n"}
        root = _amd_tree(tmp_path, extra=extra)
        mon = NvidiaGpuMonitor(pci_sysfs=root)
        state = mon.read_state()
        assert state.smartshift_supported is True
        assert state.smartshift_bias == 25

    def test_sync_power_amd_gaming_sets_bias(self, tmp_path):
        extra = {"smartshift_bias": "0\n"}
        root = _amd_tree(tmp_path, extra=extra)
        mon = NvidiaGpuMonitor(pci_sysfs=root)
        mon.detect()
        assert mon.sync_power(3) is True
        bias_file = mon._device / "smartshift_bias"
        assert bias_file.read_text().strip() == "100"

    def test_sync_power_amd_quiet_sets_bias(self, tmp_path):
        extra = {"smartshift_bias": "0\n"}
        root = _amd_tree(tmp_path, extra=extra)
        mon = NvidiaGpuMonitor(pci_sysfs=root)
        mon.detect()
        assert mon.sync_power(0) is True
        bias_file = mon._device / "smartshift_bias"
        assert bias_file.read_text().strip() == "-50"

    def test_amd_3d_controller_without_boot_vga(self, tmp_path):
        root = tmp_path / "pci"
        dev = root / "0000:03:00.0"
        dev.mkdir(parents=True)
        (dev / "vendor").write_text("0x1002\n")
        (dev / "class").write_text("0x030200\n")
        mon = NvidiaGpuMonitor(pci_sysfs=root)
        gpu, vendor = mon._find_device()
        assert gpu == dev
        assert vendor == "amd"

    def test_dynamic_boost_unsupported_proc_file(self, tmp_path):
        root = _nvidia_gpu(tmp_path)
        proc = tmp_path / "proc_nvidia"
        gpu_power = proc / "gpus" / "0000:01:00.0" / "power"
        gpu_power.parent.mkdir(parents=True)
        gpu_power.write_text("Notebook Dynamic Boost:     Not Supported\n")
        mon = NvidiaGpuMonitor(pci_sysfs=root, proc_nvidia=proc)
        mon.detect()
        assert mon._check_dynamic_boost_supported() is False


class TestDgpuGateFastPaths:
    def test_dgpu_gate_is_gated_off_fast_path_and_self_heal(self, tmp_path):
        from gigamate import dgpu_gate

        gate_state = {"gated": True, "bdf": "0000:01:00.0"}
        with patch.object(dgpu_gate, "find_dgpu_topology") as find_mock:
            assert dgpu_gate.is_gated_off(pci_sysfs=tmp_path, gate_state=gate_state) is True
            find_mock.assert_not_called()

            (tmp_path / "0000:01:00.0").mkdir()
            assert dgpu_gate.is_gated_off(pci_sysfs=tmp_path, gate_state=gate_state) is False
            find_mock.assert_not_called()

    def test_dgpu_gate_get_status_single_scan(self, tmp_path):
        from gigamate import dgpu_gate

        with patch.object(dgpu_gate, "PCI_SYSFS", tmp_path), \
             patch.object(dgpu_gate, "read_gate_state",
                          return_value={"gated": True, "bdf": "0000:01:00.0", "vendor": "nvidia"}), \
             patch.object(dgpu_gate, "get_gate_config", return_value=None), \
             patch.object(dgpu_gate, "acpi_eval_backend", return_value="module"), \
             patch.object(dgpu_gate, "find_dgpu_topology") as find_mock:
            st = dgpu_gate.get_status(check_helper=False)
            assert st["gated"] is True
            assert st["bdf"] == "0000:01:00.0"
            find_mock.assert_not_called()

        topo = dgpu_gate.DgpuTopology(bdf="0000:01:00.0", vendor="nvidia")
        with patch.object(dgpu_gate, "PCI_SYSFS", tmp_path), \
             patch.object(dgpu_gate, "read_gate_state", return_value={}), \
             patch.object(dgpu_gate, "get_gate_config", return_value=None), \
             patch.object(dgpu_gate, "acpi_eval_backend", return_value="module"), \
             patch.object(dgpu_gate, "connected_dgpu_displays", return_value=[]), \
             patch.object(dgpu_gate, "is_gpu_wedged", return_value=False), \
             patch.object(dgpu_gate, "find_dgpu_topology", return_value=topo) as find_mock:
            st = dgpu_gate.get_status(check_helper=False)
            assert st["gated"] is False
            assert st["bdf"] == "0000:01:00.0"
            assert find_mock.call_count == 1

    def test_dgpu_gate_get_status_with_autodiscovered_gate_config(self, tmp_path):
        from gigamate import dgpu_gate

        cfg = dgpu_gate.DgpuGateConfig(
            path="\\_SB.PCI0.GPP9.PG00",
            method="_OFF",
            quirks=["remove_before_off"],
            source="sysfs:power_resource",
        )
        topo = dgpu_gate.DgpuTopology(bdf="0000:01:00.0", vendor="nvidia")
        with patch.object(dgpu_gate, "PCI_SYSFS", tmp_path), \
             patch.object(dgpu_gate, "read_gate_state", return_value={}), \
             patch.object(dgpu_gate, "resolve_gate_config", return_value=(cfg, None)), \
             patch.object(dgpu_gate, "acpi_eval_backend", return_value="module"), \
             patch.object(dgpu_gate, "connected_dgpu_displays", return_value=[]), \
             patch.object(dgpu_gate, "is_gpu_wedged", return_value=False), \
             patch.object(dgpu_gate, "find_dgpu_topology", return_value=topo):
            st = dgpu_gate.get_status(check_helper=False)
            assert st["gate_config"]["path"] == "\\_SB.PCI0.GPP9.PG00"
            assert st["gate_config"]["method"] == "_OFF"
            assert st["profile_source"] == "sysfs:power_resource"
            assert st["experimental"] is False

    def test_should_pause_tuning_eco_auto_battery(self):
        from gigamate import dgpu_gate

        with patch.object(dgpu_gate, "is_gated_off", return_value=False), \
             patch.object(dgpu_gate, "is_supported", return_value=True):
            with patch.object(dgpu_gate, "configured_mode", return_value=dgpu_gate.MODE_ECO):
                assert dgpu_gate.should_pause_tuning(ac_online=False) is True
                assert dgpu_gate.should_pause_tuning(ac_online=True) is False
            with patch.object(dgpu_gate, "configured_mode", return_value=dgpu_gate.MODE_AUTO):
                assert dgpu_gate.should_pause_tuning(ac_online=False) is True
                assert dgpu_gate.should_pause_tuning(ac_online=True) is False
            with patch.object(dgpu_gate, "configured_mode", return_value=dgpu_gate.MODE_OPTIMUS):
                assert dgpu_gate.should_pause_tuning(ac_online=False) is False

    def test_watcher_tick_boot_on_battery_suspended_and_active_fallback(self, tmp_path):
        from unittest.mock import MagicMock
        from gigamate import dgpu_gate

        bdf = "0000:01:00.0"
        pwr = tmp_path / bdf / "power"
        pwr.mkdir(parents=True)
        (pwr / "runtime_status").write_text("suspended")

        topo = dgpu_gate.DgpuTopology(bdf=bdf, vendor="nvidia")
        batt = MagicMock()
        batt.is_ac_online.return_value = False

        for mode in (dgpu_gate.MODE_ECO, dgpu_gate.MODE_AUTO):
            dgpu_gate._watcher_state.update({
                "last_ac": None,
                "last_event_mtime": None,
                "last_mode": None,
                "last_attempt_at": 0.0,
                "last_attempt_rstatus": None,
                "last_policy_publish": 0.0,
                "awaiting_confirm_at": 0.0,
                "supported_cache": None,
                "supported_checked_at": 0.0,
                "want_off_since": 0.0,
                "boot_prompted": False,
            })
            (pwr / "runtime_status").write_text("suspended")
            with patch.object(dgpu_gate, "PCI_SYSFS", tmp_path), \
                 patch.object(dgpu_gate, "configured_mode", return_value=mode), \
                 patch.object(dgpu_gate, "get_battery_manager", return_value=batt), \
                 patch.object(dgpu_gate, "_ac_event_mtime", return_value=None), \
                 patch.object(dgpu_gate, "is_gated_off", return_value=False), \
                 patch.object(dgpu_gate, "is_supported", return_value=True), \
                 patch.object(dgpu_gate, "is_confirm_dialog_open", return_value=False), \
                 patch.object(dgpu_gate, "is_transition_in_progress", return_value=False), \
                 patch.object(dgpu_gate, "find_dgpu_topology", return_value=topo), \
                 patch.object(dgpu_gate, "connected_dgpu_displays", return_value=[]), \
                 patch.object(dgpu_gate, "is_gpu_wedged", return_value=False), \
                 patch.object(dgpu_gate, "boot_settle_remaining", return_value=0.0), \
                 patch.object(dgpu_gate, "sync_policy") as sync_mock:
                dgpu_gate.watcher_tick(now=100.0)
                sync_mock.assert_called_once_with(reason="watcher_gate_off")

        # Active GPU on boot-on-battery: after 12s with empty holders -> gate_off(allow_active=True)
        (pwr / "runtime_status").write_text("active")
        dgpu_gate._watcher_state.update({
            "last_ac": None,
            "last_event_mtime": None,
            "last_mode": None,
            "last_attempt_at": 0.0,
            "last_attempt_rstatus": None,
            "last_policy_publish": 0.0,
            "awaiting_confirm_at": 0.0,
            "supported_cache": None,
            "supported_checked_at": 0.0,
            "want_off_since": 0.0,
            "boot_prompted": False,
        })
        with patch.object(dgpu_gate, "PCI_SYSFS", tmp_path), \
             patch.object(dgpu_gate, "configured_mode", return_value=dgpu_gate.MODE_AUTO), \
             patch.object(dgpu_gate, "get_battery_manager", return_value=batt), \
             patch.object(dgpu_gate, "_ac_event_mtime", return_value=None), \
             patch.object(dgpu_gate, "is_gated_off", return_value=False), \
             patch.object(dgpu_gate, "is_supported", return_value=True), \
             patch.object(dgpu_gate, "is_confirm_dialog_open", return_value=False), \
             patch.object(dgpu_gate, "is_transition_in_progress", return_value=False), \
             patch.object(dgpu_gate, "find_dgpu_topology", return_value=topo), \
             patch.object(dgpu_gate, "connected_dgpu_displays", return_value=[]), \
             patch.object(dgpu_gate, "is_gpu_wedged", return_value=False), \
             patch.object(dgpu_gate, "boot_settle_remaining", return_value=0.0), \
             patch.object(dgpu_gate, "list_gpu_holder_processes", return_value=[]), \
             patch.object(dgpu_gate, "_publish_policy"), \
             patch.object(dgpu_gate, "gate_off") as gate_off_mock:
            dgpu_gate.watcher_tick(now=100.0)
            gate_off_mock.assert_not_called()
            dgpu_gate.watcher_tick(now=113.0)
            gate_off_mock.assert_called_once_with(allow_active=True)

        # Active GPU on boot-on-battery: after 12s with non-empty holders -> launch_confirm_dialog once
        dgpu_gate._watcher_state.update({
            "last_ac": None,
            "last_event_mtime": None,
            "last_mode": None,
            "last_attempt_at": 0.0,
            "last_attempt_rstatus": None,
            "last_policy_publish": 0.0,
            "awaiting_confirm_at": 0.0,
            "supported_cache": None,
            "supported_checked_at": 0.0,
            "want_off_since": 0.0,
            "boot_prompted": False,
        })
        with patch.object(dgpu_gate, "PCI_SYSFS", tmp_path), \
             patch.object(dgpu_gate, "configured_mode", return_value=dgpu_gate.MODE_ECO), \
             patch.object(dgpu_gate, "get_battery_manager", return_value=batt), \
             patch.object(dgpu_gate, "_ac_event_mtime", return_value=None), \
             patch.object(dgpu_gate, "is_gated_off", return_value=False), \
             patch.object(dgpu_gate, "is_supported", return_value=True), \
             patch.object(dgpu_gate, "is_confirm_dialog_open", return_value=False), \
             patch.object(dgpu_gate, "is_transition_in_progress", return_value=False), \
             patch.object(dgpu_gate, "find_dgpu_topology", return_value=topo), \
             patch.object(dgpu_gate, "connected_dgpu_displays", return_value=[]), \
             patch.object(dgpu_gate, "is_gpu_wedged", return_value=False), \
             patch.object(dgpu_gate, "boot_settle_remaining", return_value=0.0), \
             patch.object(dgpu_gate, "list_gpu_holder_processes", return_value=[{"pid": 1234, "comm": "python"}]) as holders_mock, \
             patch.object(dgpu_gate, "_publish_policy"), \
             patch.object(dgpu_gate, "launch_confirm_dialog") as dialog_mock, \
             patch.dict("os.environ", {"DISPLAY": ":0"}):
            dgpu_gate.watcher_tick(now=200.0)
            dialog_mock.assert_not_called()
            dgpu_gate.watcher_tick(now=213.0)
            holders_mock.assert_called_once_with()
            dialog_mock.assert_called_once_with(
                trigger="boot_battery",
                target_mode=dgpu_gate.MODE_ECO,
                prev_mode=dgpu_gate.MODE_ECO,
            )
            dgpu_gate.watcher_tick(now=220.0)
            assert dialog_mock.call_count == 1

    def test_dgpu_watcher_tick_skips_nvml_when_should_pause_tuning(self):
        from gigamate import dgpu_gate, dgpu_tune

        watcher = dgpu_tune.DgpuWatcher()
        with patch.object(dgpu_gate, "read_gate_state", return_value={"gated": False, "bdf": "0000:01:00.0"}), \
             patch.object(dgpu_gate, "should_pause_tuning", return_value=True), \
             patch.object(dgpu_tune, "find_nvidia_bdf") as find_mock:
            watcher.tick(now=100.0)
            find_mock.assert_not_called()
            assert watcher.status()["runtime"] == "gated_off"


def test_nvidia_sync_power_skips_powerd_when_dgpu_gate_pauses_tuning(tmp_path):
    from gigamate import dgpu_gate

    root = _nvidia_gpu(tmp_path, status="active", power_state="D0")
    mon = NvidiaGpuMonitor(pci_sysfs=root)
    with patch.object(dgpu_gate, "is_gated_off", return_value=False), \
         patch.object(dgpu_gate, "should_pause_tuning", return_value=True), \
         patch.object(mon, "_check_dynamic_boost_supported", return_value=True), \
         patch.object(mon, "_check_dynamic_boost_active", return_value=False), \
         patch.object(mon, "_start_nvidia_powerd") as start_mock:
        assert mon.sync_power(3) is True
        start_mock.assert_not_called()


def test_tray_automation_waits_for_dgpu_wake_on_ac_and_clamps_battery_high_power():
    from gigamate import daemon as daemon_module, dgpu_gate

    d = daemon_module.GigaMateDaemon()
    d._last_ac_online = False
    d._current_acpi_profile = 0
    d.get_discovered_profiles = lambda: {0: "Quiet", 1: "Balanced", 2: "Performance", 3: "Gaming"}

    cfg = {
        "ac_profile_mode": "switch",
        "ac_profile_id": 2,
        "battery_profile_id": 3,
        "disabled_profiles": [],
    }
    allowed_state = {"val": False}

    with patch.object(daemon_module, "load_config", return_value=cfg), \
         patch.object(daemon_module, "power_config_mtime_token", return_value=1), \
         patch.object(daemon_module, "detect_power_managers", return_value={"conflicts": [], "has_external_ac_switching": False}), \
         patch.object(dgpu_gate, "high_power_profiles_allowed", side_effect=lambda: allowed_state["val"]), \
         patch.object(dgpu_gate, "configured_mode", return_value=dgpu_gate.MODE_AUTO), \
         patch.object(daemon_module.hardware, "apply_profile") as apply_mock:
        # Transition False -> True while dGPU is still asleep: should defer, not apply
        d._run_profile_automation(True)
        apply_mock.assert_not_called()
        assert d._pending_ac_profile_target is not None

        # Next tick after dGPU wakes: should apply profile 2
        d._last_ac_online = True
        allowed_state["val"] = True
        d._run_profile_automation(True)
        apply_mock.assert_called_once_with(2, cfg=cfg, sync_system=True)
        assert d._pending_ac_profile_target is None

        # Transition True -> False on battery with battery_profile_id=3 under auto mode: clamps to 0 (Quiet)
        apply_mock.reset_mock()
        d._run_profile_automation(False)
        apply_mock.assert_called_once_with(0, cfg=cfg, sync_system=True)


def test_dgpu_gate_helper_wakes_bridge_and_retrains_degraded_pcie_link(tmp_path):
    """_wake_bridge_to_d0 forces D0 and _pcie_retrain_link sets LNKCTL bit 5."""
    import importlib.machinery
    import importlib.util
    from pathlib import Path

    helper_path = Path(__file__).resolve().parents[1] / "data" / "gigamate-dgpu-gate"
    loader = importlib.machinery.SourceFileLoader("gigamate_dgpu_gate_helper", str(helper_path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)

    pci_root = tmp_path / "pci"
    bdf = "0000:00:03.1"
    bdir = pci_root / bdf
    (bdir / "power").mkdir(parents=True)
    (bdir / "power" / "control").write_text("auto\n", encoding="utf-8")
    (bdir / "power" / "runtime_status").write_text("active\n", encoding="utf-8")
    (bdir / "power_state").write_text("D0\n", encoding="utf-8")
    (bdir / "current_link_width").write_text("1\n", encoding="utf-8")
    (bdir / "max_link_width").write_text("8\n", encoding="utf-8")

    # Build minimal 256-byte PCI config space:
    # cap_ptr at 0x34 -> 0x50 (non-PCIe cap 0x01 -> next 0x60) -> 0x60 (PCIe cap 0x10)
    cfg = bytearray(256)
    cfg[0x34] = 0x50
    cfg[0x50] = 0x01
    cfg[0x51] = 0x60
    cfg[0x60] = 0x10
    cfg[0x61] = 0x00
    # LNKCTL at 0x60 + 0x10 = 0x70, initial value 0x0040
    cfg[0x70] = 0x40
    cfg[0x71] = 0x00
    (bdir / "config").write_bytes(bytes(cfg))

    with patch.object(mod, "_PCI_DEVICES", pci_root):
        assert mod._wake_bridge_to_d0(bdf, wait_sec=0.2) is True
        assert (bdir / "power" / "control").read_text(encoding="utf-8").strip() == "on"

        mod._ensure_full_pcie_link_width(bdf, "0000:64:00.0")
        updated = (bdir / "config").read_bytes()
        lnkctl = int.from_bytes(updated[0x70:0x72], "little")
        assert lnkctl & 0x0020 == 0x0020  # Retrain Link bit set
        assert lnkctl & 0x0040 == 0x0040  # Existing bits preserved

        # SBR safety: must NOT touch reset_subordinate when GPU has a bound driver
        gdir = pci_root / "0000:64:00.0"
        gdir.mkdir(parents=True)
        (gdir / "driver").mkdir()
        sbr = bdir / "reset_subordinate"
        sbr.write_text("0\n", encoding="utf-8")
        mod._ensure_full_pcie_link_width(bdf, "0000:64:00.0", allow_sbr=True)
        assert sbr.read_text(encoding="utf-8").strip() == "0"

        # When GPU is unbound, allow_sbr=True triggers reset_subordinate
        (gdir / "driver").rmdir()
        mod._ensure_full_pcie_link_width(bdf, "0000:64:00.0", allow_sbr=True)
        assert sbr.read_text(encoding="utf-8").strip() == "1"

    # _pause_pci_autoprobe must always restore 1 even on exception
    ap = tmp_path / "drivers_autoprobe"
    ap.write_text("1\n", encoding="utf-8")
    with patch.object(mod, "_PCI_DRIVERS_AUTOPROBE", ap):
        try:
            with mod._pause_pci_autoprobe():
                assert ap.read_text(encoding="utf-8").strip() == "0"
                raise RuntimeError("boom")
        except RuntimeError:
            pass
        assert ap.read_text(encoding="utf-8").strip() == "1"


def test_dgpu_gate_helper_blocks_sbr_on_non_exclusive_or_bound_bridge(tmp_path):
    """_bridge_exclusively_hosts_unbound_dgpu blocks SBR if sibling slots, USB-C funcs, or bound drivers exist."""
    import importlib.machinery
    import importlib.util
    import shutil
    from pathlib import Path

    helper_path = Path(__file__).resolve().parents[1] / "data" / "gigamate-dgpu-gate"
    loader = importlib.machinery.SourceFileLoader("gigamate_dgpu_gate_sbr_test", str(helper_path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)

    pci_root = tmp_path / "pci"
    bridge = "0000:00:03.1"
    bdir = pci_root / bridge
    bdir.mkdir(parents=True)
    (bdir / "class").write_text("0x060400\n", encoding="utf-8")

    # Downstream devices nested under bridge (real sysfs topology).
    gpu = bdir / "0000:64:00.0"
    aud = bdir / "0000:64:00.1"
    gpu.mkdir()
    aud.mkdir()
    (gpu / "class").write_text("0x030000\n", encoding="utf-8")
    (aud / "class").write_text("0x040300\n", encoding="utf-8")
    (pci_root / "0000:64:00.0").symlink_to(gpu)
    (pci_root / "0000:64:00.1").symlink_to(aud)

    with patch.object(mod, "_PCI_DEVICES", pci_root):
        assert mod._bridge_exclusively_hosts_unbound_dgpu(bridge, "0000:64:00.0") is True

        # Bound audio sibling blocks SBR
        (aud / "driver").mkdir()
        assert mod._bridge_exclusively_hosts_unbound_dgpu(bridge, "0000:64:00.0") is False
        (aud / "driver").rmdir()

        # USB-C xHCI/UCSI function (.2) on GPU slot blocks SBR
        ucsi = bdir / "0000:64:00.2"
        ucsi.mkdir()
        (ucsi / "class").write_text("0x0c0330\n", encoding="utf-8")
        assert mod._bridge_exclusively_hosts_unbound_dgpu(bridge, "0000:64:00.0") is False
        shutil.rmtree(ucsi)

        # Non-GPU sibling slot on the same root bridge blocks SBR
        nvme = bdir / "0000:64:01.0"
        nvme.mkdir()
        (nvme / "class").write_text("0x010802\n", encoding="utf-8")
        assert mod._bridge_exclusively_hosts_unbound_dgpu(bridge, "0000:64:00.0") is False


def test_probe_dgpu_gate_handles_cached_tuple_with_none_match(monkeypatch):
    """probe_dgpu_gate() must not crash when get_gate_config() returns (cfg, None)."""
    from gigamate import dgpu_gate

    topo = dgpu_gate.DgpuTopology(
        bdf="0000:64:00.0",
        vendor="nvidia",
        bridge_bdf="0000:00:03.1",
        acpi_path="\\_SB.PCI0.GPP9.PEGP",
        power_resources=["\\_SB.PCI0.GPP9.PG00"],
    )
    cfg = dgpu_gate.DgpuGateConfig(path="\\_SB.PCI0.GPP9.PG00", source="auto")

    monkeypatch.setattr(dgpu_gate, "_GATE_PROBE_IN_PROGRESS", False)
    monkeypatch.setattr(dgpu_gate, "find_dgpu_topology", lambda: topo)
    monkeypatch.setattr(dgpu_gate, "acpi_eval_backend", lambda: "none")
    monkeypatch.setattr(dgpu_gate, "get_gate_config", lambda: (cfg, None))

    res = dgpu_gate.probe_dgpu_gate(live_test=False)
    assert res["candidates"]
    assert res["candidates"][0]["source"] == "auto"
    assert res["profile_match"]["source"] == "auto"


def test_cli_gpu_power_confirm_dialog_accepts_boot_battery_trigger(monkeypatch):
    """`gigamate gpu power confirm-dialog --trigger boot_battery` parses and forwards args."""
    import sys
    import types
    import pytest
    from gigamate import cli

    captured = {}
    fake_mod = types.ModuleType("gigamate.ui.dgpu_gate_dialog")

    def fake_main(argv):
        captured["argv"] = argv
        return 0

    fake_mod.main = fake_main
    monkeypatch.setitem(sys.modules, "gigamate.ui.dgpu_gate_dialog", fake_mod)
    monkeypatch.setattr(
        sys,
        "argv",
        ["gigamate", "gpu", "power", "confirm-dialog", "--trigger", "boot_battery", "--target-mode", "auto"],
    )
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 0
    assert "--trigger" in captured["argv"]
    assert "boot_battery" in captured["argv"]


def test_daemon_usbc_power_budget_integration(tmp_path, monkeypatch):
    from gigamate import config as config_mod
    from gigamate import daemon as daemon_module

    monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config_mod, "CONFIG_FILE", tmp_path / "config.json")
    assert daemon_module.GigaMateDaemon.desired_poll_interval_ms(True, usbc_fast_poll=True) == 2000
    assert daemon_module.GigaMateDaemon.desired_poll_interval_ms(False, usbc_fast_poll=False) == 15000

    d = daemon_module.GigaMateDaemon()
    called = {"ticks": 0, "shutdown": 0}
    monkeypatch.setattr(d._usbc_power, "tick", lambda *a, **kw: (called.__setitem__("ticks", called["ticks"] + 1) or {}))
    monkeypatch.setattr(d._usbc_power, "shutdown", lambda *a, **kw: called.__setitem__("shutdown", called["shutdown"] + 1))

    with patch.object(d, "sync_from_external_config"), \
         patch.object(d, "_ensure_acpi_controller"), \
         patch.object(d, "_maybe_retry_keyboard"), \
         patch.object(d, "_run_profile_automation"), \
         patch("gigamate.daemon.get_gpu_state", return_value=GpuState(present=False)), \
         patch("gigamate.daemon.get_battery_manager"):
        d.poll_tick(run_automation=True)
    assert called["ticks"] == 1
    d.stop()
    assert called["shutdown"] == 1


def test_cli_gpu_usbc_budget_and_doctor(tmp_path, monkeypatch, capsys):
    import sys
    from gigamate import cli, config as config_mod, doctor, usbc_power

    monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config_mod, "CONFIG_FILE", tmp_path / "config.json")

    for argv in (
        ["gigamate", "gpu", "usbc-budget", "on"],
        ["gigamate", "gpu", "usbc-budget", "set", "58"],
        ["gigamate", "gpu", "usbc-budget", "buffer", "20"],
        ["gigamate", "gpu", "usbc-budget", "off"],
    ):
        monkeypatch.setattr(sys, "argv", argv)
        cli.main()

    cfg = config_mod.load()
    assert cfg["usbc_power_budget_disclaimer_accepted"] is True
    assert cfg["usbc_power_budget_enabled"] is False
    assert cfg["usbc_gpu_budget_w"] == 58
    assert cfg["usbc_buffer_pct"] == 20

    fake_src = usbc_power.PowerSourceSnapshot(
        kind="usbc_pd", ac_online=True, usbc_online=True, is_pd=True,
        detected_wattage_w=100, wattage_source="pd_current_inferred",
        negotiated_current_a=5.0, negotiated_voltage_v=None, firmware_usbc_flag=True,
        bat_present=True, bat_status="Charging", bat_capacity=80, bat_power_w=2.0,
    )
    with patch("gigamate.usbc_power.detect_power_source", return_value=fake_src), \
         patch("gigamate.usbc_power.read_cached_gpu_caps_only", return_value=usbc_power.GpuPowerCaps(default_tgp_w=50, max_constraint_w=85)), \
         patch("gigamate.power_limits._query_nvml_power_caps") as mock_nvml:
        monkeypatch.setattr(sys, "argv", ["gigamate", "gpu", "usbc-budget", "status"])
        cli.main()
        out = capsys.readouterr().out
        assert "usbc_pd" in out and "100 W" in out
        mock_nvml.assert_not_called()

        with patch("gigamate.doctor.find_keyboard_hidraws", return_value=[]), \
             patch("gigamate.doctor.session_details", return_value=({}, "active")), \
             patch("gigamate.doctor.acpi_module_status") as mock_ms, \
             patch("gigamate.dgpu_gate.get_status", return_value={}):
            mock_ms.return_value.running_kernel = "test"
            mock_ms.return_value.summary = "loaded"
            mock_ms.return_value.hint = "ok"
            rc = doctor.run_doctor()
            assert rc == 0
            doc_out = capsys.readouterr().out
            assert "USB-C Power Budget" in doc_out
            assert "100 W" in doc_out
        mock_nvml.assert_not_called()
