"""Opt-in USB-C PD Power Budget engine (zero GUI dependencies).

Safely overrides NVIDIA's 30 W "Undersized Power Source" Aux Power State cap on
capable (>= 90 W) USB-C PD chargers by allocating a fixed system safety buffer
plus an EC/NVPCF/SMU-bounded CPU <-> GPU power split, with closed-loop battery
drain protection (gradual auto-trim) and full stock restoration on unplug.
"""

from __future__ import annotations

import ctypes
from dataclasses import dataclass
import fcntl
import json
import logging
import os
from pathlib import Path
import struct
import time
from typing import Callable, Optional, Tuple

from .acpi import AcpiController, FanProfile, GIGAMATE_ACPI_SYSFS
from .gpu import GpuState, get_gpu_state
from .paths import get_runtime_dir
from .power_limits import (
    GPU_CAPS_CACHE_FILE,
    GpuPowerCaps,
    POWER_SUPPLY_SYSFS,
    read_gpu_power_caps,
)

logger = logging.getLogger(__name__)

# Budget geometry & safety thresholds
MIN_USBC_PD_WATTS = 90
MIN_CPU_WATTS = 15
MAX_CTGP_OFFSET_W = 10
MIN_BUFFER_PCT = 15
MAX_BUFFER_PCT = 25
DEFAULT_BUFFER_PCT = 25
BUFFER_RATIO = 0.25
MIN_BUFFER_W = 15

# Timing constants (seconds)
ATTACH_SETTLE_SEC = 1.5
ATTACH_REASSERT_SEC = 3.5
DRAIN_THRESHOLD_W = 3.0
DRAIN_SUSTAIN_SEC = 5.0
CRITICAL_BAT_PCT = 15
TRIM_COOLDOWN_SEC = 5.0
RECOVER_SUSTAIN_SEC = 15.0

# NVIDIA RM Aux Power States (NV2080_CTRL_CMD_PERF_SET_AUX_POWER_STATE)
AUX_P0 = 0  # AC / unlocked (50..85 W depending on profile & ACBT)
AUX_P1 = 1  # Undersized AC stock cap (~30 W)
AUX_P2 = 2  # ~20 W emergency cap
AUX_P3 = 3  # ~15 W cap
AUX_P4 = 4  # ~10 W cap

# NVIDIA open RM ioctl constants (/dev/nvidiactl, 620.x+)
_NV_IOCTL_MAGIC = ord("F")
_NV_IOCTL_BASE = 200
_NV_ESC_CARD_INFO = _NV_IOCTL_BASE + 0
_NV_ESC_ATTACH_GPUS_TO_FD = _NV_IOCTL_BASE + 12
_NV_ESC_RM_FREE = 0x29
_NV_ESC_RM_CONTROL = 0x2A
_NV_ESC_RM_ALLOC = 0x2B
_NV01_ROOT_CLIENT = 0x00000041
_NV01_DEVICE_0 = 0x00000080
_NV20_SUBDEVICE_0 = 0x00002080
_NV2080_CTRL_CMD_PERF_SET_AUX_POWER_STATE = 0x20802092

DISCLAIMER_TITLE = "Enable USB-C PD Power Budget?"
DISCLAIMER_TEXT = (
    "USB-C PD Power Budget overrides NVIDIA's 30 W \"Undersized Power Source\" cap "
    "when connected to a >= 90 W USB-C PD charger.\n\n"
    "• Safety buffer: GigaMate reserves 15%–25% of the detected charger capacity (default 25%, minimum 15 W) "
    "for display, chassis electronics, and VRM conversion losses, and clamps CPU + GPU limits "
    "to fit inside the remaining silicon budget.\n"
    "• Battery drain guard: If sustained battery drain exceeds 3 W for 5 seconds (or battery "
    "drops below 15%), GigaMate automatically trims CPU, then GPU power, and steps back toward "
    "30 W / 20 W.\n"
    "• Firmware & charger note: Under sudden combined spikes, some third-party USB-C bricks or "
    "cables can trip charger over-current protection. Lower the GPU split or turn this off if "
    "your charger resets."
)


@dataclass(frozen=True)
class PowerSourceSnapshot:
    """Live Linux sysfs + ACPI view of active charger, USB-C PD contract, and battery."""

    kind: str  # "battery" | "barrel_ac" | "usbc_pd"
    ac_online: bool
    usbc_online: bool
    is_pd: bool
    detected_wattage_w: Optional[int]
    wattage_source: str  # "exact" | "pd_current_inferred" | "unknown"
    negotiated_current_a: Optional[float]
    negotiated_voltage_v: Optional[float]
    firmware_usbc_flag: Optional[bool]
    bat_present: bool
    bat_status: str
    bat_capacity: Optional[int]
    bat_power_w: Optional[float]  # positive = charging, negative = discharging


