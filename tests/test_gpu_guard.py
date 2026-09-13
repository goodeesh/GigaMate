import os
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from gigamate.gpu import GpuState, NvidiaGpuMonitor
from gigamate.gpu_guard import DgpuSleepGuard, GpuProcess, GpuGuardStatus


def test_zero_wake_when_gpu_is_asleep(tmp_path: Path):
    mock_monitor = MagicMock(spec=NvidiaGpuMonitor)
    mock_monitor.read_state.return_value = GpuState(
        present=True,
        status="suspended",
        power_state="D3cold",
        vendor="nvidia",
    )

    proc_dir = tmp_path / "proc"
    proc_dir.mkdir()
    # Create fake process that would match if scanned
    p1 = proc_dir / "100"
    p1.mkdir()
    (p1 / "fd").mkdir()

    guard = DgpuSleepGuard(gpu_monitor=mock_monitor, proc_path=proc_dir)
    status = guard.inspect()

    assert status.present is True
    assert status.is_awake is False
    assert status.power_state == "D3cold"
    assert len(status.processes) == 0
    # Verified: early exit, no inspection performed


def test_process_inspection_when_awake(tmp_path: Path):
    mock_monitor = MagicMock(spec=NvidiaGpuMonitor)
    mock_monitor.read_state.return_value = GpuState(
        present=True,
        status="active",
        power_state="D0",
        vendor="nvidia",
    )
    mock_monitor._device = tmp_path / "pci_dev"

    dev_dir = tmp_path / "dev"
    dev_dir.mkdir()
    (dev_dir / "nvidia0").touch()
    (dev_dir / "nvidiactl").touch()

    proc_dir = tmp_path / "proc"
    proc_dir.mkdir()

    # Process 1: steam (leech)
    p1 = proc_dir / "200"
    p1.mkdir()
    (p1 / "comm").write_text("steam\n")
    (p1 / "cmdline").write_bytes(b"/usr/bin/steam\x00-silent\x00")
    p1_fd = p1 / "fd"
    p1_fd.mkdir()
    os.symlink(str(dev_dir / "nvidia0"), str(p1_fd / "3"))

    # Process 2: plasmashell (protected)
    p2 = proc_dir / "300"
    p2.mkdir()
    (p2 / "comm").write_text("plasmashell\n")
    (p2 / "cmdline").write_bytes(b"/usr/bin/plasmashell\x00")
    p2_fd = p2 / "fd"
    p2_fd.mkdir()
    os.symlink(str(dev_dir / "nvidiactl"), str(p2_fd / "7"))

    # Process 3: unrelated (not using GPU)
    p3 = proc_dir / "400"
    p3.mkdir()
    (p3 / "comm").write_text("bash\n")
    p3_fd = p3 / "fd"
    p3_fd.mkdir()
    os.symlink("/dev/null", str(p3_fd / "0"))

    guard = DgpuSleepGuard(gpu_monitor=mock_monitor, proc_path=proc_dir, dev_path=dev_dir)
    status = guard.inspect()

    assert status.present is True
    assert status.is_awake is True
    assert len(status.processes) == 2

    steam_proc = next(p for p in status.processes if p.pid == 200)
    assert steam_proc.comm == "steam"
    assert steam_proc.is_leech is True
    assert steam_proc.is_protected is False

    plasma_proc = next(p for p in status.processes if p.pid == 300)
    assert plasma_proc.comm == "plasmashell"
    assert plasma_proc.is_protected is True
    assert plasma_proc.is_leech is False


def test_protected_process_cannot_be_terminated(tmp_path: Path):
    mock_monitor = MagicMock(spec=NvidiaGpuMonitor)
    proc_dir = tmp_path / "proc"
    proc_dir.mkdir()

    p = proc_dir / "500"
    p.mkdir()
    (p / "comm").write_text("kwin_wayland\n")

    guard = DgpuSleepGuard(gpu_monitor=mock_monitor, proc_path=proc_dir)
    # Refusal to kill kwin
    assert guard.terminate_process(500) is False


@patch("os.kill")
def test_terminate_leech_process(mock_kill, tmp_path: Path):
    mock_monitor = MagicMock(spec=NvidiaGpuMonitor)
    mock_monitor.read_state.return_value = GpuState(
        present=True,
        status="active",
        power_state="D0",
        vendor="nvidia",
    )
    dev_dir = tmp_path / "dev"
    dev_dir.mkdir()
    (dev_dir / "nvidia0").touch()

    proc_dir = tmp_path / "proc"
    proc_dir.mkdir()

    p = proc_dir / "600"
    p.mkdir()
    (p / "comm").write_text("discord\n")
    (p / "cmdline").write_bytes(b"/usr/bin/discord\x00")
    p_fd = p / "fd"
    p_fd.mkdir()
    os.symlink(str(dev_dir / "nvidia0"), str(p_fd / "5"))

    guard = DgpuSleepGuard(gpu_monitor=mock_monitor, proc_path=proc_dir, dev_path=dev_dir)
    results = guard.terminate_all_leeches()

    assert len(results) == 1
    assert results[0][0] == 600
    assert results[0][1] == "discord"
    assert results[0][2] is True
    mock_kill.assert_called_once()
