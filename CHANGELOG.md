# Changelog

## v1.0.0 (unreleased)

> GigaMate — major evolution from `gigabyte-keyboard-rgb` to a full
> Gigabyte laptop management utility.

### Added

- **Project renamed to GigaMate** (`gigamate` CLI command)
  - Legacy `gigabyte-rgb` and `gigabyte-rgb-tray` still work as aliases
  - Config auto-migrated from `~/.config/gigabyte-keyboard-rgb/` to `~/.config/gigamate/`
- **ACPI fan monitoring** — read fan RPM, duty cycles, and temperatures
  - Backend: `gigamate_acpi.ko` kernel module (preferred)
  - Backend: `acpi_call` via `/proc/acpi/call` (fallback)
- **Power profile switching** — switch between Quiet/Balanced/Performance/Gaming
  - New CLI: `gigamate profile`, `gigamate status`
  - New tray: Power Profile radio group, live status display
- **Kernel module** `gigamate_acpi.ko` — persistent platform driver with sysfs interface
  - `/sys/devices/platform/gigamate_acpi/{temp1_input,fan1_input,...,profile}`
- **v2 device profiles** — optional `acpi` section defines laptop ACPI capabilities
  - Backward-compatible: old profiles without `acpi` work unchanged
- **Community contribution flow** — PR-based model support:
  - `gigamate calibrate rgb` — interactive keyboard colour mapping
  - `gigamate detect --acpi` — automatic ACPI capability probing
  - `gigamate calibrate --all` — combined calibration
  - `gigamate profile contribute` — guides through creating a Pull Request
- **Graceful degradation** — RGB works without ACPI, ACPI works without RGB,
  partial ACPI (e.g., temps only) shows what's available

### Changed

- All Python imports: `gigabyte_keyboard_rgb.*` → `gigamate.*`
- Config directory: `~/.config/gigabyte-keyboard-rgb/` → `~/.config/gigamate/`
- Systemd service: `gigabyte-keyboard-rgb.service` → `gigamate.service`
- udev rule: `99-gigabyte-keyboard-rgb.rules` → `99-gigamate.rules`
- Desktop entry: `Gigabyte Keyboard RGB` → `GigaMate`
- Icon: `gigabyte-keyboard-rgb.svg` → `gigamate.svg`

### Fixed

- N/A

### Removed

- N/A (all old commands remain as backward-compat aliases)

---

## v0.2.0 (previous)

> Last release under the `gigabyte-keyboard-rgb` name.

- Multi-model profile system (JSON profiles, calibration)
- 11-colour empirical keyboard model
- Tray app with colour/brightness controls
- Systemd user service, udev rules
- See git history for details.
