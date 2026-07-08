# Gigabyte Aero X16 (EG61VH) — Hardware Research

## Overview

This document catalogs everything we've learned about the internal hardware interfaces
of the Gigabyte Aero X16 (EG61VH) laptop: the USB HID keyboard RGB backlight and the
ACPI WMI fan/power control interface.

The laptop has **two completely independent hardware control paths**:

| Feature | Interface | Direct EC Write? | ACPI/WMI? | Works? |
|---|---|---|---|---|
| Keyboard RGB backlight | USB HID (Interface 3) | ❌ N/A | ❌ Dead end (WMBD 0x63) | ✅ pyusb |
| Fan speed monitoring | ACPI WMBC | ❌ EC ignores writes | ✅ WMBC 0xE4/0xE5 | ✅ |
| Temperature monitoring | ACPI WMBC | — | ✅ WMBC 0xE1/0xE2 | ✅ |
| Duty cycle monitoring | ACPI WMBC | — | ✅ WMBC 0x50/0x46/0x47 | ✅ |
| Fan profile switching | ACPI WMBD | ❌ EC ignores writes | ✅ WMBD 0xED (0-3) | ✅ |
| Direct fan speed override | ACPI WMBD + EC | ❌ EC overrides | ❌ EC overrides | ❌ |

---

## 1. ACPI WMI Interface (`AMW0`)

The device `\_SB.PCI0.AMW0` (HID: `PNP0C14`, UID: `"DCK"`) exposes two main methods:

### ACPI Method Dispatch Tables

