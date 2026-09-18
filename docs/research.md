# ACPI Hardware Research

## Overview

Gigabyte Aero/AORUS laptops expose an **AMW0** WMI device (`\_SB.PCI0.AMW0`,
HID: `PNP0C14`) with two methods for hardware control:

- **WMBC** — Read sensors (temperature, fan RPM, duty cycle)
- **WMBD** — Write commands (power profile switching)

Fan/temperature data lives in a **SystemMemory operation region** at `0xFC7E0800`
(`ECMM`), not the standard EC IO space. Only ACPI methods can access it —
`ec_sys` cannot.

## Working Commands

### WMBC (read)

| Command | Returns | Description |
|---------|---------|-------------|
| `0xE1` | int | CPU temperature (°C) |
| `0xE2` | int | Socket temperature (°C) |
| `0xE4` | int | Fan 1 speed (RPM) |
| `0xE5` | int | Fan 2 speed (RPM) |
| `0x50` | int | Total fan duty cycle (%) |
| `0x46` | int | CPU fan duty cycle (%) |
| `0x47` | int | GPU fan duty cycle (%) |

### WMBD (write)

| Command | Value | Effect |
|---------|-------|--------|
| `0xED` | `0` = Quiet | Lowest fan noise, capped GPU power (~45W) |
| `0xED` | `1` = Balanced | Default profile |
| `0xED` | `2` = Performance | Higher fan curve |
| `0xED` | `3` = Gaming | Maximum GPU power (~80W+ with Dynamic Boost / SmartShift), highest fan curve |

## Discrete GPU Dynamic Power Management

Discrete mobile GPUs (NVIDIA RTX and AMD Radeon) scale their TGP dynamically:

### NVIDIA Dynamic Boost
- **Base TGP vs Boost**: The RTX mobile GPU has a default baseline ceiling (e.g. 50W). Dynamic Boost allows shifting up to 80W–85W depending on CPU load.
- **Daemon Requirement**: Requires `nvidia-powerd.service` active and connected to D-Bus. Without it, the driver locks the GPU at its base power limit (50W).
- **Automation**: GigaMate automatically verifies and starts `nvidia-powerd.service` when switching to Performance or Gaming profiles. Polkit rule `data/50-gigamate-powerd.rules` allows unprivileged management.

### AMD SmartShift
- **In-Kernel Architecture**: AMD SmartShift is handled directly in kernel space by `amdgpu` and platform firmware (SMU/PMFW). No userspace daemon is required.
- **Sysfs Control**: The driver exposes `/sys/class/drm/card*/device/smartshift_bias` (-100 to +100). GigaMate adjusts this bias on profile switches (+100 for Gaming, -50 for Quiet) when writable.

## What Didn't Work

- **Direct EC register writes** (0xB0/0xB1): The EC firmware overrides manual
  fan speed control within milliseconds.
- **WMBD fan commands** (0x70, 0x6B, 0x46, 0x7D, 0x71, 0x57): All accepted
  by ACPI but overridden by EC firmware.
- **Keyboard RGB via WMI** (WMBD 0x63): Writes to EC registers 0xD2–0xD6
  but doesn't reach the USB RGB controller.

## Implementation

The kernel module (`src/gigamate_acpi/gigamate_acpi.c`) exposes these as sysfs
files at `/sys/devices/platform/gigamate_acpi/`. The Python layer
(`src/gigamate/acpi.py`) reads/writes them.

Full ACPI command dispatch tables (all tested commands including failures)
are documented in the original research notes at `docs/research.md` (legacy).

## EC Firmware Reverse Engineering (AERO X16 1VH, BIOS `FB0A` / EC `F00A`)

### Firmware location and extraction

The Gigabyte BIOS update package
(`nb-bios-gigabyte_aero_x16-VH-amd-win11-64bit-FB0A-EC-F00A/suit/file/EG61WH.FB0A`)
is a full 32 MiB flash image and contains the EC firmware:

- Raw **8051** image at file offset **`0x70000`**, length **`0x25600`**
  (padded to `0x28000` with `RET`/`0xFF`).
- Reset vector `02 0F 27` (`LJMP`) at offset 0; `sha256
  1707211638264ac4732f8fca2ef5e75779401a92c1792e75bca8fbe876e2d9ba`.
- Strings confirm an ITE EC: `UCSI_GET_CONNECTOR_STATUS`, `GET_PDO`,
  `SET_RETIMER_FW_UPDATE_MODE_ON`, `TPC_3A` (USB-C PD/UCSI). It uses the
  `0xE800` peripheral page plus `0xF6xx`/`0xF7xx` blocks heavily.
