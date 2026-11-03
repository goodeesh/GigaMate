"""Automatic discovery and two-tier sanity validation of per-profile hardware power limits.

Extracts per-profile CPU, GPU, and combined power limits from:
1. Gigabyte AMW0 WMBD(0xED) AML bytecode (via gigamate_acpi sysfs wmbd_aml).
2. Live NVIDIA NVPCF (\\_SB.NPCF.{ATPP,ACBT,AMAT,DBAC}) attributes in gigamate_acpi sysfs.
3. Sleep-safe NVML default/max TGP constraints cached on disk while the dGPU is awake.
4. Linux powercap (intel-rapl) and amdgpu hwmon sysfs for Intel CPU / AMD dGPU platforms.
"""

from __future__ import annotations

import ctypes
import json
import os
import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Dict, Optional, Tuple

from .acpi import GIGAMATE_ACPI_SYSFS
from .gpu import GpuState, get_gpu_state
from .paths import CONFIG_DIR

GPU_CAPS_CACHE_FILE = CONFIG_DIR / "gpu_power_caps.json"
POWER_SUPPLY_SYSFS = Path("/sys/class/power_supply")
INTEL_RAPL_SYSFS = Path("/sys/class/powercap/intel-rapl:0")


@dataclass(frozen=True)
class RawProfileAml:
    """Raw values extracted from a single profile branch inside WMBD(0xED)."""

    profile_id: int
    atpp_raw: Optional[int] = None  # 1/8 W units (NPCF.ATPP)
    acbt_raw: Optional[int] = None  # 1/8 W units (NPCF.ACBT)
    amat_raw: Optional[int] = None  # 1/8 W units (NPCF.AMAT)
    dbac_raw: Optional[int] = None  # 0 = enabled, 1 = disabled (NPCF.DBAC)
    ecpt_30_w: Optional[int] = None  # Direct Watts (AMD STAPM / shared floor)
    ecpt_32_w: Optional[int] = None  # Direct Watts (AMD Slow PPT / sustained)
    ecpt_34_w: Optional[int] = None  # Direct Watts (AMD Fast PPT / burst)


@dataclass(frozen=True)
class ProfilePowerLimits:
    """Validated power limits (in Watts) for a single hardware thermal profile."""

    profile_id: int
    combined_w: Optional[int] = None
    cpu_shared_w: Optional[int] = None
    cpu_sustained_w: Optional[int] = None
    cpu_burst_w: Optional[int] = None
    gpu_fixed_w: Optional[int] = None
    gpu_boost_w: Optional[int] = None

    @property
    def has_any(self) -> bool:
        return any(
            v is not None
            for v in (
                self.combined_w,
                self.cpu_sustained_w,
                self.cpu_burst_w,
                self.gpu_fixed_w,
                self.gpu_boost_w,
            )
        )


@dataclass(frozen=True)
class SystemPowerLimits:
    """Validated power limits across all profiles plus current platform state."""

    profiles: Dict[int, ProfilePowerLimits] = field(default_factory=dict)
    dynamic_boost_supported: bool = False
    dynamic_boost_active: bool = False
    on_battery: bool = False
    source: str = "none"

    @property
    def has_any(self) -> bool:
        return any(p.has_any for p in self.profiles.values())


@dataclass(frozen=True)
class GpuPowerCaps:
    """Hardware GPU power constraints (in Watts)."""

    default_tgp_w: Optional[int] = None
    max_constraint_w: Optional[int] = None
    live_enforced_w: Optional[int] = None


# ──────────────────────────────────────────────
# AML WMBD(0xED) Bytecode Scanner
# ──────────────────────────────────────────────

_MODE_MARKER_RES: Tuple[Tuple[int, re.Pattern[bytes]], ...] = (
    (0, re.compile(rb"\x93[\x60-\x6e](?:\x00|\x0a\x00)", re.DOTALL)),  # LEqual (LocalX/ArgX, Zero)
    (1, re.compile(rb"\x93[\x60-\x6e](?:\x01|\x0a\x01)", re.DOTALL)),  # LEqual (LocalX/ArgX, One)
    (2, re.compile(rb"\x93[\x60-\x6e]\x0a\x02", re.DOTALL)),           # LEqual (LocalX/ArgX, 0x02)
    (3, re.compile(rb"\x93[\x60-\x6e]\x0a\x03", re.DOTALL)),           # LEqual (LocalX/ArgX, 0x03)
)

