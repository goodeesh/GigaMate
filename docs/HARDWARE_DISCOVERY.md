# GigaMate 5.0.0 — Zero-Profile Automatic Hardware Discovery

GigaMate 5.0.0 eliminates static laptop model JSON profiles (`profile_data/*.json`), manual calibration wizards, and `"experimental"` profile locks. Instead, every subsystem is discovered directly from live firmware, Linux sysfs, and HID descriptors at startup.

---

## 1. ACPI Power Profiles & Watt Limits (`WMBD(0xED)` AML)

GigaMate's kernel module (`gigamate_acpi`) exposes two key sysfs interfaces for power and thermal discovery:

- **`wmbd_aml`:** Exposes the ACPI DSDT `WMBD` method bytecode so user space can inspect the `0xED` sub-function branches safely in read-only mode.
- **`npcf_{atpp,acbt,amat,dbac}`:** Exposes live NVIDIA NVPCF (`\_SB.NPCF`) platform power limits when present on the system.

### How Discovery Works
1. **DSDT `0xED` Verification:** At startup, GigaMate inspects `wmbd_aml` to verify that the `WMBD(0xED)` method exists in the system DSDT. Older incompatible AORUS/Aero BIOSes that lack `WMBD(0xED)` are rejected automatically rather than guessing or applying unsafe writes.
2. **Dynamic Profile Enumeration:** GigaMate parses the `0xED` table to discover whether the firmware defines **2, 3, or 4** power profiles along with their firmware IDs and names:
   - 3-profile systems typically expose `Eco`, `Balanced`, `Boost`.
   - 4-profile systems typically expose `Quiet`, `Balanced`, `Performance`, `Gaming`.
3. **Per-Profile Watt Limits:** CPU (`PL1`/`PL2` sustained & burst, plus `ECPT` shared floor on AMD), GPU TGP, GPU Dynamic Boost, and Combined Watt limits are extracted directly from the `0xED` firmware table (and cross-checked with sleep-safe cached NVML / RAPL / `amdgpu` caps).

---

## 2. Keyboard RGB (HID LampArray `0x59`)

GigaMate 5.0.0 uses standard **USB/HID LampArray (`Usage Page 0x59`)** exclusively for keyboard backlighting.

- **Runtime Scanning:** Scans `/sys/class/hidraw/hidraw*` for Gigabyte/AORUS keyboards (`0x0414`, `0x1044`, `0x04D9:0x8105`) advertising HID Usage Page `0x05 0x59` with LampArray Reports `1`, `5`, and `6`.
- **Validation:** Requires `lamp_count >= 1` before enabling RGB controls.
- **Zero `pyusb` / Zero Driver Detach:** Communicates natively through `/dev/hidraw*` without `pyusb`, `libusb`, or detaching kernel HID drivers.
- **Brightness Floor:** Enforces a `5–100%` active brightness floor (with separate Backlight Off/On controls) so low-brightness hues remain accurate.
- **Diagnostics (`diagnose_lamparray`):** Reports structured statuses (`supported`, `permission_denied`, `unsupported_layout`, `unsupported_firmware`, `not_detected`, `error`) to pinpoint udev or firmware issues immediately in `gigamate doctor` and the UI.

> [!NOTE]
> Pre-2025 Gigabyte/AORUS keyboards that only speak the legacy vendor `0x08` protocol (without HID LampArray) are not supported in GigaMate 5.0.0. Owners of those models should use the final **GigaMate `v4.5.x`** release (`v4.5.3`).

---

## 3. dGPU Power Gate (`data/gigamate-dgpu-gate` & `dgpu_gate.py`)

GigaMate discovers and manages discrete GPU power state through ACPI and PCI sysfs topology:

