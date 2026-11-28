# dGPU Power Gate (AI Power Gear III) Design

**Date:** 2026-11-22  
**Status:** Implemented (Experimental per model pending live hardware gate cycle test)

## Overview

GigaMate implements GIGABYTE GiMATE's **AI Power Gear III** on Linux as a true dGPU power-rail gate rather than passive PCI runtime PM (`D3cold`). Three user-selectable modes provide parity with Windows GiMATE:

- **Optimus (`optimus`)**: Default; no power-rail gating, leaving kernel runtime PM / RTD3 in control.
- **ECO (`eco`)**: Cuts the dGPU power rail and de-enumerates its PCI functions until the user switches back to Optimus or Auto (on AC).
- **Auto (`auto`)**: Gates the dGPU off when running on battery and restores power when AC is plugged in.

## Architecture

### 1. Kernel Module (`src/gigamate_acpi/gigamate_acpi.c`)

Extends `/sys/devices/platform/gigamate_acpi/` with two attributes (without creating a hard dependency on external `acpi_call`):

- `dgpu_acpi_path` (`0444`, read-only): Locates the NVIDIA dGPU PCI device, resolves its ACPI companion node, and returns the full ACPI path (e.g. `\_SB_.PCI0.GPP9.PEGP`). Cached via `gigamate_lookup_dgpu_acpi_path_locked()`.
- `acpi_eval` (`0644`, `CAP_SYS_ADMIN` enforced in `acpi_eval_store`): Validates a `\`-prefixed ACPI method path and evaluates it via `acpi_evaluate_object`, caching the return status in `last_eval_result` for subsequent sysfs readback.

`src/gigamate/acpi.py` exposes `read_dgpu_acpi_path()` and `acpi_eval_backend()`, preferring `gigamate_acpi` sysfs and falling back to `/proc/acpi/call` if the module is unavailable.

### 2. Privileged Helper & Polkit Boundary (`data/gigamate-dgpu-gate`)

Installed to `/usr/lib/gigamate/gigamate-dgpu-gate` (`0755`) and invoked via `pkexec` with polkit policy/rules (`org.gigamate.dgpu-gate.policy`, `50-gigamate-dgpu-gate.rules`) auto-granting `wheel`/`sudo` members:

- Subcommands: `version`, `status`, `probe`, `gate-off`, `gate-on`.
- Locking & state: Serialized via `/run/gigamate-dgpu-gate.lock` (`flock`) and atomically published to `/run/gigamate-dgpu-gate.json` (`0644`) so unprivileged monitor/UI readers never invoke NVML or `pkexec` just to query status.
- Guards:
  - Enforces dGPU `runtime_status == suspended` before gating off.
  - Enforces zero connected external/internal outputs attached to the dGPU card under `/sys/class/drm/card*/` (following `device/device` PCI links).
- Sequences:
  - **Gate Off** (`remove_before_off` quirk for AERO X16 `PG00`): Stops `nvidia-powerd.service` if active, writes `1` to `/sys/bus/pci/devices/<bdf>/remove` for the HDA audio sibling (`.1`) and GPU (`.0`), evaluates `PG00._OFF`, and records gated state.
  - **Gate On**: Evaluates `PG00._ON`, writes `1` to `/sys/bus/pci/rescan`, waits for PCI re-enumeration and `nvidia` driver bind, and restores `nvidia-powerd.service`. If PCI re-enumeration fails in firmware, surfaces a clear error advising a reboot.

### 3. Profile Schema v4 & Discovery (`src/gigamate/profiles.py`)

Adds `DgpuGateConfig` under `acpi.dgpu_gate` (`path`, `method`, `on_method`, `sta_method`, `quirks`, `confidence`). Built-in AERO X16 profiles (`0414_8105.json`, `0414_8104.json`, `dmi_GIGABYTE_AERO_X16.json`) ship at schema `"version": 4` pointing to `\_SB.PCI0.GPP9.PG00` (`method: "_OFF"`, `quirks: ["remove_before_off"]`, `confidence: "experimental"`).

`gigamate calibrate acpi` and `gigamate gpu power probe` discover candidate ACPI bridge/PowerResource paths (`dgpu_acpi_path` + `_STA` inspection, and optional `--live-test` when explicitly requested), while `gigamate profile report` prints the resolved `dgpu_gate` configuration for community reporting.

### 4. Policy Engine, Events, Sleep/Resume & UI (`src/gigamate/dgpu_gate.py`)

- **Core engine (`dgpu_gate.py`)**: Implements `find_dgpu_topology`, `connected_dgpu_displays`, `check_gate_off_guards`, `is_gated_off`, `is_supported`, `probe_dgpu_gate`, `gate_off`, `gate_on`, `set_mode`, `sync_policy`, `watcher_tick`, `schedule_resume_reconcile`, and `get_status`.
- **Policy coordination**:
  - `gpu.py` adds `GpuState.gated_off` (`"Off (gated)"`), detects missing PCI devices when gated off, and skips runtime-PM/power sync (`NvidiaGpuMonitor.sync_power()`) while gated off.
  - `dgpu_tune.py` (`DgpuWatcher.tick()` and `run_watcher()`) reports `runtime="gated_off"` (`"off (gated)"`), skips NVML calls while gated off so nothing wakes/touches the card, and runs `watcher_tick()` to handle deferred Auto/ECO transitions once the GPU finishes active workloads and suspends.
  - `99-gigamate-dgpu-ac.rules` fires on `power_supply` Mains online changes, touching `/run/gigamate/ac-event` for rapid Auto-mode synchronization (`hardware.py` + `watcher_tick()`).
  - `sleep_handler.py` invokes `schedule_resume_reconcile()` on resume because firmware can re-power the dGPU across suspend/resume.
- **UI & CLI**:
  - CLI: `gigamate gpu power status | off | on | eco | auto | optimus | probe [--live-test] | sync`.
  - Tray (`tray.py`): `dGPU Power` flyout (`Optimus` / `ECO` / `Auto`) and `"Off (gated)"` status string.
  - GigaMate Center (`ui/pages/gpu_page.py`, `ui/pages/dashboard_page.py`): Mode selector card, `"Experimental"` badge, gated-off status indicator (`"Off (gated)"`), and lockout of NVML tuning controls while gated off.