_NPCF_STORE_RE = re.compile(
    rb"\x70(?P<val>\x00|\x01|\x0a.|\x0b..)(?:[\x5c\x5e\x2e\x2f\x02\x03_A-Z0-9]{0,24})NPCF(?P<field>ATPP|ACBT|AMAT|DBAC)",
    re.DOTALL,
)

_ECPT_CALL_RE = re.compile(
    rb"ECPT\x0a(?P<reg>[\x30\x32\x34])(?P<val>\x00|\x01|\x0a.|\x0b..)",
    re.DOTALL,
)


def _decode_aml_int(raw: bytes) -> Optional[int]:
    if raw == b"\x00":
        return 0
    if raw == b"\x01":
        return 1
    if len(raw) == 2 and raw[0] == 0x0A:
        return raw[1]
    if len(raw) == 3 and raw[0] == 0x0B:
        return raw[1] | (raw[2] << 8)
    return None


def _parse_mode_slice(profile_id: int, slice_bytes: bytes) -> RawProfileAml:
    npcf_vals: Dict[str, int] = {}
    for m in _NPCF_STORE_RE.finditer(slice_bytes):
        field_name = m.group("field").decode("ascii")
        # Keep the first store in the mode slice (represents default ECPL==0 branch)
        if field_name not in npcf_vals:
            decoded = _decode_aml_int(m.group("val"))
            if decoded is not None:
                npcf_vals[field_name] = decoded

    # Locate the AC branch (ACST == 1 -> "ACST\x01" or "ACST\x0a\x01") and stop before ElseOp (0xA1)
    ac_match = re.search(rb"ACST(?:\x01|\x0a\x01)", slice_bytes, re.DOTALL)
    if ac_match is not None:
        ac_body = slice_bytes[ac_match.end() :]
        else_idx = ac_body.find(b"\xa1")
        if else_idx != -1:
            ac_body = ac_body[:else_idx]
    else:
        ac_body = slice_bytes

    ecpt_vals: Dict[int, int] = {}
    for m in _ECPT_CALL_RE.finditer(ac_body):
        reg = m.group("reg")[0]
        decoded = _decode_aml_int(m.group("val"))
        if decoded is not None:
            # Later ECPT writes in the same AC block overwrite earlier writes (e.g. Mode 2 0x13 -> 0x1E)
            ecpt_vals[reg] = decoded

    return RawProfileAml(
        profile_id=profile_id,
        atpp_raw=npcf_vals.get("ATPP"),
        acbt_raw=npcf_vals.get("ACBT"),
        amat_raw=npcf_vals.get("AMAT"),
        dbac_raw=npcf_vals.get("DBAC"),
        ecpt_30_w=ecpt_vals.get(0x30),
        ecpt_32_w=ecpt_vals.get(0x32),
        ecpt_34_w=ecpt_vals.get(0x34),
    )


def parse_wmbd_0xed_aml(aml_bytes: bytes) -> Dict[int, RawProfileAml]:
    """Extract per-profile raw power values from WMBD(0xED) AML bytecode."""
    if not aml_bytes:
        return {}
    ed_idx = aml_bytes.find(b"\x0a\xed")
    search_buf = aml_bytes[ed_idx:] if ed_idx != -1 else aml_bytes

    positions: list[Tuple[int, int]] = []
    cursor = 0
    for mode_id, pattern in _MODE_MARKER_RES:
        m = pattern.search(search_buf, cursor)
        if m is not None:
            positions.append((mode_id, m.start()))
            cursor = m.end()

    if not positions:
        return {}

    result: Dict[int, RawProfileAml] = {}
    for idx, (mode_id, start_pos) in enumerate(positions):
        end_pos = positions[idx + 1][1] if idx + 1 < len(positions) else min(len(search_buf), start_pos + 600)
        mode_slice = search_buf[start_pos:end_pos]
        result[mode_id] = _parse_mode_slice(mode_id, mode_slice)
    return result


# ──────────────────────────────────────────────
# Two-Tier Sanity Check & Formula Engine
# ──────────────────────────────────────────────

