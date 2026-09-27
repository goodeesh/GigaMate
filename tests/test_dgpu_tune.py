"""Tests for the sleep-aware dGPU tuning watcher (no real hardware)."""

import importlib.machinery
import importlib.util
import json
from pathlib import Path
from unittest import mock

from gigamate import dgpu_tune


def _patches(rstatus="active", pstate="D0", util=0):
    """Patch the watcher's hardware/sysfs entry points with inert doubles.

    ``get_state`` is patched even where a test does not care about the idle
    probe: an unpatched call would reach the real privileged helper.
    """
    return [
        mock.patch.object(dgpu_tune, "find_nvidia_bdf", return_value="0000:64:00.0"),
        mock.patch.object(dgpu_tune, "runtime_status", return_value=rstatus),
        mock.patch.object(dgpu_tune, "power_state", return_value=pstate),
        mock.patch.object(dgpu_tune, "probe", return_value={"supported": True}),
        mock.patch.object(dgpu_tune, "get_state",
                          return_value={"ok": True, "offset_mhz": 0, "util": util}),
        mock.patch.object(dgpu_tune, "gpu_in_use", return_value=False),
        mock.patch.object(dgpu_tune, "_write_state_file", return_value=None),
    ]


def test_applies_on_active():
    w = dgpu_tune.DgpuWatcher()
    w.set_desired(True, 100, True)
    ps = _patches()
    for p in ps:
        p.start()
    try:
        with mock.patch.object(dgpu_tune, "apply_tune",
                               return_value={"ok": True, "offset_mhz": 100}) as ap, \
             mock.patch.object(dgpu_tune, "clear_tune") as cl:
            w.tick(now=1000.0)
            assert ap.call_args[0] == (100, 0, 0)
            assert cl.called is False
            assert w.status()["applied"] is True
            assert w.status()["desired_offset"] == 100
    finally:
        for p in ps:
            p.stop()


def test_reapply_when_offset_changes_without_clear():
    """Regression: changing the offset must re-apply, not report 'pending'."""
    w = dgpu_tune.DgpuWatcher()
    w.set_desired(True, 100, True)
    ps = _patches()
    for p in ps:
        p.start()
    try:
        with mock.patch.object(dgpu_tune, "apply_tune",
                               return_value={"ok": True, "offset_mhz": 100}) as ap, \
             mock.patch.object(dgpu_tune, "clear_tune") as cl:
            w.tick(now=1000.0)
            assert ap.call_args[0] == (100, 0, 0)

            w.set_desired(True, 150, True)
            w.tick(now=1002.0)
            assert ap.call_args[0] == (150, 0, 0)
            assert cl.called is False
            assert w.status()["applied"] is True
            assert w.status()["desired_offset"] == 150
    finally:
        for p in ps:
            p.stop()


def test_disabled_clears():
    w = dgpu_tune.DgpuWatcher()
    w.set_desired(False, 0, True)
    w._applied_offset = 50  # pretend it was applied before
    ps = _patches()
    for p in ps:
        p.start()
    try:
        with mock.patch.object(dgpu_tune, "apply_tune") as ap, \
             mock.patch.object(dgpu_tune, "clear_tune") as cl:
            w.tick(now=1000.0)
            assert cl.called is True
            assert ap.called is False
            assert w.status()["applied"] is False
    finally:
        for p in ps:
            p.stop()


def test_suspend_d3cold_does_not_touch_nvml():
    """D3cold wipes driver state, so clearing there would only wake the dGPU.

    Resetting the clock locks after the kernel has powered the card down means an
    NVML open, which resumes it — for no benefit, since the locks are already
    gone. The applied values are simply forgotten and the wake path reconciles.
    """
    w = dgpu_tune.DgpuWatcher()
    w.set_desired(True, 100, True)
    w._applied_offset = 100
    ps = _patches(rstatus="suspended", pstate="D3cold")
    for p in ps:
        p.start()
    try:
        with mock.patch.object(dgpu_tune, "clear_tune") as cl:
            w.tick(now=1000.0)
            assert cl.called is False
            assert w.status()["applied"] is False
    finally:
        for p in ps:
            p.stop()