- Disassemble with `naken_util -8051 -bin -disasm ec_f00a.bin` (or
  `r2 -a 8051`); the 8051 is also understood by Ghidra.

### Host window mapping (two distinct regions)

- Standard ACPI EC RAM (ports `0x62/0x66`, `ec_sys`, ACPI `ERWT`/`ERRD`)
  maps to EC XDATA base **`0xF700`** (`EC offset n == EC 0xF700 + n`).
- The `PECM` SystemMemory window at `0xFC7E0800` maps to EC **`0xF000`**:
  the EC command mailbox sits at EC `0xF582`. Note `0xF700 + 0x582 = 0xFC82`,
  which the firmware never touches — so the two regions are genuinely
  different.
- Consequence: the DSDT `PECM` fan fields (`FAN1`@0x1B, `FDTY`@0x25,
  mode@0x2C, curve@0x3C–0x59) land on EC `0xF0xx`, which the firmware never
  reads. **The WMBD fan commands are dead** (they write an unused mirror).

  **Correction (later testing):** this inference was **wrong**. WMBD fan writes
  are **live** — the vendor "max fan" sequence (`66=0, 71=0, 6B=100, 47=100,
  67=1, 6A=1`) drove the fans to ~6600 RPM. So the DSDT `PECM` fan fields do
  reach the firmware's live fields (`FAN1`→`0xF71B`, `FDTY`→`0xF725`,
  mode→`0xF72C`), i.e. the window effectively maps to EC `0xF700`; the
  `0xF582` accesses seen in firmware are unrelated internal code.

### The real fan block (EC `0xF700`–`0xF7FF`)

Fields the firmware actually consumes:

| EC addr | Meaning |
|---------|---------|
| `0xF70C` | fan sub-mode (low nibble 1..4); GFAN bit lives here |
| `0xF713` | 0–100 **duty ramp counter** — the firmware sets it to `0x64` (100) and decrements it; it is driven by the EC, not the host |
| `0xF71B`/`0xF71C` | FAN1/FAN2 — read as a **2-bit level**, not a duty |
| `0xF725`/`0xF726` | FDTY/GDTY — command/status bitfields, not raw duty |
| `0xF72C` | mode bits (CRAF/FANB/TENF/ADJF); bit1 triggers preset levels |
| `0xF73C`–`0xF759` | curve temps/speeds — host-writable (ERWT sticks) but the firmware **ignores** it |
| `0xF74B` | FLVL — a ramp counter |

`WMBC 0xE4/0xE5` (fan RPM) return the DSDT `RPM1`/`RPM2` fields; note the
firmware also uses EC `0xF713` as the duty ramp, so the window byte at
offset `0x13` is a mix of EC-driven status rather than a clean host input.

Fan "levels" are applied through `lcall 0xe234`, which does `add A,#0x20`
and sets/clears bits in the **`0x20xx` hardware register file** — i.e. fixed
presets, not a duty value. The actual PWM/control bits live in EC-internal
peripheral space (e.g. `0xF63C`, `0xE8xx`) unreachable from the host.

### Conclusion

The EC implements its fan curves entirely in firmware: it reads internal
temperature sources and drives internal hardware control bits. The
host-visible block exposes only mode bits and status. **There is no
host-writable duty register.** Only the four profile curves
(Quiet/Balanced/Performance/Gaming, WMBD `0xED`) plus forced modes can be
selected. Arbitrary custom curves would require modifying and re-flashing
the EC firmware itself (high risk, not attempted).

This matches the live findings: an `ec_sys` brute-force of offsets `0x00`–`0x7F`
changed fan behaviour only at `0x2C` (the mode byte), and writing the curve
region produced no RPM change.

## Live Direct-EC-RAM Experiments (AERO X16 1VH, BIOS FB0A / EC F00A)

Strategy validated against the community (Gigabyte Aero/AORUS:
`rcassani/p37-ec-aorus15g`, `tangalbert919/p37-ec-aero-15`,
`CommitThis/aero15x-fand`; ITE ECs: `KyaniteLabs/evo-x2-ec`,
`h4fnp/tpnsfand`, `passiveEndeavour/it5570-fan`): direct writes to the EC RAM
window via `ec_sys` (`write_support=1`) are the standard way to drive these
fans. The known traps were accounted for: (`ec_sys` silently discards writes
unless `write_support=Y`), the EC reverts one-shot writes, so every candidate
was **re-asserted every 500 ms for ~10 s** with a watchdog and full restore.

