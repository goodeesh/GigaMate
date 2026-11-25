# GigaMate 5.0.0 — Zero-Profile Automatic Hardware Discovery

GigaMate 5.0.0 eliminates static laptop model JSON profiles, manual calibration wizards, and experimental profile locks. Instead, every subsystem is discovered directly from live firmware, kernel sysfs, and HID descriptors at startup.

---

## 1. ACPI Power Profiles & Watt Limits (`WMBD(0xED)` AML)

GigaMate's kernel module (`gigamate_acpi`) exposes two key sysfs interfaces for power and thermal discovery:

- **`wmbd_aml`**: Exposes the ACPI DSDT `WMBD` method bytecode and `0xED` sub-function table.
- **`NPCF`**: Exposes NVIDIA Dynamic Boost / platform power configuration state.

### How Discovery Works
1. **DSDT `0xED` Verification**: At startup, GigaMate inspects `wmbd_aml` to verify that the `WMBD(0xED)` method exists in the system DSDT. Older incompatible AORUS/Aero BIOSes that lack `WMBD(0xED)` are rejected automatically rather than guessing or applying unsafe writes.
2. **Dynamic Profile Enumeration**: GigaMate parses the `0xED` table to discover whether the firmware defines **2, 3, or 4** power profiles, along with their exact firmware IDs and names:
   - 3-profile systems typically expose `Eco`, `Balanced`, `Boost`.
   - 4-profile systems typically expose `Quiet`, `Balanced`, `Performance`, `Gaming`.
3. **Per-Profile Watt Limits**: CPU (PL1/PL2) and GPU power limits are extracted directly from the `0xED` firmware table for each discovered profile, so the UI and CLI always reflect the laptop's actual hardware capabilities.

---

## 2. Keyboard RGB (HID LampArray `0x59`)

GigaMate 5.0.0 uses the standard **USB/HID LampArray (`Usage Page 0x59`)** protocol exclusively for keyboard backlighting.

- **Runtime Scanning**: Scans `/sys/class/hidraw/hidraw*` for devices advertising HID Usage Page `0x05 0x59` with LampArray Reports `1`, `5`, and `6`.
- **Validation**: Requires `lamp_count >= 1` before enabling RGB controls.
- **Zero `pyusb` / Zero Driver Detach**: Communicates natively through `/dev/hidraw*` without `pyusb`, `libusb`, or detaching kernel HID drivers.
- **Brightness Floor**: Enforces a `5–100%` active brightness floor (with dedicated Off/On controls) so low-brightness states remain visible and consistent.
- **Diagnostics (`diagnose_lamparray`)**: Reports clear runtime statuses (`supported`, `permission_denied`, `unsupported_firmware`, `unsupported_layout`, `not_detected`, `error`) to pinpoint udev or hardware issues immediately.

> [!NOTE]
> Pre-2025 Gigabyte/AORUS keyboards that only speak the legacy vendor `0x08` protocol (without HID LampArray) are not supported in GigaMate 5.0.0. Owners of those models should use the final **GigaMate `v4.5.x`** release.

---

## 3. dGPU Power Gate

GigaMate discovers and manages discrete GPU power state through ACPI and PCI sysfs topology:

- **Topology & Power-Resource Probing**: Inspects PCI device bindings and ACPI `_OFF` / `_PS3` power resources, automatically probing whether the `remove_before_off` quirk is required on the host firmware.
- **Safety Guards**:
  - Tracks live GPU holders (processes/displays) and cuts off or blocks unsafe transitions (including a 2-second live-holder auto-cut check).
  - Supports **Optimus**, **ECO**, and **Auto** modes, plus **Keep dGPU On for Now** for temporary overrides.
  - Displays a **one-time user risk disclaimer** before enabling aggressive dGPU power-gating.

---

## 4. Hotkeys

- **Built-in Gigabyte HID Defaults**: Automatically listens for standard Gigabyte HID hotkey events (`interface 2`, `report_id 4`, codes `000084` / `000091`).
- **User-Recordable Hardware Capture**: In **Automation → Shortcuts**, users can capture any hardware key or Fn-combo directly from the HID stream and bind it to GigaMate actions or register it with KDE/GNOME.

---

## 5. Battery Care & Sensors

- **Battery Care**: Reads and writes standard Linux `power_supply` sysfs charge threshold attributes (`charge_control_start_threshold` / `charge_control_end_threshold` or vendor equivalents) when exposed by the kernel.
- **Sensors**: Reads CPU/GPU temperatures, fan RPM, and fan duty cycle directly from `gigamate_acpi` sysfs attributes.