def test_suspend_d3hot_still_clears():
    """D3hot keeps device state, so the locks are reset for real there."""
    w = dgpu_tune.DgpuWatcher()
    w.set_desired(True, 100, True)
    w._applied_offset = 100
    ps = _patches(rstatus="suspended", pstate="D3hot")
    for p in ps:
        p.start()
    try:
        with mock.patch.object(dgpu_tune, "clear_tune") as cl:
            w.tick(now=1000.0)
            assert cl.called is True
            assert w.status()["applied"] is False
    finally:
        for p in ps:
            p.stop()


def test_idle_clear_and_reapply():
    w = dgpu_tune.DgpuWatcher()
    w.set_desired(True, 100, True)
    w._applied_offset = 100
    w._last_poll = 1000.0
    ps = _patches()
    for p in ps:
        p.start()
    try:
        t = 1000.0 + dgpu_tune.IDLE_POLL_SEC
        with mock.patch.object(dgpu_tune, "get_state",
                               return_value={"ok": True, "offset_mhz": 100, "util": 0}), \
             mock.patch.object(dgpu_tune, "clear_tune") as cl:
            w.tick(now=t)
            assert cl.called is False
            w.tick(now=t + dgpu_tune.IDLE_POLL_SEC)
            w.tick(now=t + dgpu_tune.IDLE_CLEAR_SEC + dgpu_tune.IDLE_POLL_SEC)
            assert cl.called is True
            assert w.status()["applied"] is False

        with mock.patch.object(dgpu_tune, "get_state",
                               return_value={"ok": True, "offset_mhz": 0, "util": 80}), \
             mock.patch.object(dgpu_tune, "apply_tune",
                               return_value={"ok": True, "offset_mhz": 100}) as ap:
            t2 = t + 2 * dgpu_tune.IDLE_CLEAR_SEC
            w.tick(now=t2)
            w.tick(now=t2 + dgpu_tune.IDLE_POLL_SEC)
            assert ap.called is True
            assert w.status()["applied"] is True
    finally:
        for p in ps:
            p.stop()


def test_idle_clear_skipped_while_gpu_held():
    """A process holding the dGPU keeps the tuning applied even at 0% util."""
    w = dgpu_tune.DgpuWatcher()
    w.set_desired(True, 100, True)
    w._applied_offset = 100
    w._last_poll = 1000.0
    ps = _patches()
    for p in ps:
        p.start()
    try:
        t = 1000.0 + dgpu_tune.IDLE_POLL_SEC
        with mock.patch.object(dgpu_tune, "get_state",
                               return_value={"ok": True, "offset_mhz": 100, "util": 0}), \
             mock.patch.object(dgpu_tune, "gpu_in_use", return_value=True), \
             mock.patch.object(dgpu_tune, "clear_tune") as cl:
            for step in (t, t + dgpu_tune.IDLE_CLEAR_SEC + dgpu_tune.IDLE_POLL_SEC):
                w.tick(now=step)
            assert cl.called is False
            assert w.status()["applied"] is True
    finally:
        for p in ps:
            p.stop()


def test_reapply_when_gpu_becomes_held():
    """While idle-cleared, a process holding the dGPU re-applies without util."""
    w = dgpu_tune.DgpuWatcher()
    w.set_desired(True, 100, True)
    w._idle_cleared = True
    w._last_poll = 1000.0
    ps = _patches()
    for p in ps:
        p.start()
    try:
        with mock.patch.object(dgpu_tune, "gpu_in_use", return_value=True), \
             mock.patch.object(dgpu_tune, "apply_tune",
                               return_value={"ok": True, "offset_mhz": 100}) as ap, \
             mock.patch.object(dgpu_tune, "get_state") as gs:
            w.tick(now=1000.0 + dgpu_tune.IDLE_POLL_SEC)
            assert ap.called is True
            assert w.status()["applied"] is True
            gs.assert_not_called()  # holder check avoids the NVML read
    finally:
        for p in ps:
            p.stop()