@dataclass(frozen=True)
class BudgetEnvelope:
    """Computed safe power envelope for a given charger & GPU hardware capability set."""

    supported: bool
    reason: str
    detected_charger_w: Optional[int]
    buffer_w: int
    silicon_budget_w: int
    gpu_min_w: int
    gpu_max_w: int
    default_gpu_w: int
    cpu_floor_w: int
    has_ctgp_ctrl: bool
    has_cpu_ppt_ctrl: bool
    has_dyn_boost_ctrl: bool
    buffer_pct: int = DEFAULT_BUFFER_PCT


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore").strip()
    except OSError:
        return ""


def _read_int(path: Path) -> Optional[int]:
    txt = _read_text(path)
    if not txt:
        return None
    try:
        return int(txt)
    except ValueError:
        return None


def _iowr(nr: int, size: int) -> int:
    return (3 << 30) | (size << 16) | (_NV_IOCTL_MAGIC << 8) | nr


def _read_battery_sample(entry: Path) -> Tuple[str, Optional[int], Optional[float]]:
    status = _read_text(entry / "status") or "Unknown"
    capacity = _read_int(entry / "capacity")

    power_w: Optional[float] = None
    p_uw = _read_int(entry / "power_now")
    if p_uw is not None:
        power_w = abs(p_uw) / 1_000_000.0
    else:
        v_uv = _read_int(entry / "voltage_now")
        i_ua = _read_int(entry / "current_now")
        if v_uv is not None and i_ua is not None:
            power_w = (abs(v_uv) * abs(i_ua)) / 1e12

    if power_w is not None:
        power_w = round(-power_w if status.lower() == "discharging" else power_w, 2)
    return status, capacity, power_w


def _extract_usbc_contract(entry: Path) -> Tuple[bool, Optional[int], str, Optional[float], Optional[float]]:
    usb_type = _read_text(entry / "usb_type")
    is_pd = "[PD]" in usb_type or "[PD_PPS]" in usb_type

    v_max = _read_int(entry / "voltage_max")
    i_max = _read_int(entry / "current_max")
    v_now = _read_int(entry / "voltage_now")
    i_now = _read_int(entry / "current_now")

    if v_max and v_max > 0 and i_max and i_max > 0:
        watts = int(round((v_max * i_max) / 1e12))
        return is_pd, watts, "exact", round(i_max / 1e6, 2), round(v_max / 1e6, 2)

    if v_now and v_now > 0 and i_now and i_now > 0:
        watts = int(round((v_now * i_now) / 1e12))
        return is_pd, watts, "exact", round(i_now / 1e6, 2), round(v_now / 1e6, 2)

    if is_pd:
        curr_ua = max(i_now or 0, i_max or 0)
        curr_a = round(curr_ua / 1e6, 2) if curr_ua > 0 else None
        if curr_ua >= 4_500_000:
            # 20 V x 5 A USB-C PD EPR/SPR 100 W class when voltage_now/max reads 0.
            return True, 100, "pd_current_inferred", curr_a, None
        if curr_ua >= 2_800_000:
            return True, 60, "pd_current_inferred", curr_a, None
        if curr_ua > 0:
            return True, max(5, int(round(20.0 * (curr_ua / 1e6)))), "pd_current_inferred", curr_a, None
        return True, None, "unknown", None, None

    curr_ua = max(i_now or 0, i_max or 0)
    curr_a = round(curr_ua / 1e6, 2) if curr_ua > 0 else None
    return False, None, "unknown", curr_a, None