Method: `ec_sys` window offsets `0x00`–`0x60`, kernel `7.2.5`, lockdown
`[none]`, temps 42–57 °C.

**Baseline map (live, little-endian bytes):**

| Offset | Meaning |
|--------|---------|
| `0x13`/`0x14` | CPU fan RPM (LE) — e.g. `0x14 0x08` = 2068 |
| `0x15`/`0x16` | GPU fan RPM (LE) |
| `0x25`/`0x26` | fan duty % (matches `pwm1`/`pwm2`) |
| `0x2C`, `0x0C` | mode-ish bytes (EC-driven; value changes on its own) |
| `0xB0`/`0xB1` | **temperature** mirror (44→54 with CPU load) — *not* the Aero‑15 fan-speed registers |

**Results — none of these changed fan RPM or duty:**

- `0x25`/`0x26` (`FDTY`/`GDTY`) = 80 %, re-asserted.
- `0x1B`/`0x1C` (`FAN1`/`FAN2`) = 80 %, re-asserted.
- Mode byte `0x2C` = every combination of `CRAF(0)/FANB(1)/TENF(2)/ADJF(3)`.
- Mode byte `0x0C` = `0x00`–`0x04`.
- Mode+duty combos (`0x2C` bit + `FAN1`/`FDTY` = 80 %).
- Legacy Gigabyte Aero bits `0x08.6` (quiet), `0x0C.4` (gaming), `0x0D.0`
  (auto-max), `0x0D.7` (deep), `0x06.4` (fix) — all inert here (the firmware
  never accesses `0x06`/`0x08`/`0x0D`).

The EC kept the fans pinned at **~22 % / ~2068 RPM even at 57 °C** during a
16-thread load — its auto curve is deliberately flat/quiet at these
temperatures. Profile switching (WMBD `0xED`) produced only a small change
(one fan 2068→2343 RPM).

**Conclusion:** consistent with the firmware analysis, this EC exposes **no
host-writable fan duty**. The window bytes are EC-driven status/telemetry; the
host can read them but cannot steer the fans. Only EC profile selection
(Quiet/Balanced/Performance/Gaming) is available as a lever.

## Temperature-Spoofing Investigation ("fake temperatures")

Idea: feed the EC a fake temperature so its hidden internal fan curve reacts,
while the OS still shows real temperatures — the **NBFC "Fake temperatures"**
technique (precedents: NBFC on HP ProBook/EliteBook; Lenovo S10-3T rewrites ACPI
field `RTMP` every 50 ms; Fujitsu Amilo writes `XHPP`; Acer 5720 on LKML). It
works only when the EC's curve reads a **host-writable** temperature field.

Findings on the AERO X16:

- A read-only 256-byte ERAM sweep (idle → load → cooldown, `k10temp` 42→95 °C)
  found only three temperature-tracking bytes: **`0xB0`/`0xB1`** (r ≈ +0.86 vs
  CPU) and **`0xB4`** (r ≈ +0.97 vs GPU/socket).
- Race-writing a fake **HIGH** temperature (70/75/85 °C) to `0xB0`/`0xB1`/`0xB4`
  at ~270 writes/s for 10–12 s produced **zero fan response** (fans held
  ~1863 RPM / 20 %). The EC had always recomputed the real value by the time it
  mattered.
- Side observation: writing ERAM `0xB0` changes the **ACPI/OS-visible CPU temp**
  (`WMBC 0xE1`) and `0xB4` changes the visible socket temp (`WMBC 0xE2`) — they
  are ACPI *output* registers the host can overwrite, but the fan curve does not
  consume them.

**Conclusion:** the EC computes its fan-controlling temperature internally (CPU
PECI/DTS + GPU SMBus) and reads **no host-writable temperature field**; the only
writable temp registers are OS-visible outputs ignored by the fan logic.
Temperature spoofing is therefore **infeasible in software** on this EC. (The one
untested, high-risk avenue would be the WMI `ECMD 0x45` arbitrary-register write,
which previously corrupted USB-C/touchpad.)

## WMI Fan-Curve Interface Test (definitive)

Earlier curve tests were invalid: the probe module has no `debug_wmbc` attribute
(the scripts read back through a non-existent file). After adding a real
`debug_wmbc` (WMBC with an optional argument) and rebuilding for the running
kernel, the DSDT curve interface was tested end to end:

- `WMBC`/`WMBD` are live: `WMBC 0xE1` → CPU temp, `0xE4` → fan RPM; the WMBD
  max-fan sequence still drives the fans.
- **Curve read**: `WMBC 0x68 <index>` (DSDT: `XFNR = index`, return `XFN1`)
  returned **0 for every index 0–14**.