def test_human_status_idle_paused():
    w = dgpu_tune.DgpuWatcher()
    w.set_desired(True, 100, True)
    st = w.status()
    st.update({"supported": True, "runtime": "active", "enabled": True,
               "applied": False, "idle_cleared": True, "auto": True,
               "last_error": None, "desired_offset": 100})
    w._status = st
    with mock.patch.object(dgpu_tune, "watcher", w):
        assert "paused while idle" in dgpu_tune.human_status()


def test_max_clock_applies_independently():
    w = dgpu_tune.DgpuWatcher()
    w.set_desired(False, 0, True, max_enabled=True, max_clock=2100)
    ps = _patches()
    for p in ps:
        p.start()
    try:
        with mock.patch.object(dgpu_tune, "apply_tune",
                               return_value={"ok": True, "offset_mhz": 0}) as ap:
            w.tick(now=1000.0)
            assert ap.call_args[0] == (0, 2100, 0)
            assert w.status()["applied"] is True
            assert w.status()["desired_max_clock"] == 2100
    finally:
        for p in ps:
            p.stop()


def test_max_clock_reapply_on_change():
    w = dgpu_tune.DgpuWatcher()
    w.set_desired(False, 0, True, max_enabled=True, max_clock=2100)
    ps = _patches()
    for p in ps:
        p.start()
    try:
        with mock.patch.object(dgpu_tune, "apply_tune",
                               return_value={"ok": True}) as ap:
            w.tick(now=1000.0)
            assert ap.call_args[0] == (0, 2100, 0)
            w.set_desired(False, 0, True, max_enabled=True, max_clock=1800)
            w.tick(now=1002.0)
            assert ap.call_args[0] == (0, 1800, 0)
    finally:
        for p in ps:
            p.stop()


def test_max_clock_cleared_when_disabled():
    w = dgpu_tune.DgpuWatcher()
    w.set_desired(False, 0, True, max_enabled=True, max_clock=2100)
    ps = _patches()
    for p in ps:
        p.start()
    try:
        with mock.patch.object(dgpu_tune, "apply_tune",
                               return_value={"ok": True}) as ap, \
             mock.patch.object(dgpu_tune, "clear_tune") as cl:
            w.tick(now=1000.0)
            assert ap.called is True
            w.set_desired(False, 0, True, max_enabled=False, max_clock=2100)
            w.tick(now=1002.0)
            assert cl.called is True
            assert w.status()["applied"] is False
    finally:
        for p in ps:
            p.stop()


def test_max_clock_error_surfaces_and_retries():
    w = dgpu_tune.DgpuWatcher()
    w.set_desired(False, 0, True, max_enabled=True, max_clock=2100)
    ps = _patches()
    for p in ps:
        p.start()
    try:
        with mock.patch.object(dgpu_tune, "apply_tune",
                               return_value={"ok": True, "offset_mhz": 0,
                                             "max_clock_error": "unsupported"}) as ap:
            w.tick(now=1000.0)
            assert w.status()["last_error"] == "unsupported"
            # Not recorded as applied -> retried on the next tick.
            assert w.status()["applied"] is False
            w.tick(now=1002.0)
            assert ap.call_count == 2
    finally:
        for p in ps:
            p.stop()


def test_state_file_snapshot_written():
    w = dgpu_tune.DgpuWatcher()
    w.set_desired(True, 100, True, max_enabled=True, max_clock=2100)
    snapshots = []
    ps = _patches()
    for p in ps:
        p.start()
    try:
        with mock.patch.object(dgpu_tune, "_write_state_file",
                               side_effect=lambda s: snapshots.append(s)), \
             mock.patch.object(dgpu_tune, "apply_tune",
                               return_value={"ok": True, "offset_mhz": 100}):
            w.tick(now=1000.0)
            # Only the service / explicit apply publishes state, not bare ticks.
            assert snapshots == []
            w.write_state()
        assert snapshots
        snap = snapshots[-1]
        assert snap["applied_offset"] == 100
        assert snap["applied_max"] == 2100
        assert snap["desired_offset"] == 100
        assert snap["desired_max_clock"] == 2100
    finally:
        for p in ps:
            p.stop()


