# GigaMate 5.0.0

**Zero-profile automatic hardware discovery and control center for modern Gigabyte & AORUS laptops on Linux.**

GigaMate 5.0.0 discovers your laptop's power profiles, per-profile Watt limits, 24-bit keyboard RGB, dGPU power gate, hotkeys, and battery charge limits directly from live firmware and kernel sysfs at startup — **no model JSON files, no manual calibration, no `experimental` profile locks, and no `pyusb`**.

---

## Key Features (5.0.0)

### Warm Obsidian Material 3 Center
- Modern, responsive desktop control center built with a Warm Obsidian Material 3 theme.
- Live hardware telemetry: CPU/GPU temperatures, fan RPM, fan duty cycle, AC/Battery state, and active power limits.

### 24-Bit HID LampArray RGB
- Native `/dev/hidraw*` **HID LampArray (`Usage Page 0x59`)** support — zero `pyusb`, zero kernel driver detaching.
- **5–100%** active brightness floor with dedicated **Off / On** controls.
- **Backlight Auto-Off** idle timer with **Keep active on AC** option.

### Automation Tab
- **Colour per Power Profile**: Automatically switch keyboard RGB colour when changing power profiles, with **auto-unlink on manual pick** if you choose a custom colour directly.
- **3-Mode AC / Battery Card Selector** (**Off / Follow System / GigaMate-Driven**):
  - **One-click TLP / KDE PowerDevil deactivation** with **reversible restore** so external power daemons don't fight GigaMate.
  - **Drift watchdog** to keep firmware power state aligned with your selected rule.
- **Shortcuts**: Record hardware keys directly from the HID stream and register shortcuts with **KDE / GNOME**.

### dGPU Power Gate
- **Optimus / ECO / Auto** power-gating modes via ACPI `_OFF` / `_PS3` power resources.
- **Keep dGPU On for Now** temporary override.
- **2 s live holder auto-cut** safety guard and a **one-time user risk disclaimer** before enabling aggressive power-gating.

### Battery Care
- Configurable battery charge start/stop thresholds via Linux `power_supply` sysfs.

### System Tray
- Quick menu: **Open Center**, **Status**, **Power Profile**, **Backlight** (`Off` / `5%` / `50%` / `100%`), and **Quit**.

---

## Hardware Compatibility & Automatic Discovery

GigaMate 5.0.0 automatically probes your system at startup:
- **Power Profiles & Watt Limits**: Verifies `WMBD(0xED)` in the ACPI DSDT via `gigamate_acpi` (`wmbd_aml` & `NPCF`), rejecting older incompatible BIOSes automatically and discovering 2, 3, or 4 firmware profiles with their CPU/GPU Watt limits.
- **Keyboard RGB**: Scans `/sys/class/hidraw/hidraw*` for HID LampArray (`0x05 0x59`, Reports `1, 5, 6`, `lamp_count >= 1`).
- **dGPU Power Gate, Hotkeys & Battery Care**: Discovered dynamically from ACPI/PCI sysfs and Gigabyte HID hotkey events (`interface 2`, `report_id 4`).

See [docs/HARDWARE_DISCOVERY.md](docs/HARDWARE_DISCOVERY.md) for the full architecture and [docs/RGB_PROTOCOL.md](docs/RGB_PROTOCOL.md) for LampArray protocol details.

> [!IMPORTANT]
> ### Older Pre-2025 Models (Download v4.5.x)
> GigaMate 5.0.0 dropped legacy vendor `0x08` RGB and static model JSON profiles in favor of pure automatic hardware discovery. If you own an older pre-LampArray Gigabyte or AORUS laptop, please download the final **`v4.5.x`** release from **GitHub Releases**.

---

## CLI Commands

```sh
gigamate status                  # Show discovered hardware, power profile, sensors, and RGB state
gigamate doctor                  # Run diagnostics (gigamate_acpi, wmbd_aml, diagnose_lamparray, sysfs)
gigamate profile [NAME]          # Get or set the active ACPI power profile
gigamate rgb [COLOUR] [PCT]      # Set keyboard RGB colour and brightness (5–100%)
gigamate backlight [on|off]      # Toggle keyboard backlight power
gigamate dgpu [optimus|eco|auto] # Manage dGPU power-gate mode
gigamate gui                     # Launch the GigaMate Material 3 Center & tray
```

---

## Installation & Permissions

1. Install GigaMate and the `gigamate_acpi` kernel module (DKMS) per your distribution package or from source:
   ```sh
   pip install -e .
   ```
2. Ensure `data/70-gigamate.rules` is installed in `/etc/udev/rules.d/` and reload udev so `/dev/hidraw*` receives `uaccess` ACLs for your local session:
   ```sh
   sudo udevadm control --reload-rules && sudo udevadm trigger
   ```
3. Run `gigamate doctor` to verify `gigamate_acpi`, `wmbd_aml`, and `diagnose_lamparray`.

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for bug-reporting guidelines (`gigamate doctor` + `wmbd_aml`), development setup, and running `pytest`.