All command numbers are passed as `Arg1` (Command), `Arg2` (Value/Data).
`Arg0` is always 0 (reserved/don't care).

#### `WMBD` — Write Methods

| Cmd | EC Register | Purpose | Tested? |
|---|---|---|---|
| `0xFA` | (none) | No-op | ✅ |
| `0xCB` | WINK | Webcam / Wink LED | ✅ |
| `0xE6` | WXCM(0xD0) | EC register 0xD0 access | ✅ |
| `0x80` | PL3E | Panel / LCD enable? | ❌ |
| `0xF6` | KBLL | Keyboard backlight level (0-100) | ✅ Dead end |
| `0xC7` | MUTE | Audio mute toggle | ❌ |
| `0xCA` | PSON | Power supply on | ❌ |
| `0x7D` | TFAN | Fan target (EC overrides) | ✅ Ignored |
| `0x71` | GFAN=0, FANB | Balanced fan mode | ✅ Ignored |
| `0x70` | TFAN=0, GFAN=1, FAN1+FAN2 | Manual both fans | ✅ Ignored |
| `0x57` | GFAN=0, CRAF | CPU fixed speed | ✅ Ignored |
| `0xC4` | LCDO | LCD on/off | ❌ |
| `0x6A` | ADJF | Adjustment factor | ❌ |
| `0x6B` | FAN1 | Write fan speed 1 | ✅ Ignored |
| `0x68` | XFNW | Extended fan write | ❌ |
| `0x67` | TENF | Ten fan? | ❌ |
| `0x66` | FLVL | Fan level | ❌ |
| `0x64` | BCPS | Battery charge protection start | ❌ |
| `0x65` | BCPC | Battery charge protection end | ❌ |
| `0xED` | — | **Power profile (0-3)** | ✅ **Works!** |
| `0xE7` | — | DBAC/DBDC toggle (0/1) | ❌ |
| `0x51` | — | Opt-mode 2 (dGPU) control | ❌ |
| `0x50` | FDTY | Fan duty type | ✅ Ignored |
| `0x46` | FDTY, FAN1 | CPU fan duty cycle | ✅ Ignored |
| `0x47` | GDTY, FAN2 | GPU fan duty cycle | ✅ Ignored |
| `0x63` | WXCM(0xD2-0xD6) | Keyboard RGB (buffer) | ✅ Dead end |
| `0x61` | BHEA | Battery health | ❌ |
| `0xA3` | WXCM(0xD1) | Unknown | ❌ |
| `0xD9` | KBAT | Keyboard battery LED | ❌ |
| `0xA1` | — | Returns 1 (version?) | ❌ |
| `0xC9` | FNKS | Fn key status | ❌ |
| `0x87` | PLED | Power LED (0=off, 1=on) | ❌ |
| `0x88` | BLED | Battery LED | ❌ |

#### `WMBC` — Read Methods

| Cmd | EC Register | Returns | Tested |
|---|---|---|---|
| `0x03` | — | Notification trigger | ✅ |
| `0xE6` | RXCM(0xD0) & 0x7F | EC register 0xD0 | ✅ |
| `0xA1` | M029(0x04) | Unknown | ❌ |
| `0xF6` | KBLL | Keyboard backlight level | ✅ |
| `0xE7` | DBAC | Dock battery AC | ❌ |
| `0xCA` | PSON | Power supply on | ❌ |
| `0xC7` | MUTE | Audio mute state | ❌ |
| `0xEF` | ~LIDF | Lid state (inverted) | ❌ |
| `0x7D` | TFAN | Fan target | ✅ |
| `0xE1` | **CTMP** | **CPU temperature** (°C) | ✅ |
| `0xE2` | **SKTC** | **Socket temperature** (°C) | ✅ |
| `0xE3` | SKTC | Same as E2 | ✅ |
| `0xE4` | **RPM1** | **Fan 1 speed** (RPM) | ✅ |
| `0xE5` | **RPM2** | **Fan 2 speed** (RPM) | ✅ |
| `0x71` | FANB | Balanced fan value | ✅ |
| `0x70` | FAN1 | Fan 1 speed EC register | ✅ |
| `0x6F` | FAN1 | Same as 0x70 | ✅ |
| `0x57` | CRAF | CPU fixed speed | ✅ |
| `0x80` | PL3E | Panel state | ❌ |
| `0xC4` | LCDO | LCD state | ❌ |
| `0x6A` | ADJF | Adjustment factor | ❌ |
| `0x6B` | FAN1 | Fan 1 speed | ✅ |
| `0x68` | XFN1 | Extended fan info (sleep 100ms) | ✅ |
| `0x67` | TENF | Ten fan | ❌ |
| `0x64` | BCPS | Battery protection start | ❌ |
| `0x65` | BCPC | Battery protection end | ❌ |
| `0x63` | RXCM(0xD2-0xD6) | Keyboard RGB readback | ✅ |
| `0x50` | **FDTY** | **Fan duty type** (%) | ✅ |
| `0x46` | **FDTY** | **CPU fan duty** (%) | ✅ |
| `0x47` | **GDTY** | **GPU fan duty** (%) | ✅ |
| `0x61` | BHEA | Battery health | ❌ |
| `0xA3` | RXCM(0xD1) | Unknown | ❌ |
| `0xA2` | ACST & 0x04 | AC status | ❌ |
| `0xD9` | KBAT | Keyboard battery LED | ❌ |
| `0xEB` | — | Returns 2 (version?) | ❌ |
| `0xC9` | FNKS | Fn key status | ❌ |
| `0x87` | PLED | Power LED (0=on, 1=off) | ❌ |
| `0x88` | BLED | Battery LED | ❌ |

---

## 2. Power Profile Switching (WMBD `0xED`)

The most useful discovery. Four profiles controlled via `WMBD(0xED, profile_id)`:

| ID | Name | ATPP | CPU Limit (AC) | GPU Limit (AC) | Total Limit (AC) |
|---|---|---|---|---|---|
| `0` | **Quiet** | 0xA0 | 20W | 65W | 65W |
| `1` | **Balanced** | 0xC8 | 25W | 65W | 80W |
| `2` | **Performance** | 0xC8–0xF0 | 19W | 80W | 80W |
| `3` | **Gaming** | 0xC8 | 25W | 80W | 80W |

These are **confirmed working**. Verified by monitoring GPU power draw:
- Gaming mode: GPU draws ~70W
- Quiet mode: GPU capped at ~45W

The profile switching calls `ECPT` which writes to EC registers `0x30`, `0x32`, `0x34`
(power limits in watts × 10). It also sets `ATPP`, `ACBT`, `AMAT` in the NPCF device.

### How to call from a kernel module

```c
acpi_get_handle(NULL, "\\_SB.PCI0.AMW0", &handle);
// Set Gaming:
acpi_evaluate_object(handle, "WMBD", wmbd_args(0xED, 3), NULL);
// Set Quiet:
acpi_evaluate_object(handle, "WMBD", wmbd_args(0xED, 0), NULL);
```

### How to call from userspace (with `acpi_call`)

```sh
# Set Gaming mode
echo 'WMBD 0xED 3' > /proc/acpi/call

# Set Quiet mode
echo 'WMBD 0xED 0' > /proc/acpi/call
```

---

## 3. EC Register Layout (`ec_sys`)

The standard EC IO space (ports `0x62`/`0x66`, 256 bytes via `/sys/kernel/debug/ec/ec0/io`)
is **nearly empty** for fan control. Relevant registers identified:

| Offset | Name | Value (idle) | Value (gaming) | Notes |
|---|---|---|---|---|
| `0x06` | Fan mode | `0x00` | `0x00` | Aero 15x uses 0x0B/0x1B, ours stays 0 |
| `0xB0` | Fan1 readback | `0x3D` (61) | `0x4B–0x4E` (75-78) | Read-only, EC overrides writes |
| `0xB1` | Fan2 readback | `0x3D` (61) | `0x4B–0x4E` (75-78) | Read-only, EC overrides writes |

All other 253 registers are static (do not change under load or gaming).

**Writing to 0xB0/0xB1:** The register accepts writes (value changes), but the actual
fan speed does not respond. The EC firmware overrides our manual control within
milliseconds.

---

## 4. EC SystemMemory Operation Region

The fan/temperature data lives in a **SystemMemory operation region** at
`0xFC7E0800` (`ECMM`) — not the standard IO-space 256-byte range.
This is why `ec_sys` cannot access RPM, CTMP, etc. directly.
Only ACPI methods (`WMBC`/`WMBD`) can read/write these registers.

---

## 5. Keyboard RGB Backlight (USB HID)

Already documented in `README.md` and `protocol.py`. Summary for completeness:

- **Interface**: USB HID, Interface 3, AltSetting 0
- **Protocol**: 8-byte control transfer (`bmRequest=0x21, bRequest=0x09, wValue=0x0300`)
- **Video**: `0414:8105` (Gigabyte Aero X16 EG61VH)
- **Command format**: `[0x08, 0x00, program, speed, brightness, colour, 0x01, checksum]`
- **Colour model**: Non-linear — uses `(byte5, byte4)` pairs per colour × brightness level
- **ACI/WMI (0x63)**: Writes to registers 0xD2–0xD6 — confirmed dead end for RGB

---

## 6. What Works vs What Doesn't

### ✅ Fully Working

| Feature | Method | Status |
|---|---|---|
| RGB colour set | pyusb ctrl_transfer | Stable, 11 colours |
| RGB off | pyusb with brightness=0 | Stable |
| CPU temperature read | `WMBC 0xE1` | **New** |
| Socket temperature read | `WMBC 0xE2/E3` | **New** |
| Fan 1 RPM read | `WMBC 0xE4` | **New** |
| Fan 2 RPM read | `WMBC 0xE5` | **New** |
| Fan duty cycle read | `WMBC 0x50/0x46/0x47` | **New** |
| Power profile switch | `WMBD 0xED` (0-3) | **New** |

### ❌ Confirmed Not Working

| Attempt | Method | Why |
|---|---|---|
| RGB via WMI | `WMBD 0x63` (buffer) | Correct but doesn't reach USB controller |
| RGB via EC | `ECPL()`, KBLL (0xF6) | Only brightness level, not colour |
| Direct fan speed | `WMBD 0x70/0x6B/0x46` | EC overrides writes |
| Direct fan mode | `WMBD 0x7D/0x71/0x57` | TFAN/FANB/CRAF all overridden |
| EC register write | `ec_sys` write to 0xB0/0xB1 | EC overrides |

### ❓ Untested

- Battery charge limits (0x64/0x65)
- Panel/backlight control (0x80, 0xC4)
- Fn key state (0x87/0x88, 0xC9)
- dGPU power switching (0x51)
- Extended fan info (0x68)

---

## 7. Compatibility with Other Gigabyte Models

### Likely compatible (same AMW0 ACPI interface)

All modern Gigabyte Aero and AORUS laptops share the same `PNP0C14` AMW0 WMI device.
If the DSDT contains `\_SB.PCI0.AMW0` with `WMBD`/`WMBC` methods and a similar
dispatch table, the fan control commands should work unchanged.

Known models using this interface:
- **Aero 15X v8** — Has nbfc-linux config (registers 0xB0/0xB1 for fans)
- **Aero 15-SA / 17-XD** — Same AMW0 device
- **Aorus 15P / 15G / 17G** — Same AMW0 device
- **Aero X16 EG61VH** — Confirmed ✅

### What differs between models

| Component | Likely universal | Model-specific |
|---|---|---|
| USB RGB protocol | 8-byte HID Feature Report | VID/PID, interface number, colour map |
| ACPI WMI WMBD/WMBC | Method names | Command dispatch table (some commands may differ) |
| EC register layout | Fan readback at 0xB0/0xB1 | Power limit registers, mode registers |
| Power profile (0xED) | Method exists | Values (0-3) and ECPT parameters |
| Keyboard RGB command | 8-byte format | Colour byte mapping, brightness behaviour |

### Using this project on another model

1. **RGB**: Run `gigabyte-rgb detect` + `gigabyte-rgb --calibrate` to build a profile
2. **Fan/monitoring**: Verify the AMW0 device exists:
   ```sh
   grep -l "AMW0" /sys/bus/acpi/devices/*/hid 2>/dev/null
   ```
3. **Power profiles**: Try `gigabyte-rgb-profile 0` through `3` (once built) and
   watch GPU power with `sensors | grep PPT`

---

## 8. DSDT Source

The full ACPI DSDT was dumped and decompiled from BIOS version `FB07` (2025-11-07):

```
$ sudo cat /sys/firmware/acpi/tables/DSDT > dsdt.dat
$ iasl -d dsdt.dat
```

Key locations in the decompiled DSDT:

| Component | Lines | Description |
|---|---|---|
| `EC0` (PNP0C09) | ~8145-8890 | Embedded controller device |
| EC operation regions | 8178+ | `ECMM` (0xFC7E0800), `ECM2` (0xFC7E0500) |
| EC field defs | 8350+ | Register → field name mapping |
| `AMW0` (PNP0C14) | 9423+ | WMI device with WMBD/WMBC |
| `WMBD` method | 9550-9940 | Write dispatch (all commands) |
| `WMBC` method | 9936-10130 | Read dispatch (all commands) |
| `ECPT` method | 8800+ | Power target writes to EC registers |

---

## 9. Timeline

| Date | Milestone |
|---|---|
| 2025-07-05 | USB HID RGB protocol reverse-engineered |
| 2025-07-05 | WMI/ACPI keyboard control investigated — dead end |
| 2025-07-06 | 11-colour empirical model, COLOUR_MAP, tray app |
| 2025-07-06 | Multi-model profile system (JSON profiles, calibration) |
| 2025-07-08 | **EC fan register investigation** — 0xB0/0xB1 read-only |
| 2025-07-08 | **ACPI WMBD/WMBC reverse engineering** — full dispatch table |
| 2025-07-08 | **Power profile switching confirmed** — 0xED (Quiet/Gaming) |
| 2025-07-08 | **Fan RPM + temperature readback confirmed** — all WMBC reads |

---

## 10. Future Work

- [ ] Build a userspace Python tool (`gigabyte-rgb-profile` or integrated into
      `gigabyte-rgb`) that calls WMBD/WMBC via `/proc/acpi/call` or a small
      kernel module
- [ ] Tray app integration — show temperature, fan RPM, profile selector
- [ ] Investigate 0x51 (dGPU power switching) for hybrid graphics control
- [ ] Investigate 0xE7 (DBAC/DBDC battery boost toggle)
- [ ] Test 0x64/0x65 (battery charge limit — useful for preserving battery life)
- [ ] Verify 0x50/0x46/0x47 duty cycle writes on other models
- [ ] Add model-specific fan profile configuration (custom power limits per model)

---

## 11. CLI/API Summary for Tool Builders

```python
# Required kernel support:
# - Either a kernel module calling acpi_evaluate_object ("acpi_fan" module)
# - Or acpi_call module writing to /proc/acpi/call

def set_fan_profile(profile: int) -> bool:
    """Set power profile: 0=Quiet, 1=Balanced, 2=Performance, 3=Gaming"""
    # Call WMBD(0xED, profile) via ACPI

def read_cpu_temp() -> int:
    """Returns CPU temperature in Celsius"""
    # Call WMBC(0xE1) via ACPI

def read_gpu_temp() -> int: ...   # WMBC 0xE2 or sensors
def read_fan1_rpm() -> int: ...  # WMBC 0xE4
def read_fan2_rpm() -> int: ...  # WMBC 0xE5
def read_cpu_duty() -> int: ...  # WMBC 0x46 (percentage)
def read_gpu_duty() -> int: ...  # WMBC 0x47 (percentage)

# Recommended: write a new kernel module "gigabyte_acpi" that
# exposes these as a /sys or /sysfs interface, then use from Python
```

## 12. Key Files on Disk

| File | Purpose |
|---|---|
| `~/gigabyte-keyboard-rgb/` | Main project repo (RGB + future fan tool) |
| `~/wmi_rgb_test/acpi_fan.c` | Test kernel module for ACPI fan calls |
| `~/ec_dumps/` | EC register dumps (baseline + gaming + stress) |
| `/tmp/acpi_dump/dsdt.dsl` | Decompiled DSDT (full BIOS tables) |