def test_no_gpu_is_noop():
    w = dgpu_tune.DgpuWatcher()
    w.set_desired(True, 100, True)
    with mock.patch.object(dgpu_tune, "find_nvidia_bdf", return_value=None), \
         mock.patch.object(dgpu_tune, "apply_tune") as ap:
        w.tick(now=1.0)
        assert ap.called is False
        assert w.status()["supported"] is False


def test_set_desired_config_independent_controls():
    """Toggling max clock must not disturb the configured undervolt."""
    dgpu_tune.set_desired_config(enabled=True, offset=120)
    dgpu_tune.set_desired_config(max_enabled=True, max_clock=2000)

    st = dgpu_tune.watcher.status()
    assert st["desired_offset"] == 120
    assert st["desired_max_clock"] == 2000

    dgpu_tune.set_desired_config(max_enabled=False, max_clock=0)
    st = dgpu_tune.watcher.status()
    assert st["desired_offset"] == 120
    assert st["desired_max_clock"] == 0
    assert st["enabled"] is True


def test_auto_off_does_not_apply():
    w = dgpu_tune.DgpuWatcher()
    w.set_desired(True, 100, auto=False)
    ps = _patches()
    for p in ps:
        p.start()
    try:
        with mock.patch.object(dgpu_tune, "apply_tune") as ap, \
             mock.patch.object(dgpu_tune, "clear_tune") as cl:
            w.tick(now=1000.0)
            assert ap.called is False
            assert cl.called is False
            assert w.status()["applied"] is False
            assert w.status()["auto"] is False
    finally:
        for p in ps:
            p.stop()


def test_auto_off_manual_force_applies():
    w = dgpu_tune.DgpuWatcher()
    w.set_desired(True, 100, auto=False)
    ps = _patches()
    for p in ps:
        p.start()
    try:
        with mock.patch.object(dgpu_tune, "apply_tune",
                               return_value={"ok": True, "offset_mhz": 100}) as ap:
            w.tick(now=1000.0, force_apply=True)
            assert ap.call_args[0] == (100, 0, 0)
            assert w.status()["applied"] is True
    finally:
        for p in ps:
            p.stop()


def test_auto_off_still_clears_on_suspend():
    """Auto-apply off must not leave a manual tuning applied across suspend."""
    w = dgpu_tune.DgpuWatcher()
    w.set_desired(True, 100, auto=False)
    w._applied_offset = 100
    ps = _patches(rstatus="suspended", pstate="D3hot")
    for p in ps:
        p.start()
    try:
        with mock.patch.object(dgpu_tune, "clear_tune") as cl:
            w.tick(now=1000.0)
            assert cl.called is True
            assert w.status()["applied"] is False
    finally:
        for p in ps:
            p.stop()


def test_set_auto_config_roundtrip():
    from gigamate.config import load as load_config

    with mock.patch.object(dgpu_tune, "find_nvidia_bdf", return_value=None), \
         mock.patch.object(dgpu_tune, "_write_state_file"):
        dgpu_tune.set_auto_config(False)
        assert load_config()["dgpu_undervolt_auto"] is False
        assert dgpu_tune.watcher.status()["auto"] is False

        dgpu_tune.set_auto_config(True)
        assert load_config()["dgpu_undervolt_auto"] is True
        assert dgpu_tune.watcher.status()["auto"] is True


# ──────────────────────────────────────────────────────────────────────────────
# Memory clock
# ──────────────────────────────────────────────────────────────────────────────

def test_mem_offset_applies_independently():
    w = dgpu_tune.DgpuWatcher()
    w.set_desired(False, 0, True, mem_enabled=True, mem_offset=300)
    ps = _patches()
    for p in ps:
        p.start()
    try:
        with mock.patch.object(dgpu_tune, "apply_tune",
                               return_value={"ok": True, "offset_mhz": 0}) as ap:
            w.tick(now=1000.0)
            assert ap.call_args[0] == (0, 0, 300)
            assert w.status()["applied"] is True
            assert w.status()["desired_mem_offset"] == 300
    finally:
        for p in ps:
            p.stop()