- **Curve write**: `WMBD 0x68` with payload `speed<<16 | temp<<8 | index` was
  accepted (ACPI echoed it) but the readback stayed 0.
- **Functional test**: wrote a full 15-point curve (speed 200 at every point),
  enabled custom mode (`0x67=1`, `0x57=1`, `0x66=4`) → fans stayed **0**
  (custom mode with an "empty" curve). Releasing all modes restored the EC auto
  curve (~1898 RPM).
- **Firmware cross-check**: the DSDT "curve" offsets (`0xF73C`–`0xF759`) are not
  a host curve table on this EC — the firmware uses them as internal
  counters/timers (`0xF74B`/`0xF74C`/`0xF74D` inc/dec; `0xF73C` compared to
  magic `0x55`), and `XFNW` (`0xF71D`) as a status flag.

**Conclusion:** the X16 EC firmware (`F00A`) does **not** implement the DSDT's
indexed fan-curve store (`SetFanIndexValue` / `XFNW`). Those methods are a
generic Gigabyte ACPI template that is inert here. This confirms, from the
WMI side, that there is **no host-programmable custom fan curve** on this unit.
The working controls remain the four EC profile curves (WMBD `0xED`) and the
ordered fixed/max sequence (`66=0, 71=0, 6B=100, 47=100, 67=1, 6A=1` → ~6600
RPM; release by clearing all fan methods).

Note: GiMATE's newer "Customize Mode → Fan Table" exists on some GiMATE
builds/versions, but its UI is capability-gated and version-dependent; on this EC
firmware the curve plumbing appears unsupported, so a Windows-side table would
need EC-firmware support this unit lacks (not verifiable without Windows).

## NVIDIA dGPU V/F-offset undervolt & max-clock cap (implemented)

Separate from the EC/fan work, the NVIDIA dGPU exposes two NVML controls:

- **V/F-curve offset** — `nvmlDeviceSetGpcClkVfOffset(device, int mhz)` shifts the
  GPC clock V/F curve so a given clock runs at lower voltage.
  `nvmlDeviceGetGpcClkVfOffset` reads it back. **Linux-only, requires root**; the
  newer `nvmlDeviceSetClockOffsets` API is buggy (per-pstate offsets leak, get
  returns 0), so the offset API is used.
- **Max-clock cap** — `nvmlDeviceSetGpuLockedClocks(device, 0, N)` upper-bounds
  the boost clock to `N` MHz (min 0 means "no lower bound"). There is **no NVML
  getter** for the locked value, so the applied cap is tracked by GigaMate.
  `nvmlDeviceResetGpuLockedClocks` unlocks; the ceiling comes from
  `nvmlDeviceGetMaxClockInfo(device, NVML_CLOCK_GRAPHICS)`.
- Verified on the AERO X16's RTX 5060 Laptop (`0000:64:00.0`, driver 615.71.09):
  apply/read-back/clear and locked clocks all work.
- **Sleep-safety**: opening NVML holds a PM reference and wakes the dGPU, so the
  tuning must be applied only while `power/runtime_status == active` (detected
  via sysfs, which does not wake it), cleared on suspend, and re-applied after
  `D3cold` wipes driver state. Locked clocks keep the core at a higher floor and
  raise idle power (see LACT #908), so GigaMate clears **both** controls when the
  GPU is awake but idle (`util == 0` for a grace period) and re-applies them when
  load returns.
- Implemented in GigaMate as a privileged one-shot helper
  (`data/gigamate-dgpu-nvml`, invoked via `pkexec` with an auto-grant polkit
  rule) plus an unprivileged watcher (`src/gigamate/dgpu_tune.py`). The watcher
  tracks the last applied offset/cap (not just a boolean) so changing either
  value re-applies immediately, and publishes its state to
  `$XDG_RUNTIME_DIR/gigamate-dgpu.json` for cross-process status. Config keys:
  `dgpu_undervolt_{enabled,offset_mhz,auto}` and
  `dgpu_max_clock_{enabled,mhz}`; CLI
  `gigamate gpu undervolt <0-255|off|status>`,
  `gigamate gpu maxclock <0-4000|off|status>` and
  `gigamate gpu auto <on|off|status>`; Center controls (undervolt slider
  + max-clock slider), plus a Settings toggle for `dgpu_undervolt_auto`.
- `dgpu_undervolt_auto` gates only **automatic** application on boot/wake/resume:
  when off, the watcher never (re)applies by itself (it still clears on suspend
  for safety), and an explicit Apply/CLI command applies immediately and persists
  until the next suspend.
