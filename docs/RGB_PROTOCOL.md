# RGB Keyboard Protocol

## HID LampArray (`Usage Page 0x59`) — GigaMate 5.0+

In GigaMate 5.0.0 and later, **HID LampArray (`Usage Page 0x59`)** is the sole keyboard RGB protocol. GigaMate scans `/sys/class/hidraw/hidraw*` at runtime with **zero `pyusb`**, **zero kernel driver detaching**, and **zero manual calibration**.

> [!NOTE]
> Owners of pre-2025 Gigabyte/AORUS keyboards that do not expose HID LampArray (`Usage Page 0x59`) should use the final **GigaMate `v4.5.x`** release.

### Runtime Auto-Discovery

At startup, GigaMate scans `/sys/class/hidraw/hidraw*` for any HID device advertising:
- **Usage Page `0x05 0x59`** (HID LampArray)
- **Reports `1`, `5`, and `6`**
- **`lamp_count >= 1`**

### 5% Minimum Brightness Floor

Below 5%, 8-bit integer channel scaling (`round(c * pct / 100)`) quantizes R:G:B ratios too coarsely and shifts colour hue. GigaMate therefore clamps active LampArray brightness to `5%–100%` and exposes a separate Backlight Off/On toggle.

### Diagnostics (`gigamate doctor`)

`gigamate doctor` reports `diagnose_lamparray` status and diagnostics:
- `ready` / `supported` — LampArray descriptor and reports detected and accessible
- `no_hidraw_devices` — No `/dev/hidraw*` LampArray device found
- `permission_denied` — `/dev/hidraw*` lacks read-write ACLs
- `unsupported_reports` — Device does not expose Reports `1, 5, 6`
- `zero_lamps` — LampArray descriptor reports `lamp_count == 0`

### Permissions

The LampArray protocol opens a `/dev/hidraw*` node read-write. udev creates that node on every boot, so a manual `chmod` is lost at the next boot and the number can change. `data/70-gigamate.rules` tags the device with `uaccess`, and the standard logind flow applies the ACL to the active local user. The file must keep the `70` prefix so it loads before `73-seat-late.rules`.

Find the keyboard node by its HID identity:

```sh
for h in /sys/class/hidraw/hidraw*; do
  echo "$h: $(grep HID_ID "$h/device/uevent")"
done
```

A working node looks like `crw-rw----+ root root` and has a `user:<you>:rw-` entry in `getfacl`. Remote sessions (SSH) and systems without `systemd-logind` do not receive the ACL.

### Report Layout

- **Report 1** (23 bytes, read): lamp count at bytes 1–2 (little endian).
- **Report 6** (2 bytes, write): `[6, 0]` disables autonomous mode.
- **Report 5** (10 bytes, write): `[5, 1, start_lo, start_hi, end_lo, end_hi, R, G, B, 255]`.

Brightness scales RGB because this keyboard's intensity channel is on/off.

---

## Historical Reference (GigaMate v4.x): Legacy Vendor `0x08` Protocol

> [!IMPORTANT]
> The legacy vendor `0x08` protocol below is **no longer supported in GigaMate 5.0.0+**. It is preserved here as historical documentation for pre-2025 hardware and **GigaMate `v4.5.x`**.

### Command Format

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

### Legacy `0x08` Colour Model (`0414:8105`)

The colour response is **non-linear** — visible hue depends on both the colour byte (byte 5) and the brightness byte (byte 4). A single colour byte can produce different hues at different brightness levels (e.g., byte `0x06` at bright levels gives purple, at mid levels gives pink).

Reverse-engineering on `0414:8105` verified:
- The real linear brightness ceiling of `byte4` is **`0..50` (`0x00..0x32`)** (standard ITE `0x08` scale).
- Values `> 50` (`55, 75, 90, 100`) cause 8-bit channel wraparound (`mod 256`) on mixed/white-balanced slots (`5, 6, 7`), which is *why* `byte4 > 50` shifts hues into `blush_pink`, `light_blue`, `light_purple`, and `dark_yellow`.
- The ITE `0x14` custom palette write command is ignored by `0414:8105` firmware.

### What Didn't Work

- **ACPI/WMI (WMBD 0x63)**: Writes to EC registers `0xD2–0xD6` — confirmed dead end. The ACPI path does not reach the USB RGB controller.
- **EC direct write**: Keyboard backlight level (`KBLL` at EC offset `0x31`) controls brightness only, not colour.
- **Animated effects**: Programs `0x02–0x0D` can hang the keyboard firmware, requiring a USB reset to recover. Only static (`0x01`) is safe.

---

## References & Credits

- **Teng Lin (PR #32)** — Introduced HID LampArray (`lamparray.py`) support and hidraw descriptor discovery (inspired by `gigabyte-kbd`).
- **Paul Ridgway** — Original legacy `0x08` Aero 15 reverse engineering: https://blockdev.io/gigabyte-aero-w15-keyboard-and-linux-ubuntu/
- Protocol implementation: `src/gigamate/lamparray.py`