def test_mem_offset_signed_underclock():
    w = dgpu_tune.DgpuWatcher()
    w.set_desired(False, 0, True, mem_enabled=True, mem_offset=-200)
    ps = _patches()
    for p in ps:
        p.start()
    try:
        with mock.patch.object(dgpu_tune, "apply_tune",
                               return_value={"ok": True, "offset_mhz": 0}) as ap:
            w.tick(now=1000.0)
            assert ap.call_args[0] == (0, 0, -200)
            assert w.status()["desired_mem_offset"] == -200
    finally:
        for p in ps:
            p.stop()


def test_mem_offset_applies_alongside_undervolt_and_cap():
    w = dgpu_tune.DgpuWatcher()
    w.set_desired(True, 100, True, max_enabled=True, max_clock=2100,
                  mem_enabled=True, mem_offset=300)
    ps = _patches()
    for p in ps:
        p.start()
    try:
        with mock.patch.object(dgpu_tune, "apply_tune",
                               return_value={"ok": True, "offset_mhz": 100}) as ap:
            w.tick(now=1000.0)
            assert ap.call_args[0] == (100, 2100, 300)
    finally:
        for p in ps:
            p.stop()


def test_mem_offset_error_surfaces_and_retries():
    w = dgpu_tune.DgpuWatcher()
    w.set_desired(False, 0, True, mem_enabled=True, mem_offset=300)
    ps = _patches()
    for p in ps:
        p.start()
    try:
        with mock.patch.object(dgpu_tune, "apply_tune",
                               return_value={"ok": True, "offset_mhz": 0,
                                             "mem_offset_error": "not supported"}) as ap:
            w.tick(now=1000.0)
            assert w.status()["last_error"] == "not supported"
            # Not recorded as applied -> retried rather than silently dropped.
            assert w.status()["applied"] is False
            w.tick(now=1002.0)
            assert ap.call_count == 2
    finally:
        for p in ps:
            p.stop()


def test_mem_offset_cleared_on_suspend():
    w = dgpu_tune.DgpuWatcher()
    w.set_desired(False, 0, True, mem_enabled=True, mem_offset=300)
    w._applied_mem = 300
    ps = _patches(rstatus="suspended", pstate="D3hot")
    for p in ps:
        p.start()
    try:
        with mock.patch.object(dgpu_tune, "clear_tune") as cl:
            w.tick(now=1000.0)
            assert cl.called is True
            assert w.status()["applied"] is False
    finally:
        for p in ps:
            p.stop()


def test_mem_offset_cleared_when_disabled():
    w = dgpu_tune.DgpuWatcher()
    w.set_desired(False, 0, True, mem_enabled=True, mem_offset=300)
    ps = _patches()
    for p in ps:
        p.start()
    try:
        with mock.patch.object(dgpu_tune, "apply_tune",
                               return_value={"ok": True, "offset_mhz": 0}), \
             mock.patch.object(dgpu_tune, "clear_tune") as cl:
            w.tick(now=1000.0)
            assert w.status()["desired_mem_offset"] == 300
            w.set_desired(False, 0, True, mem_enabled=False, mem_offset=300)
            w.tick(now=1002.0)
            assert cl.called is True
            assert w.status()["desired_mem_offset"] == 0
            assert w.status()["applied"] is False
    finally:
        for p in ps:
            p.stop()


def test_set_desired_config_mem_offset_independent():
    """Toggling the memory offset must not disturb the other two controls."""
    dgpu_tune.set_desired_config(enabled=True, offset=120)
    dgpu_tune.set_desired_config(max_enabled=True, max_clock=2000)
    dgpu_tune.set_desired_config(mem_enabled=True, mem_offset=300)

    st = dgpu_tune.watcher.status()
    assert st["desired_offset"] == 120
    assert st["desired_max_clock"] == 2000
    assert st["desired_mem_offset"] == 300

    dgpu_tune.set_desired_config(mem_enabled=False, mem_offset=0)
    st = dgpu_tune.watcher.status()
    assert st["desired_offset"] == 120
    assert st["desired_max_clock"] == 2000
    assert st["desired_mem_offset"] == 0