- **Topology & Power-Resource Probing:** Inspects PCI device bindings (dGPU `.0`, HDA audio sibling `.1`, upstream PCIe root bridge), `firmware_node/power_resources_D3*`, and device `_PS3`/`_PS0` paths (`dgpu_acpi_path` sysfs cache ensures discovery still works while the GPU is gated off). Default quirk order is `remove_before_off` so the kernel unbinds PCI functions cleanly before ACPI cuts the rail.
- **Pre-Gate Safety Guards:**
  - Verifies zero `connected` displays on dGPU DRM connectors (`display_connected`).
  - Wakes a runtime-suspended NVIDIA GPU cleanly (up to 45 s) before stopping `nvidia-powerd` to avoid cold GSP/FSP resume hangs, verifies zero open `/dev/nvidia*` or `/dev/snd/controlC*` holders (briefly pausing PipeWire/WirePlumber around PCI removal to prevent `snd_card_free()` `D`-state deadlocks), and runs an active confirmation dialog when apps hold the GPU.
- **Cross-Laptop Wake & Recovery Isolation:**
  - **Pause autoprobe + strictly-scoped SBR (`data/gigamate-dgpu-gate`):** On `gate-on`, pauses `/sys/bus/pci/drivers_autoprobe` during PCI rescan and only allows Secondary Bus Reset (`reset_subordinate`) when `_bridge_exclusively_hosts_unbound_dgpu()` proves zero bound drivers and no non-dGPU sibling functions/slots share that root bridge; otherwise falls back safely to non-destructive PCIe Link Retrain (`LNKCTL` bit 5).
  - **Scoped `PG00._ON` timeout recovery (`gigamate_acpi.c`):** Raw EC `0x2D` (`EC0.DSMD`) recovery on `AE_AML_LOOP_TIMEOUT` is gated by `gigamate_is_aero_x16_ec_dsmd()` (requiring DMI `GIGABYTE` + `AERO X16` AND ACPI handle `\_SB.PCI0.SBRG.EC0.DSMD`), and `\_SB.ALIB(0x18, 1)` only runs when `\_SB.ALIB` exists on AMD platforms. Unknown laptops never receive raw writes to EC `0x2D`.
- **Policy Modes:** **Optimus** *(safe default)*, **ECO** *(battery only)*, and **Auto** *(wake & restore on AC, prompt on battery unplug)*, plus automatic step-down from Performance/Gaming when gated off.

---

## 4. Pure-Python Daemon, PyQt6 Tray & Wayland Idle Intelligence

- **Architecture:** `src/gigamate/daemon.py` runs all polling, AC/Battery profile automation, profile-linked RGB, idle backlight sleep, and BMS periodic calibration in pure Python, while `src/gigamate/tray.py` provides a stateless PyQt6 `QSystemTrayIcon` shell — eliminating GTK3 and `AppIndicator3`.
- **Idle Monitoring (`src/gigamate/idle.py`):** Supports desktop session idle (KDE/GNOME/Mutter/D-Bus) as well as **Built-in keyboard & touchpad only** hardware filtering using `evdev` plus non-blocking `/dev/hidraw*` `input0` monitoring for compatibility with `EVIOCGRAB` key-remappers (**Toshy** / `xwaykeyz`) on Wayland.

---

## 5. Hotkeys

- **Built-in Gigabyte HID Defaults:** Automatically listens for standard Gigabyte HID hotkey events (`interface 2`, `report_id 4`, codes `000084` / `000091`).
- **User-Recordable Hardware Capture:** In **Settings → Input & Desktop Shortcuts**, users can capture any hardware key or Fn-combo directly from the HID stream (`gigamate hotkeys watch`) and bind it to GigaMate actions or register shortcuts with **KDE / GNOME**.

---

## 6. Battery Care & Sensors

- **Battery Care (`src/gigamate/battery.py`):** Reads and writes both the `gigamate_acpi` threshold attribute (`/sys/devices/platform/gigamate_acpi/charge_limit`) and standard Linux `power_supply` sysfs (`charge_control_end_threshold` / `charge_control_start_threshold`) when exposed by the kernel, plus supervised one-time `100%` top-ups and periodic BMS calibration scheduling.
- **Sensors (`src/gigamate/acpi.py`):** Reads CPU/socket temperatures, fan RPMs, and fan duty cycles directly from `gigamate_acpi` sysfs attributes.