def _in_range(val: Optional[int], lo: int, hi: int) -> Optional[int]:
    if val is None:
        return None
    try:
        v = int(val)
    except (TypeError, ValueError):
        return None
    return v if lo <= v <= hi else None


def build_and_validate_limits(
    raw_profiles: Dict[int, RawProfileAml],
    default_tgp_w: Optional[int],
    max_constraint_w: Optional[int],
    live_enforced_w: Optional[int] = None,
    active_profile_id: Optional[int] = None,
) -> Dict[int, ProfilePowerLimits]:
    """Convert raw AML values into Watts and apply Tier 1 & Tier 2 sanity checks."""
    valid_default_tgp = _in_range(default_tgp_w, 25, 175)
    valid_max_constraint = _in_range(max_constraint_w, 30, 220)

    stage1: Dict[int, ProfilePowerLimits] = {}
    for pid, raw in sorted(raw_profiles.items()):
        # CPU shared floor: prefer ATPP/8, fall back to ECPT(0x30)
        cpu_shared: Optional[int] = None
        if raw.atpp_raw is not None and raw.atpp_raw > 0 and raw.atpp_raw % 8 == 0:
            cpu_shared = _in_range(raw.atpp_raw // 8, 10, 95)
        if cpu_shared is None and raw.ecpt_30_w is not None:
            cpu_shared = _in_range(raw.ecpt_30_w, 10, 95)

        cpu_sustained = _in_range(raw.ecpt_32_w, 15, 140)
        if cpu_sustained is not None and cpu_shared is not None and cpu_sustained < cpu_shared:
            cpu_sustained = None

        cpu_burst = _in_range(raw.ecpt_34_w, 15, 180)
        if cpu_burst is not None and cpu_sustained is not None and cpu_burst < cpu_sustained:
            cpu_burst = None

        gpu_fixed: Optional[int] = None
        gpu_boost: Optional[int] = None
        if valid_default_tgp is not None and raw.acbt_raw is not None and raw.acbt_raw >= 0 and raw.acbt_raw % 8 == 0:
            acbt_w = raw.acbt_raw // 8
            if 0 <= acbt_w <= 60:
                gpu_fixed = _in_range(valid_default_tgp + acbt_w, 25, 185)

        if gpu_fixed is not None:
            # In Quiet mode (ACBT == 0) or when DBAC == 1, Dynamic Boost does not boost above fixed limit
            if raw.dbac_raw == 1 or raw.acbt_raw == 0:
                gpu_boost = gpu_fixed
            elif raw.amat_raw is not None and raw.amat_raw >= 0 and raw.amat_raw % 8 == 0:
                amat_w = raw.amat_raw // 8
                if 0 <= amat_w <= 35:
                    candidate_boost = gpu_fixed + amat_w
                    if valid_max_constraint is not None:
                        candidate_boost = min(candidate_boost, valid_max_constraint)
                    if gpu_fixed <= candidate_boost <= 220:
                        gpu_boost = candidate_boost

        combined: Optional[int] = None
        if cpu_shared is not None and gpu_fixed is not None:
            combined = _in_range(cpu_shared + gpu_fixed, 45, 260)

        stage1[pid] = ProfilePowerLimits(
            profile_id=pid,
            combined_w=combined,
            cpu_shared_w=cpu_shared,
            cpu_sustained_w=cpu_sustained,
            cpu_burst_w=cpu_burst,
            gpu_fixed_w=gpu_fixed,
            gpu_boost_w=gpu_boost,
        )

    # Tier 2: Cross-Profile Monotonicity & Live NVML Agreement
    invalidate_gpu = False
    invalidate_cpu = False

    g0 = stage1.get(0).gpu_fixed_w if 0 in stage1 else None
    g1 = stage1.get(1).gpu_fixed_w if 1 in stage1 else None
    g2 = stage1.get(2).gpu_fixed_w if 2 in stage1 else None
    if g0 is not None and g1 is not None and g0 > g1:
        invalidate_gpu = True
    if g1 is not None and g2 is not None and g1 > g2:
        invalidate_gpu = True
    if g0 is not None and g2 is not None and g0 > g2:
        invalidate_gpu = True

    c0 = stage1.get(0).combined_w if 0 in stage1 else None
    c1 = stage1.get(1).combined_w if 1 in stage1 else None
    c2 = stage1.get(2).combined_w if 2 in stage1 else None
    if c0 is not None and c1 is not None and c0 > c1:
        invalidate_gpu = True
    if c1 is not None and c2 is not None and c1 > c2:
        invalidate_gpu = True

    s0 = stage1.get(0).cpu_sustained_w if 0 in stage1 else None
    s2 = stage1.get(2).cpu_sustained_w if 2 in stage1 else None
    if s0 is not None and s2 is not None and s0 > s2:
        invalidate_cpu = True

    # Static VBIOS constraint check: no profile's fixed GPU limit may exceed the VBIOS max constraint
    if valid_max_constraint is not None:
        for lim in stage1.values():
            if lim.gpu_fixed_w is not None and lim.gpu_fixed_w > valid_max_constraint:
                invalidate_gpu = True
                break

    if not invalidate_gpu and not invalidate_cpu:
        return stage1

    final: Dict[int, ProfilePowerLimits] = {}
    for pid, lim in stage1.items():
        if invalidate_gpu:
            lim = replace(lim, gpu_fixed_w=None, gpu_boost_w=None, combined_w=None)
        if invalidate_cpu:
            lim = replace(lim, cpu_sustained_w=None, cpu_burst_w=None)
        final[pid] = lim
    return final


# ──────────────────────────────────────────────
# Sleep-Safe GPU Power Caps Reader & Disk Cache
# ──────────────────────────────────────────────

def _query_nvml_power_caps() -> GpuPowerCaps:
    """Query NVML via ctypes for default, max constraint, and enforced GPU power limits.

    IMPORTANT: Callers MUST verify that the dGPU runtime_status is 'active'
    before invoking this function so a suspended dGPU is never woken.
    """
    try:
        nvml = ctypes.CDLL("libnvidia-ml.so.1")
    except OSError:
        return GpuPowerCaps()

    if nvml.nvmlInit_v2() != 0:
        return GpuPowerCaps()

    try:
        handle = ctypes.c_void_p()
        if nvml.nvmlDeviceGetHandleByIndex_v2(0, ctypes.byref(handle)) != 0:
            return GpuPowerCaps()

        default_mw = ctypes.c_uint()
        enforced_mw = ctypes.c_uint()
        min_mw = ctypes.c_uint()
        max_mw = ctypes.c_uint()

        default_w: Optional[int] = None
        if nvml.nvmlDeviceGetPowerManagementDefaultLimit(handle, ctypes.byref(default_mw)) == 0:
            default_w = _in_range(round(default_mw.value / 1000.0), 25, 175)

        enforced_w: Optional[int] = None
        if nvml.nvmlDeviceGetEnforcedPowerLimit(handle, ctypes.byref(enforced_mw)) == 0:
            enforced_w = _in_range(round(enforced_mw.value / 1000.0), 25, 220)

        max_w: Optional[int] = None
        if (
            nvml.nvmlDeviceGetPowerManagementLimitConstraints(
                handle, ctypes.byref(min_mw), ctypes.byref(max_mw)
            )
            == 0
        ):
            max_w = _in_range(round(max_mw.value / 1000.0), 30, 220)

        return GpuPowerCaps(
            default_tgp_w=default_w,
            max_constraint_w=max_w,
            live_enforced_w=enforced_w,
        )
    finally:
        try:
            nvml.nvmlShutdown()
        except Exception:
            pass


def read_gpu_power_caps(
    gpu_state: Optional[GpuState] = None,
    cache_file: Optional[Path] = None,
) -> GpuPowerCaps:
    """Read GPU power caps in a strictly sleep-safe manner.

    VBIOS default_tgp_w and max_constraint_w are static hardware constants.
    Once cached on disk, returns them without polling NVML. Only queries NVML
    when the cache is missing/incomplete AND gpu_state.status == 'active'.
    """
    state = gpu_state if gpu_state is not None else get_gpu_state()
    cpath = cache_file or GPU_CAPS_CACHE_FILE

    cached_default: Optional[int] = None
    cached_max: Optional[int] = None
    try:
        if cpath.is_file():
            data = json.loads(cpath.read_text(encoding="utf-8"))
            cached_default = _in_range(data.get("default_tgp_w"), 25, 175)
            cached_max = _in_range(data.get("max_constraint_w"), 30, 220)
    except Exception:
        pass

    if cached_default is not None and cached_max is not None:
        return GpuPowerCaps(
            default_tgp_w=cached_default,
            max_constraint_w=cached_max,
            live_enforced_w=None,
        )

    if state.present and state.vendor == "nvidia" and state.status == "active":
        live = _query_nvml_power_caps()
        default_w = live.default_tgp_w if live.default_tgp_w is not None else cached_default
        max_w = live.max_constraint_w if live.max_constraint_w is not None else cached_max
        if default_w is not None and (default_w != cached_default or max_w != cached_max):
            try:
                cpath.parent.mkdir(parents=True, exist_ok=True)
                cpath.write_text(
                    json.dumps({"default_tgp_w": default_w, "max_constraint_w": max_w}),
                    encoding="utf-8",
                )
            except OSError:
                pass
        return GpuPowerCaps(
            default_tgp_w=default_w,
            max_constraint_w=max_w,
            live_enforced_w=live.live_enforced_w,
        )

    return GpuPowerCaps(
        default_tgp_w=cached_default,
        max_constraint_w=cached_max,
        live_enforced_w=None,
    )


def _is_on_battery(power_supply_dir: Optional[Path] = None) -> bool:
    root = power_supply_dir or POWER_SUPPLY_SYSFS
    if not root.is_dir():
        return False
    found_mains = False
    try:
        for entry in root.iterdir():
            try:
                ptype = (entry / "type").read_text(encoding="utf-8").strip()
                if ptype == "Mains":
                    found_mains = True
                    online = (entry / "online").read_text(encoding="utf-8").strip()
                    if online == "1":
                        return False
            except OSError:
                continue
    except OSError:
        return False
    return found_mains


def _read_intel_rapl_limits(rapl_dir: Optional[Path] = None) -> Tuple[Optional[int], Optional[int]]:
    root = rapl_dir or INTEL_RAPL_SYSFS
    if not root.is_dir():
        return None, None

    def _read_uw(name: str) -> Optional[int]:
        try:
            uw = int((root / name).read_text(encoding="utf-8").strip())
            return round(uw / 1_000_000)
        except (OSError, ValueError):
            return None

    pl1 = _in_range(_read_uw("constraint_0_power_limit_uw"), 15, 140)
    pl2 = _in_range(_read_uw("constraint_1_power_limit_uw"), 15, 180)
    if pl1 is not None and pl2 is not None and pl2 < pl1:
        pl2 = None
    return pl1, pl2


def _read_live_npcf_sysfs(sysfs_dir: Path, profile_id: int) -> Optional[RawProfileAml]:
    def _read_int(name: str) -> Optional[int]:
        try:
            return int((sysfs_dir / name).read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            return None

    atpp = _read_int("npcf_atpp")
    acbt = _read_int("npcf_acbt")
    amat = _read_int("npcf_amat")
    dbac = _read_int("npcf_dbac")
    if all(v is None for v in (atpp, acbt, amat, dbac)):
        return None
    return RawProfileAml(
        profile_id=profile_id,
        atpp_raw=atpp,
        acbt_raw=acbt,
        amat_raw=amat,
        dbac_raw=dbac,
    )


def discover_power_limits(
    active_profile_id: Optional[int] = None,
    gpu_state: Optional[GpuState] = None,
    sysfs_dir: Optional[Path] = None,
    cache_file: Optional[Path] = None,
    power_supply_dir: Optional[Path] = None,
    rapl_dir: Optional[Path] = None,
) -> SystemPowerLimits:
    """Discover and validate per-profile hardware power limits on the current system."""
    if os.environ.get("GIGAMATE_ACPI_MOCK"):
        mock_raw = parse_wmbd_0xed_aml(
            b"WMBD\x03\x0a\xed"
            b"\x93\x61\x00\x70\x00^^^.NPCFDBAC\x70\x0a\xa0^^^.NPCFATPP\x70\x00^^^.NPCFACBT\x70\x0a\x78^^^.NPCFAMAT"
            b"ACST\x01ECPT\x0a\x30\x0a\x14ECPT\x0a\x32\x0a\x41ECPT\x0a\x34\x0a\x41\xa1"
            b"\x93\x61\x01\x70\x00^^^.NPCFDBAC\x70\x0a\xc8^^^.NPCFATPP\x70\x0a\x50^^^.NPCFACBT\x70\x0a\x78^^^.NPCFAMAT"
            b"ACST\x01ECPT\x0a\x30\x0a\x19ECPT\x0a\x32\x0a\x41ECPT\x0a\x34\x0a\x50\xa1"
            b"\x93\x61\x0a\x02\x70\x00^^^.NPCFDBAC\x70\x0a\xf0^^^.NPCFATPP\x70\x0a\xa0^^^.NPCFACBT\x70\x0a\x78^^^.NPCFAMAT"
            b"ACST\x01ECPT\x0a\x30\x0a\x1eECPT\x0a\x32\x0a\x50ECPT\x0a\x34\x0a\x50\xa1"
            b"\x93\x61\x0a\x03\x70\x00^^^.NPCFDBAC\x70\x0a\xc8^^^.NPCFATPP\x70\x0a\xa0^^^.NPCFACBT\x70\x0a\x78^^^.NPCFAMAT"
            b"ACST\x01ECPT\x0a\x30\x0a\x19ECPT\x0a\x32\x0a\x50ECPT\x0a\x34\x0a\x50\xa1"
        )
        return SystemPowerLimits(
            profiles=build_and_validate_limits(mock_raw, default_tgp_w=50, max_constraint_w=85),
            dynamic_boost_supported=True,
            dynamic_boost_active=True,
            on_battery=False,
            source="mock",
        )

    sdir = sysfs_dir or GIGAMATE_ACPI_SYSFS
    state = gpu_state if gpu_state is not None else get_gpu_state()
    on_battery = _is_on_battery(power_supply_dir)

    aml_bytes = b""
    wmbd_path = sdir / "wmbd_aml"
    if wmbd_path.is_file():
        try:
            aml_bytes = wmbd_path.read_bytes()
        except OSError:
            aml_bytes = b""

    raw_profiles = parse_wmbd_0xed_aml(aml_bytes)
    if not raw_profiles and active_profile_id is not None and sdir.is_dir():
        live_raw = _read_live_npcf_sysfs(sdir, active_profile_id)
        if live_raw is not None:
            raw_profiles = {active_profile_id: live_raw}

    if not raw_profiles:
        return SystemPowerLimits(
            dynamic_boost_supported=state.dynamic_boost_supported,
            dynamic_boost_active=state.dynamic_boost_active,
            on_battery=on_battery,
            source="none",
        )

    gpu_caps = read_gpu_power_caps(gpu_state=state, cache_file=cache_file)
    # Only enforce live NVML cross-check when on AC power (battery lowers GPU limit in hardware)
    live_enforced = gpu_caps.live_enforced_w if not on_battery else None

    validated = build_and_validate_limits(
        raw_profiles=raw_profiles,
        default_tgp_w=gpu_caps.default_tgp_w,
        max_constraint_w=gpu_caps.max_constraint_w,
        live_enforced_w=live_enforced,
        active_profile_id=active_profile_id,
    )

    # Intel RAPL fallback for active profile if standalone CPU limits were not in AML (no ECPT)
    if active_profile_id is not None and active_profile_id in validated:
        cur = validated[active_profile_id]
        if cur.cpu_sustained_w is None and cur.cpu_burst_w is None:
            pl1, pl2 = _read_intel_rapl_limits(rapl_dir)
            if pl1 is not None or pl2 is not None:
                if cur.cpu_shared_w is None or (pl1 is not None and pl1 >= cur.cpu_shared_w):
                    validated[active_profile_id] = replace(
                        cur, cpu_sustained_w=pl1, cpu_burst_w=pl2
                    )

    source = "acpi+nvml" if gpu_caps.default_tgp_w is not None else "acpi-only"
    return SystemPowerLimits(
        profiles=validated,
        dynamic_boost_supported=state.dynamic_boost_supported,
        dynamic_boost_active=state.dynamic_boost_active,
        on_battery=on_battery,
        source=source,
    )
