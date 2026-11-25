# Contributing to GigaMate

Thank you for helping improve GigaMate!

GigaMate 5.0.0 uses **zero-profile automatic hardware discovery** — there are no static laptop model JSON files (`profile_data/*.json`), no `gigamate calibrate` wizard, and no `"experimental"` profile locks. Contributions focus on automatic hardware discovery, the `gigamate_acpi` kernel driver, the Material 3 UI/Automation engine, and tests.

---

## Reporting Bugs & Hardware Issues

When opening an issue for hardware detection, power profiles, RGB, dGPU power-gating, or sensors, please include:

1. **System & Kernel Info**: Laptop model, BIOS version, Linux distribution, and `uname -r`.
2. **`gigamate doctor` Output**: Run `gigamate doctor` and paste the full output (this includes `diagnose_lamparray`, `gigamate_acpi` status, and sysfs checks).
3. **`wmbd_aml` Diagnostics**: If your issue involves power profiles or Watt limits, include the `wmbd_aml` / `NPCF` diagnostic output from `gigamate doctor` (or `/sys/devices/platform/gigamate_acpi/`).

---

## Development Setup & Running Tests

1. Clone the repository and install in editable mode with dev dependencies:
   ```sh
   python -m venv .venv
   source .venv/bin/activate
   pip install -e ".[dev]"
   ```
2. Run the test suite:
   ```sh
   pytest
   ```
3. Keep changes focused and ensure `pytest` passes before opening a pull request.

---

## Areas for Contribution

- **`gigamate_acpi` Kernel Driver**: Improving DSDT `WMBD(0xED)` parsing, `NPCF` handling, fan/thermal sysfs attributes, and kernel compatibility.
- **Automatic Hardware Discovery**: Enhancing HID LampArray (`Usage Page 0x59`) probing, dGPU ACPI `_OFF`/`_PS3` power-gate probing, and battery charge-threshold sysfs support (see [docs/HARDWARE_DISCOVERY.md](docs/HARDWARE_DISCOVERY.md)).
- **UI & Automation**: Improvements to the Warm Obsidian Material 3 Center, Automation rules (AC/Battery 3-mode selector, TLP/PowerDevil integration, Shortcuts hardware capture), and system tray.
- **Documentation & Tests**: Keeping docs accurate and expanding `pytest` coverage for discovery and automation logic.