def test_reset_legacy_mem_pin_calls_helper():
    with mock.patch.object(dgpu_tune, "_run_helper") as rh:
        dgpu_tune.reset_legacy_mem_pin()
        rh.assert_called_once_with(["reset-mem-pin"])


def test_helper_version_detects_stale_install():
    with mock.patch.object(dgpu_tune, "_probe_cache", {"helper_version": 1}):
        assert dgpu_tune.cached_helper_version() == 1
        assert dgpu_tune.helper_is_current() is False
    with mock.patch.object(dgpu_tune, "_probe_cache", {"helper_version": 4}):
        assert dgpu_tune.cached_helper_version() == 4
        assert dgpu_tune.helper_is_current() is True
    # Cold cache: helper_is_current probes once, cached_helper_version does not.
    with mock.patch.object(dgpu_tune, "_probe_cache", None), \
         mock.patch.object(dgpu_tune, "probe",
                           return_value={"helper_version": 4}) as probe:
        assert dgpu_tune.cached_helper_version() is None
        assert probe.called is False
        assert dgpu_tune.helper_is_current() is True
        assert probe.called is True


# ──────────────────────────────────────────────────────────────────────────────
# NVML gating: a suspended dGPU must never be resumed by our own code
# ──────────────────────────────────────────────────────────────────────────────

def test_nvml_allowed_tracks_runtime_status():
    with mock.patch.object(dgpu_tune, "find_nvidia_bdf", return_value="0000:64:00.0"), \
         mock.patch.object(dgpu_tune, "runtime_status", return_value="active"):
        assert dgpu_tune.nvml_allowed() is True
    with mock.patch.object(dgpu_tune, "find_nvidia_bdf", return_value="0000:64:00.0"), \
         mock.patch.object(dgpu_tune, "runtime_status", return_value="suspended"):
        assert dgpu_tune.nvml_allowed() is False
    with mock.patch.object(dgpu_tune, "find_nvidia_bdf", return_value=None):
        assert dgpu_tune.nvml_allowed() is False


def test_probe_if_awake_falls_back_to_cache():
    """The whole point: never open NVML on a card the kernel powered down."""
    cached = {"supported": True, "helper_version": 3}
    with mock.patch.object(dgpu_tune, "nvml_allowed", return_value=False), \
         mock.patch.object(dgpu_tune, "_probe_cache", cached), \
         mock.patch.object(dgpu_tune, "_run_helper") as rh:
        assert dgpu_tune.probe_if_awake() == cached
        assert rh.called is False
    with mock.patch.object(dgpu_tune, "nvml_allowed", return_value=False), \
         mock.patch.object(dgpu_tune, "_probe_cache", None), \
         mock.patch.object(dgpu_tune, "_run_helper") as rh:
        assert dgpu_tune.probe_if_awake() == {}
        assert rh.called is False

# ──────────────────────────────────────────────────────────────────────────────
# Privileged helper (data/gigamate-dgpu-nvml)
# ──────────────────────────────────────────────────────────────────────────────

