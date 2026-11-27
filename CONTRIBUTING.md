# Contributing to GigaMate

Thank you for helping improve GigaMate!

GigaMate 5.0.0 uses **zero-profile automatic hardware discovery** — there are no static laptop model JSON files (`profile_data/*.json`), no `gigamate calibrate` wizard, and no `"experimental"` profile locks. Contributions focus on automatic hardware discovery, the `gigamate_acpi` kernel driver, safe dGPU power gating, the Aero Instrument UI & PyQt6 tray/daemon, and tests.

---

## Reporting Bugs & Hardware Issues

When opening an issue for hardware detection, power profiles, RGB, dGPU power-gating, or sensors at <https://github.com/goodeesh/GigaMate/issues>, please include:

1. **System & Kernel Info:** Laptop model, BIOS version, Linux distribution, desktop/compositor (KDE/GNOME/Wayland/X11), and `uname -r`.
2. **`gigamate doctor` Output:** Run `gigamate doctor` and paste the full output (this includes `diagnose_lamparray`, `gigamate_acpi` status, `wmbd_aml`, `NPCF`, udev ACLs, and sysfs checks).
3. **Hardware Discovery Report:** Include `gigamate profile report` (and `gigamate gpu power probe` if reporting discrete GPU power-gating behavior).
4. **Daemon Logs (if applicable):**
   ```sh
   journalctl --user -u gigamate.service -u gigamate-dgpu.service -n 100 --no-pager
   ```

---

## Development Setup & Running Tests

1. Clone the repository and create a virtual environment (giving access to system PyQt6/evdev if installed natively, or installing from PyPI):
   ```sh
   git clone https://github.com/goodeesh/GigaMate.git
   cd GigaMate
   python -m venv --system-site-packages .venv
   source .venv/bin/activate
   pip install -e .
   pip install pytest
   ```
2. Run the full headless test suite (including kernel module compilation check if headers for `$(uname -r)` are installed):
   ```sh
   QT_QPA_PLATFORM=offscreen pytest -q
   ```
3. Validate installer scripts:
   ```sh
   bash -n install.sh uninstall.sh
   ```
4. Keep changes focused and ensure `pytest` passes with zero failures before opening a pull request.

---

## Areas for Contribution

- **`gigamate_acpi` Kernel Driver:** Improving DSDT `WMBD(0xED)` parsing, `NPCF` handling, fan/thermal/battery sysfs attributes, and cross-kernel compatibility.
- **Automatic Hardware Discovery & Safety:** Enhancing HID LampArray (`Usage Page 0x59`) probing, safe dGPU ACPI `_OFF`/`_PS3` & PCIe link-width recovery, and battery charge-threshold sysfs support (see [docs/HARDWARE_DISCOVERY.md](docs/HARDWARE_DISCOVERY.md)).
- **UI, Daemon & Automation:** Improvements to the **Aero Instrument Center** (Dark & Light themes), Automation rules (AC/Battery 3-mode selector, TLP/PowerDevil hand-off), **Settings → Input & Desktop Shortcuts** hardware capture, and the pure-Python daemon + PyQt6 tray.
- **Documentation & Tests:** Keeping docs accurate and expanding `pytest` coverage in the existing `tests/test_*.py` suites.
