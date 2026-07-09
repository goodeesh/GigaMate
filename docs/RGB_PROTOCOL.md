# RGB Keyboard Protocol

## USB Interface

Gigabyte Aero/AORUS keyboards use USB HID with multiple interfaces.
The RGB control channel is on a vendor-specific interface (typically interface 3).

| Interface | Function | Notes |
|-----------|----------|-------|
| 0 | Keyboard input | Leave untouched |
| 1 | Vendor-specific (0xFF00) | Detach kernel driver |
| 2 | Mouse + Feature Report 0x5A | Leave untouched |
| **3** | **Vendor-specific (0xFF01)** | **RGB control channel** |
| 4 | Digitizer/touchpad | Leave untouched |

## Command Format

8-byte USB HID Feature Report sent via control transfer:

```
[0x08, 0x00, program, speed, brightness, colour, 0x01, checksum]
```

| Byte | Purpose | Values |
|------|---------|--------|
| 0 | Instruction | `0x08` |
| 1 | Padding | `0x00` |
| 2 | Program | `0x01` = static (safe), others (0x02-0x0D) = animated (may hang firmware) |
| 3 | Speed | `0x01` (fastest) to `0x0A` (slowest) |
| 4 | Brightness | `0x00` (off) to `0x64` (max) |
| 5 | Colour | `0x01`–`0x07` |
| 6 | Padding | `0x01` |
| 7 | Checksum | `(255 - sum(bytes 0-6)) & 0xFF` |

Checksum = `(255 - sum(first 7 bytes)) & 0xFF`

## Colour Model

The colour response is **non-linear** — visible hue depends on both the colour byte
(byte 5) and the brightness byte (byte 4). A single colour byte can produce
different hues at different brightness levels (e.g., byte `0x06` at bright levels
gives purple, at mid levels gives pink).

Each colour in a device profile maps to fixed `(byte5, byte4)` pairs per
brightness level (off/dim/full). These pairs are determined empirically via
the calibration tool.

## What Didn't Work

- **ACPI/WMI (WMBD 0x63)**: Writes to EC registers 0xD2–0xD6 — confirmed dead end.
  The ACPI path does not reach the USB RGB controller.
- **EC direct write**: Keyboard backlight level (`KBLL` at EC offset 0x31)
  controls brightness only, not colour.
- **Animated effects**: Programs 0x02–0x0D can hang the keyboard firmware,
  requiring a USB reset to recover. Only static (0x01) is safe.

## References

- Paul Ridgway's original reverse engineering:
  https://blockdev.io/gigabyte-aero-w15-keyboard-and-linux-ubuntu/
- Protocol implementation: `src/gigamate/protocol.py`
- Device profiles: `src/gigamate/profile_data/`