def detect_power_source(
    power_supply_dir: Optional[Path] = None,
    acpi_sysfs_dir: Optional[Path] = None,
) -> PowerSourceSnapshot:
    """Inspect ``/sys/class/power_supply`` and ACPI ``usbc_adapter`` without waking the dGPU."""
    ps_dir = power_supply_dir if power_supply_dir is not None else POWER_SUPPLY_SYSFS
    acpi_dir = acpi_sysfs_dir if acpi_sysfs_dir is not None else GIGAMATE_ACPI_SYSFS

    ac_online = False
    usbc_candidates: list[Tuple[int, Path]] = []
    bat_entry: Optional[Path] = None

    if ps_dir.is_dir():
        try:
            entries = sorted(ps_dir.iterdir(), key=lambda p: p.name)
        except OSError:
            entries = []
        for entry in entries:
            ptype = _read_text(entry / "type")
            if ptype == "Mains":
                if _read_int(entry / "online") == 1:
                    ac_online = True
            elif entry.name.startswith("ucsi-source-psy-") or ptype == "USB":
                if _read_int(entry / "online") == 1:
                    curr = max(_read_int(entry / "current_now") or 0, _read_int(entry / "current_max") or 0)
                    usbc_candidates.append((curr, entry))
            elif ptype == "Battery" and bat_entry is None:
                if _read_text(entry / "scope").lower() != "device":
                    bat_entry = entry

    usbc_online = bool(usbc_candidates)
    is_pd = False
    detected_w: Optional[int] = None
    wattage_src = "unknown"
    neg_curr_a: Optional[float] = None
    neg_volt_v: Optional[float] = None
    if usbc_candidates:
        usbc_candidates.sort(key=lambda item: item[0], reverse=True)
        is_pd, detected_w, wattage_src, neg_curr_a, neg_volt_v = _extract_usbc_contract(
            usbc_candidates[0][1]
        )

    bat_present = bat_entry is not None
    bat_status = "Unknown"
    bat_capacity: Optional[int] = None
    bat_power_w: Optional[float] = None
    if bat_entry is not None:
        bat_status, bat_capacity, bat_power_w = _read_battery_sample(bat_entry)

    fw_raw = _read_int(acpi_dir / "usbc_adapter")
    fw_usbc: Optional[bool] = bool(fw_raw) if fw_raw in (0, 1) else None

    if usbc_online and (is_pd or fw_usbc is True):
        kind = "usbc_pd"
    elif ac_online and fw_usbc is True:
        kind = "usbc_pd"
    elif ac_online:
        kind = "barrel_ac"
    else:
        kind = "battery"

    return PowerSourceSnapshot(
        kind=kind,
        ac_online=ac_online,
        usbc_online=usbc_online,
        is_pd=is_pd,
        detected_wattage_w=detected_w,
        wattage_source=wattage_src,
        negotiated_current_a=neg_curr_a,
        negotiated_voltage_v=neg_volt_v,
        firmware_usbc_flag=fw_usbc,
        bat_present=bat_present,
        bat_status=bat_status,
        bat_capacity=bat_capacity,
        bat_power_w=bat_power_w,
    )