def _load_helper():
    path = (Path(dgpu_tune.__file__).resolve().parent.parent.parent
            / "data" / "gigamate-dgpu-nvml")
    loader = importlib.machinery.SourceFileLoader("gigamate_dgpu_nvml", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


class _FakeBackend:
    def __init__(self):
        self.offset = 0
        self.locked = None
        self.reset_locked = False
        self.ceiling = 2400
        self.mem_offset = 0
        self.mem_pin_reset = False
        self.mem_ceiling = 12001

    def set_offset(self, mhz):
        self.offset = mhz

    def get_offset(self):
        return self.offset

    def set_max_clock(self, mhz):
        self.locked = mhz

    def reset_max_clock(self):
        self.reset_locked = True
        self.locked = None

    def get_max_clock(self, clock_id=0):
        return self.mem_ceiling if clock_id == 2 else self.ceiling

    def set_mem_offset(self, mhz):
        self.mem_offset = mhz

    def get_mem_offset(self):
        return self.mem_offset

    def has_mem_offset(self):
        return True

    def reset_mem_locked(self):
        self.mem_pin_reset = True

    def get_util(self):
        return 0

    def close(self):
        pass


def _helper_call(mod, argv, be):
    with mock.patch.object(mod, "find_nvidia_bdf", return_value="0000:64:00.0"), \
         mock.patch.object(mod, "open_backend", return_value=be):
        return mod.main(["gigamate-dgpu-nvml", *argv])


def test_helper_apply_sets_offset_and_cap(capsys):
    mod = _load_helper()
    be = _FakeBackend()
    code = _helper_call(mod, ["apply", "100", "2100"], be)
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert code == 0
    assert out["ok"] is True
    assert out["offset_mhz"] == 100
    assert out["max_clock_mhz"] == 2100
    assert out["mem_offset_mhz"] == 0
    assert be.offset == 100
    assert be.locked == 2100
    # Any legacy memory-clock pin is dropped on every apply.
    assert be.mem_pin_reset is True


def test_helper_apply_sets_memory_offset(capsys):
    mod = _load_helper()
    be = _FakeBackend()
    _helper_call(mod, ["apply", "0", "0", "300"], be)
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert out["ok"] is True
    assert out["mem_offset_mhz"] == 300
    assert be.mem_offset == 300
    assert be.reset_locked is True


def test_helper_apply_memory_offset_signed(capsys):
    mod = _load_helper()
    be = _FakeBackend()
    _helper_call(mod, ["apply", "0", "0", "-250"], be)
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert out["ok"] is True
    assert out["mem_offset_mhz"] == -250
    assert be.mem_offset == -250


def test_helper_apply_mem_offset_error_is_isolated(capsys):
    """A refused memory offset must not undo the offset/cap, and must be reported."""
    mod = _load_helper()
    be = _FakeBackend()

    def _boom(_mhz):
        raise RuntimeError("memory clock offset unsupported")

    be.set_mem_offset = _boom
    _helper_call(mod, ["apply", "100", "2100", "300"], be)
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert out["ok"] is True
    assert out["offset_mhz"] == 100
    assert out["max_clock_mhz"] == 2100
    assert out["mem_offset_mhz"] == 0
    assert "memory clock offset unsupported" in out["mem_offset_error"]


def test_helper_apply_zero_max_unlocks(capsys):
    mod = _load_helper()
    be = _FakeBackend()
    _helper_call(mod, ["apply", "50"], be)
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert out["ok"] is True
    assert out["max_clock_mhz"] == 0
    assert be.reset_locked is True


def test_helper_clear_resets_all(capsys):
    mod = _load_helper()
    be = _FakeBackend()
    _helper_call(mod, ["clear"], be)
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert out["ok"] is True
    assert out["offset_mhz"] == 0
    assert out["max_clock_mhz"] == 0
    assert out["mem_offset_mhz"] == 0
    assert be.offset == 0
    assert be.reset_locked is True
    assert be.mem_offset == 0
    assert be.mem_pin_reset is True


def test_helper_reset_mem_pin(capsys):
    mod = _load_helper()
    be = _FakeBackend()
    _helper_call(mod, ["reset-mem-pin"], be)
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert out["ok"] is True
    assert be.mem_pin_reset is True


def test_helper_probe_reports_ceiling(capsys):
    mod = _load_helper()
    be = _FakeBackend()
    _helper_call(mod, ["probe"], be)
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert out["ok"] is True
    assert out["supported"] is True
    assert out["gpu_max_clock_mhz"] == 2400
    assert out["mem_max_clock_mhz"] == 12001
    assert out["mem_offset_api"] is True
    assert out["helper_version"] == mod.HELPER_VERSION
