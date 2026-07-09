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
| `0xED` | `3` = Gaming | Maximum GPU power (~70W), highest fan curve |

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