def compute_budget_envelope(
    detected_w: Optional[int],
    default_tgp_w: Optional[int],
    max_constraint_w: Optional[int],
    *,
    has_ctgp_ctrl: bool,
    has_cpu_ppt_ctrl: bool,
    has_dyn_boost_ctrl: bool,
    has_nvidia_gpu: bool = True,
    buffer_pct: int = DEFAULT_BUFFER_PCT,
) -> BudgetEnvelope:
    """Compute a safe USB-C PD power envelope and valid GPU split range."""
    pct = max(MIN_BUFFER_PCT, min(MAX_BUFFER_PCT, int(buffer_pct)))
    base_kwargs = {
        "detected_charger_w": detected_w,
        "has_ctgp_ctrl": bool(has_ctgp_ctrl),
        "has_cpu_ppt_ctrl": bool(has_cpu_ppt_ctrl),
        "has_dyn_boost_ctrl": bool(has_dyn_boost_ctrl),
        "cpu_floor_w": MIN_CPU_WATTS,
        "buffer_pct": pct,
    }

    if not has_nvidia_gpu:
        return BudgetEnvelope(
            supported=False,
            reason="no_nvidia_gpu",
            buffer_w=0,
            silicon_budget_w=0,
            gpu_min_w=0,
            gpu_max_w=0,
            default_gpu_w=0,
            **base_kwargs,
        )

    if detected_w is None:
        return BudgetEnvelope(
            supported=False,
            reason="unknown_charger_wattage",
            buffer_w=0,
            silicon_budget_w=0,
            gpu_min_w=int(default_tgp_w or 0),
            gpu_max_w=int(default_tgp_w or 0),
            default_gpu_w=int(default_tgp_w or 0),
            **base_kwargs,
        )

    if detected_w < MIN_USBC_PD_WATTS:
        buf = max(MIN_BUFFER_W, int(round(detected_w * (pct / 100.0)))) if detected_w > MIN_BUFFER_W else 0
        return BudgetEnvelope(
            supported=False,
            reason="charger_too_small",
            buffer_w=buf,
            silicon_budget_w=max(0, detected_w - buf),
            gpu_min_w=int(default_tgp_w or 0),
            gpu_max_w=int(default_tgp_w or 0),
            default_gpu_w=int(default_tgp_w or 0),
            **base_kwargs,
        )

    buffer_w = max(MIN_BUFFER_W, int(round(detected_w * (pct / 100.0))))
    silicon_budget_w = max(0, int(detected_w) - buffer_w)

    if default_tgp_w is None:
        return BudgetEnvelope(
            supported=False,
            reason="gpu_tgp_unknown",
            buffer_w=buffer_w,
            silicon_budget_w=silicon_budget_w,
            gpu_min_w=0,
            gpu_max_w=0,
            default_gpu_w=0,
            **base_kwargs,
        )

    gpu_min_w = int(default_tgp_w)
    max_safe_gpu_w = silicon_budget_w - MIN_CPU_WATTS
    if gpu_min_w > max_safe_gpu_w:
        return BudgetEnvelope(
            supported=False,
            reason="gpu_tgp_exceeds_budget",
            buffer_w=buffer_w,
            silicon_budget_w=silicon_budget_w,
            gpu_min_w=gpu_min_w,
            gpu_max_w=gpu_min_w,
            default_gpu_w=gpu_min_w,
            **base_kwargs,
        )

    ctgp_span = MAX_CTGP_OFFSET_W if has_ctgp_ctrl else 0
    vbios_cap = int(max_constraint_w) if max_constraint_w is not None else (gpu_min_w + ctgp_span)
    gpu_max_w = max(gpu_min_w, min(gpu_min_w + ctgp_span, vbios_cap, max_safe_gpu_w))
    default_gpu_w = min(gpu_max_w, max(gpu_min_w, gpu_min_w + (gpu_max_w - gpu_min_w) // 2))

    return BudgetEnvelope(
        supported=True,
        reason="ok",
        buffer_w=buffer_w,
        silicon_budget_w=silicon_budget_w,
        gpu_min_w=gpu_min_w,
        gpu_max_w=gpu_max_w,
        default_gpu_w=default_gpu_w,
        **base_kwargs,
    )


def clamp_split(desired_gpu_w: int, env: BudgetEnvelope) -> Tuple[int, int]:
    """Clamp desired GPU target inside ``env`` and return ``(gpu_w, cpu_w)``."""
    gpu_w = max(env.gpu_min_w, min(env.gpu_max_w, int(desired_gpu_w)))
    cpu_w = max(env.cpu_floor_w, env.silicon_budget_w - gpu_w)
    return gpu_w, cpu_w


def _nvrm_alloc(
    ctl_fd: int,
    h_root: int,
    h_parent: int,
    h_new: int,
    h_class: int,
    param_buf: Optional[ctypes.Array],
) -> Tuple[int, int]:
    ptr = ctypes.addressof(param_buf) if param_buf is not None else 0
    sz = ctypes.sizeof(param_buf) if param_buf is not None else 0
    buf = bytearray(struct.pack("<IIIIQII", h_root, h_parent, h_new, h_class, ptr, sz, 0))
    fcntl.ioctl(ctl_fd, _iowr(_NV_ESC_RM_ALLOC, len(buf)), buf, True)
    h_root_out, _, h_new_out, _, _, _, status = struct.unpack("<IIIIQII", buf)
    return status, (h_new_out or h_root_out)


def _nvrm_attach_gpu_to_ctl(ctl_fd: int) -> None:
    card_sz = 72
    buf = bytearray(card_sz * 4)
    fcntl.ioctl(ctl_fd, _iowr(_NV_ESC_CARD_INFO, len(buf)), buf, True)
    for idx in range(4):
        chunk = buf[idx * card_sz : (idx + 1) * card_sz]
        if chunk[0]:
            gpu_id = struct.unpack_from("<I", chunk, 16)[0]
            arr = bytearray(struct.pack("<I", gpu_id))
            fcntl.ioctl(ctl_fd, _iowr(_NV_ESC_ATTACH_GPUS_TO_FD, 4), arr, True)
            return


def _nvrm_free(ctl_fd: int, h_root: int, h_parent: int, h_obj: int) -> None:
    buf = bytearray(struct.pack("<IIII", h_root, h_parent, h_obj, 0))
    try:
        fcntl.ioctl(ctl_fd, _iowr(_NV_ESC_RM_FREE, len(buf)), buf, True)
    except OSError:
        pass


def set_nvidia_aux_power_state(
    state: int,
    gpu_state: Optional[GpuState] = None,
) -> Tuple[bool, str]:
    """Set NVIDIA Aux Power State (0=P0..4=P4) via non-privileged RM ioctl.

    Strictly sleep-safe: returns immediately without opening device nodes
    if the dGPU is gated off or not in runtime ``active`` state.
    """
    if not (AUX_P0 <= int(state) <= AUX_P4):
        return False, f"invalid_state:{state}"

    st = gpu_state if gpu_state is not None else get_gpu_state()
    if not st.present or st.vendor != "nvidia" or st.gated_off or st.status != "active":
        return False, "gpu_not_active"

    ctl_fd: Optional[int] = None
    dev_fd: Optional[int] = None
    h_root = 0
    h_dev = 0xBEEF0080
    h_subdev = 0xBEEF2080
    has_dev = False
    has_subdev = False

    try:
        ctl_fd = os.open("/dev/nvidiactl", os.O_RDWR)
        try:
            dev_fd = os.open("/dev/nvidia0", os.O_RDWR)
        except OSError:
            dev_fd = None

        rm_st, h_new = _nvrm_alloc(ctl_fd, 0, 0, 0, _NV01_ROOT_CLIENT, None)
        if rm_st != 0 or h_new == 0:
            p0 = (ctypes.c_ubyte * 120)()
            struct.pack_into("<II16s", p0, 0, 0, os.getpid(), b"gigamate_usbc")
            rm_st, h_new = _nvrm_alloc(ctl_fd, 0, 0, 0, _NV01_ROOT_CLIENT, p0)
        if rm_st != 0 or h_new == 0:
            return False, f"nvrm_root_alloc:0x{rm_st:x}"
        h_root = h_new

        p80 = (ctypes.c_ubyte * 56)()
        rm_st, _ = _nvrm_alloc(ctl_fd, h_root, h_root, h_dev, _NV01_DEVICE_0, p80)
        if rm_st != 0:
            _nvrm_attach_gpu_to_ctl(ctl_fd)
            rm_st, _ = _nvrm_alloc(ctl_fd, h_root, h_root, h_dev, _NV01_DEVICE_0, p80)
        if rm_st != 0:
            return False, f"nvrm_dev_alloc:0x{rm_st:x}"
        has_dev = True

        p2080 = (ctypes.c_ubyte * 4)()
        rm_st, _ = _nvrm_alloc(ctl_fd, h_root, h_dev, h_subdev, _NV20_SUBDEVICE_0, p2080)
        if rm_st != 0:
            return False, f"nvrm_subdev_alloc:0x{rm_st:x}"
        has_subdev = True

        param = (ctypes.c_ubyte * 4).from_buffer_copy(struct.pack("<I", int(state)))
        ctrl_buf = bytearray(
            struct.pack(
                "<IIIIQII",
                h_root,
                h_subdev,
                _NV2080_CTRL_CMD_PERF_SET_AUX_POWER_STATE,
                0,
                ctypes.addressof(param),
                len(param),
                0,
            )
        )
        fcntl.ioctl(ctl_fd, _iowr(_NV_ESC_RM_CONTROL, len(ctrl_buf)), ctrl_buf, True)
        ctrl_st = struct.unpack_from("<I", ctrl_buf, 28)[0]
        if ctrl_st != 0:
            return False, f"nvrm_ctrl_status:0x{ctrl_st:x}"
        return True, ""
    except OSError as exc:
        return False, f"ioctl_error:{exc}"
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)
    finally:
        if ctl_fd is not None:
            if has_subdev:
                _nvrm_free(ctl_fd, h_root, h_dev, h_subdev)
            if has_dev:
                _nvrm_free(ctl_fd, h_root, h_root, h_dev)
            if h_root:
                _nvrm_free(ctl_fd, 0, 0, h_root)
            try:
                os.close(ctl_fd)
            except OSError:
                pass
        if dev_fd is not None:
            try:
                os.close(dev_fd)
            except OSError:
                pass


def read_cached_gpu_caps_only(cache_file: Optional[Path] = None) -> GpuPowerCaps:
    """Read cached ``gpu_power_caps.json`` strictly from disk; never touches NVML."""
    cpath = cache_file if cache_file is not None else GPU_CAPS_CACHE_FILE
    try:
        if not cpath.is_file():
            return GpuPowerCaps()
        data = json.loads(cpath.read_text(encoding="utf-8"))
        default_w = data.get("default_tgp_w")
        max_w = data.get("max_constraint_w")
        d_ok = int(default_w) if default_w is not None and 25 <= int(default_w) <= 175 else None
        m_ok = int(max_w) if max_w is not None and 30 <= int(max_w) <= 220 else None
        return GpuPowerCaps(default_tgp_w=d_ok, max_constraint_w=m_ok)
    except Exception:
        return GpuPowerCaps()


def state_file_path() -> Path:
    return get_runtime_dir() / f"gigamate-usbc-power-{os.getuid()}.json"


def write_state_file(data: dict, path: Optional[Path] = None) -> None:
    target = path if path is not None else state_file_path()
    tmp = target.with_name(f"{target.name}.tmp.{os.getpid()}")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        fd = os.open(tmp, flags, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        os.replace(tmp, target)
    except OSError:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def read_state_file(path: Optional[Path] = None) -> dict:
    target = path if path is not None else state_file_path()
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except (OSError, ValueError):
        return {}


def build_trim_ladder(
    target_gpu_w: int,
    target_cpu_w: int,
    env: BudgetEnvelope,
) -> list[Tuple[int, int, int]]:
    """Return progressive ``(gpu_w, cpu_w, aux_state)`` steps for sustained battery drain."""
    steps: list[Tuple[int, int, int]] = [(target_gpu_w, target_cpu_w, AUX_P0)]

    cur_cpu = target_cpu_w
    if env.has_cpu_ppt_ctrl:
        while cur_cpu > env.cpu_floor_w:
            cur_cpu = max(env.cpu_floor_w, cur_cpu - 5)
            steps.append((target_gpu_w, cur_cpu, AUX_P0))

    cur_gpu = target_gpu_w
    if env.has_ctgp_ctrl:
        while cur_gpu > env.gpu_min_w:
            cur_gpu = max(env.gpu_min_w, cur_gpu - 5)
            steps.append((cur_gpu, cur_cpu, AUX_P0))

    steps.append((min(30, target_gpu_w), env.cpu_floor_w, AUX_P1))
    steps.append((min(20, target_gpu_w), env.cpu_floor_w, AUX_P2))

    deduped: list[Tuple[int, int, int]] = []
    for step in steps:
        if not deduped or deduped[-1] != step:
            deduped.append(step)
    return deduped


class UsbcPowerBudgetController:
    """Closed-loop daemon controller for opt-in USB-C PD power budgeting."""

    def __init__(
        self,
        *,
        acpi_getter: Callable[[], Optional[AcpiController]],
        power_detector: Callable[[], PowerSourceSnapshot] = detect_power_source,
        gpu_state_getter: Callable[[], GpuState] = get_gpu_state,
        gpu_caps_getter: Callable[..., GpuPowerCaps] = read_gpu_power_caps,
        aux_setter: Callable[..., Tuple[bool, str]] = set_nvidia_aux_power_state,
        clock: Callable[[], float] = time.monotonic,
        state_path: Optional[Path] = None,
        publish_state: bool = True,
    ) -> None:
        self._acpi_getter = acpi_getter
        self._power_detector = power_detector
        self._gpu_state_getter = gpu_state_getter
        self._gpu_caps_getter = gpu_caps_getter
        self._aux_setter = aux_setter
        self._clock = clock
        self._state_path = state_path
        self._publish_state = publish_state

        self._usbc_attached_at: Optional[float] = None
        self._reassert_done = False
        self._applied = False
        self._applied_gpu_w: Optional[int] = None
        self._applied_cpu_w: Optional[int] = None
        self._applied_aux_state: Optional[int] = None
        self._last_applied_profile: Optional[int] = None
        self._trim_level = 0
        self._drain_since: Optional[float] = None
        self._recover_since: Optional[float] = None
        self._last_trim_at = 0.0
        self._last_error = ""
        self._last_state: dict = {}

    @property
    def fast_poll_needed(self) -> bool:
        return bool(
            self._last_state.get("enabled")
            and self._last_state.get("power_source") == "usbc_pd"
            and self._last_state.get("mode") in ("settling", "active", "auto_trimmed")
        )

    def _restore_stock(
        self,
        cfg: dict,
        source: PowerSourceSnapshot,
        gpu: GpuState,
        acpi: Optional[AcpiController],
        active_profile: Optional[int],
    ) -> None:
        enabled = bool(cfg.get("usbc_power_budget_enabled", False))
        restore_prof: Optional[int] = active_profile
        if restore_prof is None:
            raw_cfg_prof = cfg.get("acpi_profile")
            if raw_cfg_prof is not None:
                try:
                    restore_prof = int(raw_cfg_prof)
                except (TypeError, ValueError):
                    restore_prof = None
        if restore_prof is None and acpi is not None and acpi.available:
            cur_p = acpi.get_profile()
            restore_prof = cur_p.value if cur_p is not None else None
        if restore_prof is None or not (0 <= restore_prof <= 3):
            restore_prof = 1

        if acpi is not None and acpi.available:
            try:
                # Call set_profile directly so WMBD(0xED) reloads stock EC PPT/NPCF limits.
                acpi.set_profile(FanProfile(restore_prof))
            except Exception as exc:  # noqa: BLE001
                logger.warning("usbc_power: failed restoring ACPI profile %s: %s", restore_prof, exc)

        gpu_active = bool(gpu.present and gpu.vendor == "nvidia" and not gpu.gated_off and gpu.status == "active")
        if gpu_active:
            if source.kind == "barrel_ac" and self._applied_aux_state != AUX_P0:
                self._aux_setter(AUX_P0, gpu_state=gpu)
            elif not enabled and source.kind == "usbc_pd":
                self._aux_setter(AUX_P1, gpu_state=gpu)

    def _reset_applied_tracking(self) -> None:
        self._applied = False
        self._applied_gpu_w = None
        self._applied_cpu_w = None
        self._applied_aux_state = None
        self._last_applied_profile = None
        self._trim_level = 0
        self._drain_since = None
        self._recover_since = None
        self._reassert_done = False

    def shutdown(self, cfg: dict, *, active_profile: Optional[int] = None) -> None:
        """Restore stock firmware limits on daemon shutdown or system sleep."""
        if not self._applied:
            return
        try:
            source = self._power_detector()
        except Exception:
            source = PowerSourceSnapshot(
                kind="battery",
                ac_online=False,
                usbc_online=False,
                is_pd=False,
                detected_wattage_w=None,
                wattage_source="unknown",
                negotiated_current_a=None,
                negotiated_voltage_v=None,
                firmware_usbc_flag=None,
                bat_present=False,
                bat_status="Unknown",
                bat_capacity=None,
                bat_power_w=None,
            )
        try:
            gpu = self._gpu_state_getter()
        except Exception:
            gpu = GpuState(present=False)
        self._restore_stock(cfg, source, gpu, self._acpi_getter(), active_profile)
        self._reset_applied_tracking()

    def _update_trim_level(self, now: float, source: PowerSourceSnapshot, max_level: int) -> None:
        self._trim_level = max(0, min(self._trim_level, max_level))
        critical_low = (
            source.bat_present
            and source.bat_capacity is not None
            and source.bat_capacity < CRITICAL_BAT_PCT
        )
        draining = critical_low or (
            source.bat_present
            and source.bat_status.lower() == "discharging"
            and source.bat_power_w is not None
            and source.bat_power_w <= -DRAIN_THRESHOLD_W
        )
        if draining:
            self._recover_since = None
            if self._drain_since is None:
                self._drain_since = now
            sustained = critical_low or ((now - self._drain_since) >= DRAIN_SUSTAIN_SEC)
            cooled = (now - self._last_trim_at) >= TRIM_COOLDOWN_SEC or self._last_trim_at == 0.0
            if sustained and cooled and self._trim_level < max_level:
                self._trim_level += 1
                self._last_trim_at = now
                self._drain_since = now
            return

        self._drain_since = None
        if self._trim_level > 0:
            power_ok = source.bat_power_w is None or source.bat_power_w >= -0.5
            cap_ok = source.bat_capacity is None or source.bat_capacity >= CRITICAL_BAT_PCT + 3
            if power_ok and cap_ok:
                if self._recover_since is None:
                    self._recover_since = now
                if (now - self._recover_since) >= RECOVER_SUSTAIN_SEC:
                    self._trim_level -= 1
                    self._recover_since = now
            else:
                self._recover_since = None
        else:
            self._recover_since = None

    def tick(
        self,
        cfg: dict,
        *,
        gpu: Optional[GpuState] = None,
        active_profile: Optional[int] = None,
        apply_hardware: bool = True,
    ) -> dict:
        now = self._clock()
        enabled = bool(cfg.get("usbc_power_budget_enabled", False))
        source = self._power_detector()
        gpu_st = gpu if gpu is not None else self._gpu_state_getter()
        acpi = self._acpi_getter()
        caps = self._gpu_caps_getter(gpu_state=gpu_st)

        has_acpi = bool(acpi is not None and acpi.available)
        has_cpu_ppt = has_acpi and acpi.has_cpu_ppt_control()
        has_ctgp = has_acpi and acpi.has_gpu_ctgp_control()
        has_dyn_boost = has_acpi and acpi.has_gpu_dynamic_boost_control()
        has_nv = bool(gpu_st.present and gpu_st.vendor == "nvidia")

        buf_pct = int(cfg.get("usbc_buffer_pct", DEFAULT_BUFFER_PCT) or DEFAULT_BUFFER_PCT)
        env = compute_budget_envelope(
            source.detected_wattage_w,
            caps.default_tgp_w,
            caps.max_constraint_w,
            has_ctgp_ctrl=has_ctgp,
            has_cpu_ppt_ctrl=has_cpu_ppt,
            has_dyn_boost_ctrl=has_dyn_boost,
            has_nvidia_gpu=has_nv,
            buffer_pct=buf_pct,
        )

        pd_ready = bool(source.kind == "usbc_pd" and source.ac_online)
        if pd_ready:
            if self._usbc_attached_at is None:
                self._usbc_attached_at = now
                self._reassert_done = False
        else:
            self._usbc_attached_at = None
            self._reassert_done = False

        desired_raw = int(cfg.get("usbc_gpu_budget_w", env.default_gpu_w or 55) or 55)
        if env.supported:
            target_gpu_w, target_cpu_w = clamp_split(desired_raw, env)
            ladder = build_trim_ladder(target_gpu_w, target_cpu_w, env)
        else:
            target_gpu_w = env.gpu_min_w
            target_cpu_w = env.cpu_floor_w
            ladder = []

        should_engage = bool(
            enabled
            and pd_ready
            and env.supported
            and has_nv
            and not gpu_st.gated_off
            and gpu_st.status == "active"
        )

        trim_max_level = max(0, len(ladder) - 1)
        mode = "off"

        if not should_engage:
            if apply_hardware and self._applied:
                self._restore_stock(cfg, source, gpu_st, acpi, active_profile)
                self._reset_applied_tracking()
            elif not apply_hardware and self._applied and not enabled:
                self._reset_applied_tracking()

            if not enabled:
                mode = "off"
            elif source.kind == "battery":
                mode = "standby_battery"
            elif source.kind == "barrel_ac":
                mode = "standby_barrel"
            elif not has_nv:
                mode = "unsupported_gpu"
            elif gpu_st.gated_off:
                mode = "standby_gpu_gated"
            elif not env.supported:
                mode = (
                    "unsupported_charger"
                    if env.reason in ("charger_too_small", "unknown_charger_wattage")
                    else "unsupported_gpu"
                )
            elif not source.ac_online:
                mode = "settling"
            else:
                mode = "standby_gpu_asleep"
        else:
            attached_for = now - (self._usbc_attached_at or now)
            if attached_for < ATTACH_SETTLE_SEC:
                mode = "settling"
            else:
                if self._applied:
                    self._update_trim_level(now, source, trim_max_level)
                eff_gpu_w, eff_cpu_w, eff_aux = ladder[min(self._trim_level, trim_max_level)]

                if apply_hardware:
                    acpi_drift = False
                    if acpi is not None and acpi.available:
                        if env.has_dyn_boost_ctrl and acpi.read_npcf_dynamic_boost() is True:
                            acpi_drift = True
                        expected_off = max(0, eff_gpu_w - env.gpu_min_w)
                        if env.has_ctgp_ctrl and acpi.read_npcf_ctgp_offset_w() != expected_off:
                            acpi_drift = True

                    reassert_due = (not self._reassert_done) and (attached_for >= ATTACH_REASSERT_SEC)
                    need_apply = (
                        not self._applied
                        or (eff_gpu_w, eff_cpu_w, eff_aux)
                        != (self._applied_gpu_w, self._applied_cpu_w, self._applied_aux_state)
                        or (active_profile is not None and active_profile != self._last_applied_profile)
                        or acpi_drift
                        or reassert_due
                    )

                    if need_apply:
                        # Constrain EC/CPU/Dynamic-Boost BEFORE unlocking NVRM AUX P0.
                        if acpi is not None and acpi.available:
                            if not env.has_ctgp_ctrl and acpi.get_profile() != FanProfile.QUIET:
                                acpi.set_profile(FanProfile.QUIET)
                            if env.has_dyn_boost_ctrl:
                                acpi.set_gpu_dynamic_boost(False)
                            if env.has_ctgp_ctrl:
                                acpi.set_gpu_ctgp_offset(max(0, eff_gpu_w - env.gpu_min_w))
                            if env.has_cpu_ppt_ctrl:
                                acpi.set_cpu_ppt_limits(eff_cpu_w, eff_cpu_w, eff_cpu_w)

                        ok, err = self._aux_setter(eff_aux, gpu_state=gpu_st)
                        if ok:
                            self._applied = True
                            self._applied_gpu_w = eff_gpu_w
                            self._applied_cpu_w = eff_cpu_w
                            self._applied_aux_state = eff_aux
                            self._last_applied_profile = active_profile
                            self._last_error = ""
                            if attached_for >= ATTACH_REASSERT_SEC:
                                self._reassert_done = True
                        else:
                            self._applied = False
                            self._last_error = err

                if self._applied:
                    mode = "auto_trimmed" if self._trim_level > 0 else "active"
                elif apply_hardware:
                    mode = "error"
                else:
                    mode = "settling"

        snapshot = {
            "mode": mode,
            "enabled": enabled,
            "active": bool(self._applied and should_engage),
            "power_source": source.kind,
            "ac_online": source.ac_online,
            "usbc_online": source.usbc_online,
            "detected_wattage_w": source.detected_wattage_w,
            "wattage_source": source.wattage_source,
            "negotiated_current_a": source.negotiated_current_a,
            "buffer_pct": env.buffer_pct,
            "buffer_w": env.buffer_w,
            "silicon_budget_w": env.silicon_budget_w,
            "gpu_min_w": env.gpu_min_w,
            "gpu_max_w": env.gpu_max_w,
            "target_gpu_w": target_gpu_w,
            "target_cpu_w": target_cpu_w,
            "applied_gpu_w": self._applied_gpu_w if self._applied else None,
            "applied_cpu_w": self._applied_cpu_w if self._applied else None,
            "aux_state": self._applied_aux_state if self._applied else None,
            "trim_level": self._trim_level,
            "trim_max_level": trim_max_level,
            "bat_present": source.bat_present,
            "bat_status": source.bat_status,
            "bat_capacity": source.bat_capacity,
            "bat_power_w": source.bat_power_w,
            "default_tgp_w": caps.default_tgp_w,
            "max_constraint_w": caps.max_constraint_w,
            "has_ctgp_ctrl": env.has_ctgp_ctrl,
            "has_cpu_ppt_ctrl": env.has_cpu_ppt_ctrl,
            "has_dyn_boost_ctrl": env.has_dyn_boost_ctrl,
            "reason": env.reason,
            "last_error": self._last_error,
            "updated_ts": time.time(),
        }
        self._last_state = snapshot
        if self._publish_state:
            write_state_file(snapshot, self._state_path)
        return snapshot
