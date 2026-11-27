# GigaMate 5.0.0 — Frequently Asked Questions (FAQ)

## Contents

- [1. Hardware Compatibility](#1-hardware-compatibility)
- [2. Upgrading from v4.5.x & Rolling Back](#2-upgrading-from-v45x--rolling-back)
- [3. dGPU Power Gate Safety & Troubleshooting](#3-dgpu-power-gate-safety--troubleshooting)
- [4. GPU Performance & Sleep-Aware V/F Tuning](#4-gpu-performance--sleep-aware-vf-tuning)
- [5. Keyboard RGB & Idle Backlight Sleep](#5-keyboard-rgb--idle-backlight-sleep)
- [6. Automation & OS Power Managers (TLP / KDE / GNOME)](#6-automation--os-power-managers-tlp--kde--gnome)
- [7. Battery Care & Periodic Calibration](#7-battery-care--periodic-calibration)
- [8. Kernel Module, Secure Boot & Diagnostics](#8-kernel-module-secure-boot--diagnostics)

---

## 1. Hardware Compatibility

### Which laptops are supported by GigaMate 5.0.0?
GigaMate 5.0.0 targets modern Gigabyte and AORUS laptops whose firmware and keyboard expose standard auto-discoverable interfaces:
- **Thermal & Power Profiles:** ACPI `AMW0` device exposing `WMBD(0xED)` in the DSDT (inspected safely via `gigamate_acpi`'s `wmbd_aml` sysfs attribute).
- **Keyboard Backlighting:** USB/HID keyboards advertising **HID LampArray (`Usage Page 0x59`)** with Reports `1`, `5`, and `6` (zero `pyusb` required).
- **Battery Charge Threshold:** Exposed via `gigamate_acpi` (`/sys/devices/platform/gigamate_acpi/charge_limit`) or standard Linux `power_supply` sysfs (`charge_control_end_threshold`).
- **Discrete GPU Control:** NVIDIA laptops (runtime PM telemetry, sleep-aware NVML V/F tuning, `nvidia-powerd` Dynamic Boost, ACPI power gate) and AMD discrete GPUs (SmartShift / ACPI power gate).

Check what GigaMate discovered on your machine with:
```sh
gigamate detect --acpi
gigamate doctor
gigamate profile report
```

### What happened to `profile_data/*.json` and `gigamate calibrate`?
They were removed in v5.0.0. Static JSON profiles required guessing or crowdsourcing per-BIOS quirks and locked unrecognized models behind `"experimental"` flags. v5.0.0 inspects live firmware tables (`WMBD(0xED)` + `NPCF`), HID report descriptors, and PCI/ACPI sysfs directly at runtime instead.

### What if I own an older pre-2025 Gigabyte / AORUS laptop?
If your keyboard uses the legacy vendor `0x08` protocol without HID LampArray, or your BIOS lacks `WMBD(0xED)`, **stay on (or roll back to) GigaMate `v4.5.3`**. v5.0.0 intentionally refuses to blindly guess legacy `WMBD` methods on unrecognized BIOSes.

---

## 2. Upgrading from v4.5.x & Rolling Back

### Is upgrading from `v4.5.x` to `v5.0.0` safe? Will my settings survive?
Yes:
- `./install.sh --update` (and `gigamate update`) preserve `~/.config/gigamate/config.json` and never delete `~/.config/gigamate/profiles/`.
- All common preferences (`colour`, `brightness`, `startup_apply`, `idle_off_enabled`, `idle_timeout_sec`, `charge_limit`, `dgpu_undervolt_*`, `dgpu_power_mode`, `hotkey_overrides`) carry forward cleanly.
- If `install.sh` detects a pre-LampArray keyboard or missing `WMBD(0xED)` profile table after upgrading, it prints a compatibility banner showing the exact `v4.5.3` rollback command.

### Why did 5.0.0 drop GTK3 and `AppIndicator3`?
Previously, GigaMate mixed a GTK3/`AppIndicator3` system tray with a PyQt6 control center and ran automation logic inside the tray process, leading to stale menu labels, `(dGPU off)` lockouts, and heavy C library dependencies. v5.0.0 splits background logic into a pure-Python daemon (`gigamate.daemon`) and wraps it in a stateless PyQt6 `QSystemTrayIcon` (`gigamate.tray`), unifying the entire stack on Python 3.10+ + PyQt6 + `evdev`.

### How do I roll back to `v4.5.3`?
```sh
curl -fsSL https://raw.githubusercontent.com/goodeesh/GigaMate/v4.5.3/install.sh | bash -s -- --tag v4.5.3
```
Your existing `~/.config/gigamate/profiles/` directory and legacy `v4.x` config keys remain intact on disk, so `v4.5.3` picks them back up automatically.

---

## 3. dGPU Power Gate Safety & Troubleshooting

### How safe is dGPU rail gating (`ECO` / `Auto`) on laptops other than the AERO X16 1VH?
**Be cautious — `Optimus` is always the safest default.** Here is the exact safety boundary across laptops:

1. **What is model-agnostic vs. model-verified:**
   - **Pre-gate safety checks** work on any Linux laptop: GigaMate verifies no external display is connected on dGPU DRM connectors, no process holds `/dev/nvidia*`, no audio daemon holds `/dev/snd/controlC*`, and wakes a suspended GPU cleanly before stopping `nvidia-powerd` (preventing cold GSP resume hangs).
   - **PCI removal + ACPI evaluation (`remove_before_off`):** Automatically discovers the dGPU PCI device, audio sibling (`.1`), upstream PCIe root bridge, and ACPI `PowerResource` (`PG00._OFF` / `_PS3`).
   - **AERO X16 EC timeout recovery is strictly scoped:** Clearing EC register `0x2D` bit 0 (`EC0.DSMD`) on `PG00._ON` `AE_AML_LOOP_TIMEOUT` in `gigamate_acpi.c` is strictly gated by DMI (`GIGABYTE` + `AERO X16`) **and** ACPI handle lookup (`\_SB.PCI0.SBRG.EC0.DSMD`). Unknown laptops never receive raw writes to EC register `0x2D`.
   - **PCIe Secondary Bus Reset (`reset_subordinate`) is strictly scoped:** On wake, GigaMate pauses `/sys/bus/pci/drivers_autoprobe` and only issues `reset_subordinate` when the root port has **zero bound drivers** and **exclusively hosts the dGPU (`.0`) and its HDA audio function (`.1`)**. If a laptop shares that root port with NVMe, Thunderbolt/USB4, or USB-C PD controllers, SBR is skipped and GigaMate falls back to safe Link Control Retrain (`LNKCTL` bit 5).
2. **What could still go wrong on untested laptops?**
   - Firmware DSDT power-on methods (`_ON` / `_PS0`) vary by ODM. On untested models, cutting the rail can fail to re-enumerate on wake, train at reduced PCIe lanes (`x1` / `x4`), or freeze until reboot.
   - Always test manually first (`gigamate gpu power probe --live-test` on idle AC with no external displays) before enabling unattended `Auto` mode.
   - **Reversibility:** Cutting the dGPU power rail is not permanent flash modification — a normal reboot re-initializes ACPI and the GPU cleanly.

### What is the difference between `Optimus`, `ECO`, and `Auto`?
| Mode | Behavior |
| :--- | :--- |
| **Optimus** *(Default)* | Leaves the discrete GPU powered on the PCI bus; the kernel & NVIDIA/AMD runtime PM suspend it to `D3cold` (~1–3 W) automatically when idle. External HDMI/DP ports work normally. |
| **ECO** *(Battery Only)* | Immediately asks to close any apps holding the dGPU (if needed), detaches PCI functions, and cuts the ACPI power rail (~0 W). Stays off until you switch back to Optimus or plug into AC. |
| **Auto** | Keeps the dGPU available on **AC power**. When you unplug AC, prompts with the live confirmation window to cut the dGPU rail on **Battery**, and automatically wakes & restores the GPU (`x8`/`x16` + `nvidia-powerd` + saved V/F tuning) when AC returns. |

### Why is `ECO` disabled while plugged into AC?
`ECO` is designed for untethered battery life. On AC power, use **Optimus** (GPU active / runtime `D3cold`) or **Auto** (active on AC, gated off on battery).

### Why are `Performance` and `Gaming` power profiles locked when the dGPU is gated off?
On modern Gigabyte/AORUS laptops, `Performance` and `Gaming` rely on **NVIDIA Dynamic Boost / AMD SmartShift** firmware budgets that coordinate with the dGPU. While the dGPU PCI device is removed and powered off, GigaMate locks high-power GPU profiles and automatically steps down from `Performance`/`Gaming` to `Balanced` or `Quiet`. Plugging back into AC in `Auto` mode (or switching back to `Optimus`) wakes the dGPU first, then restores your target AC profile.

### Why does my HDMI / DisplayPort external monitor stop working while gated off?
Laptops with MUXed or direct-wired HDMI/DisplayPort ports route those physical connectors straight to the discrete GPU. When the dGPU power rail is cut, those connectors have no GPU driving them. GigaMate blocks gating off automatically whenever it detects any `connected` display connector belonging to the dGPU (disconnect it first to gate off). Note: integrated USB-C DisplayPort wired to the iGPU continues working normally.

### Why does system audio briefly pause (~0.5 s) when cutting or restoring dGPU power?
The discrete GPU exposes an HDMI/DP HDA audio controller (`.1`). If PipeWire/WirePlumber holds `/dev/snd/controlC*` open during PCI removal, the Linux kernel's `snd_card_free()` enters an uninterruptible `D`-state hang. To prevent that kernel deadlock, GigaMate briefly stops and restarts your user audio socket/service for ~0.5 s during PCI detach.

### What if a wake fails, Dynamic Boost drops to 50 W, or PCIe link width is stuck at `x4`?
1. Run `gigamate gpu status` and `nvidia-smi -q -d POWER` / `cat /sys/bus/pci/devices/<bdf>/current_link_width` (after waking the GPU).
2. Ensure the updated `gigamate_acpi` module and `/usr/lib/gigamate/gigamate-dgpu-gate` helper from v5.0.0 are installed (`gigamate repair` / `./install.sh --update`).
3. If the NVIDIA kernel driver wedged mid-transition (`Reason: wedged`), **reboot the laptop** to reset the GPU cleanly — GigaMate pauses further gating retries until reboot so it won't loop on a wedged device.

---

## 4. GPU Performance & Sleep-Aware V/F Tuning

### Do V/F undervolt, boost clock cap, and VRAM offset wake a sleeping dGPU?
**No.** Both GigaMate Center (`GpuPage`) and the background watcher (`gigamate-dgpu-watch` / `gigamate-dgpu.service`) read `/sys/bus/pci/devices/*/power/runtime_status` purely from sysfs and read cached state from disk. NVML is **only** touched while the dGPU is already awake and active (`active`), and offsets are cleared back to stock when idle or before sleep so runtime `D3cold` power saving keeps working normally.

### Why didn't moving a slider on the GPU page change my clocks immediately?
Moving **Core V/F Offset**, **Max Boost Cap**, or **VRAM Clock** stages changes locally until you click **Apply Tuning** (making a single atomic config update and helper call). Clicking **Reset** beside any individual control immediately resets that control back to stock.

---

## 5. Keyboard RGB & Idle Backlight Sleep

### Why is the minimum active keyboard brightness clamped to 5%?
On Gigabyte HID LampArray keyboards, the intensity channel is effectively binary on/off, so dimming is performed by scaling the 8-bit `R, G, B` values directly. Below `5%`, integer rounding (`round(c * pct / 100)`) quantizes colour ratios so aggressively that purples, oranges, and pastels distort into wrong hues. Keeping active brightness at `5–100%` with a dedicated **Turn Off / Turn On** control preserves accurate 24-bit colour at dim levels.

### Why does idle backlight sleep wake up immediately on Wayland, or when I'm using an external keyboard/mouse?
Enable **Built-in keyboard & touchpad only** in **Automation → ON INACTIVITY — Keyboard Idle Sleep**!
- By default, desktop idle monitors (`mutter` / `KIdleTime` / global `evdev`) count **any** USB/Bluetooth mouse or external mechanical keyboard movement as activity. When docked, moving your external mouse wakes the laptop's built-in keyboard backlight.
- With **Built-in keyboard & touchpad only** enabled, GigaMate inspects ONLY internal laptop input devices (`AT Translated Set 2 keyboard` / `i2c` / internal HID touchpad).

### Does idle sleep work with key-remappers like Toshy or `xwaykeyz`?
Yes! Tools like **Toshy** and **`xwaykeyz`** grab `AT Translated Set 2 keyboard`'s `evdev` node with `EVIOCGRAB`, which stops other `evdev` readers from seeing keystrokes. When **Built-in keyboard & touchpad only** is enabled, GigaMate's idle monitor automatically falls back to reading non-blocking **kernel `/dev/hidraw*` `input0`** packets (plus normal `evdev` for ungrabbed touchpads), keeping idle wake/sleep accurate even under exclusive remapper grabs on Wayland.

### My keyboard isn't detected or says "permission denied" — how do I fix it?
Run:
```sh
gigamate doctor
```
Make sure `/etc/udev/rules.d/70-gigamate.rules` is installed (the `70-` prefix must run before `73-seat-late.rules` so `systemd-logind` grants `uaccess` ACLs), then reload udev or reboot/re-login:
```sh
sudo udevadm control --reload-rules && sudo udevadm trigger
```
Verify ACLs on your hidraw node with `getfacl /dev/hidraw*`. Note that headless SSH sessions without an active local seat do not receive `uaccess` ACLs.

---

## 6. Automation & OS Power Managers (TLP / KDE / GNOME)

### Which AC / Battery Power Profile Mode should I pick?
- **Off:** GigaMate never changes your power profile automatically on plug/unplug. Best if you manage profiles manually or want zero interaction with other power tools.
- **Follow System:** GigaMate watches your OS power manager (**TLP**, **KDE PowerDevil**, or **GNOME `power-profiles-daemon`**) and mirrors its changes to the Gigabyte EC fan/Watt profile and linked keyboard colour. Best if you prefer using your desktop environment's built-in battery slider.
- **GigaMate-Driven:** GigaMate owns AC vs. Battery switching directly (allowing Gigabyte **Gaming** on AC, which standard PPD profiles lack, plus coordinating dGPU Auto-Gate wake-before-switch on AC return).

### Why is "Synchronize system power profile with fan profile" locked ON in GigaMate-Driven mode?
In **GigaMate-Driven** mode, syncing the OS power backend (`power-profiles-daemon` / NVIDIA Dynamic Boost / AMD SmartShift) prevents your desktop environment and Gigabyte EC firmware from fighting each other with mismatched thermal/power targets. Switch to **Off** or **Follow System** if you want independent control.

### What does "Deactivate Conflicts (Take Over)" do? Can I undo it?
If an external service (such as `tlp.service` or PowerDevil AC profile actions) is actively overwriting profiles on plug/unplug, clicking **Deactivate Conflicts (Take Over)** safely pauses those conflicting services/rules with polkit/pkexec authorization and saves a reversible hand-off record. You can restore your original power manager configuration at any time via **Restore Paused Power Managers**.

---

## 7. Battery Care & Periodic Calibration

### What is the recommended charge limit?
Setting the threshold to **80% Daily Care** keeps lithium-ion cells out of high-voltage stress (~4.2 V/cell) during desk use, reducing capacity degradation over time. Use **60% Storage** if the laptop stays plugged in almost constantly, or click **Charge to 100% Once** before travel (the threshold restores automatically after reaching full charge).

### What does "Periodic BMS full-charge calibration" do?
Extended 60%–80% capping can gradually drift the battery management system's (BMS) state-of-charge percentage estimate. Enabling **Periodic BMS full-charge calibration** (14 / 30 / 60 days) automatically triggers a single supervised 100% top-up when the interval elapses, then re-enables your configured charge threshold cleanly.

---

## 8. Kernel Module, Secure Boot & Diagnostics

### After a kernel update, GigaMate says "ACPI kernel driver not loaded" — how do I fix it?
Click **Repair Driver** in **Settings → Background Daemon & Driver**, or run:
```sh
gigamate repair
```
If your system uses **Secure Boot**, remember to enroll `/var/lib/dkms/mok.pub` once:
```sh
sudo mokutil --import /var/lib/dkms/mok.pub
```

### How do I collect logs and report a bug or new laptop model?
Include all three of the following when opening an issue at <https://github.com/goodeesh/GigaMate/issues>:
1. Full output of **`gigamate doctor`**
2. Full output of **`gigamate profile report`** (and **`gigamate gpu power probe`** if reporting dGPU gating)
3. Daemon logs if related to tray, idle sleep, hotkeys, or GPU watcher:
   ```sh
   journalctl --user -u gigamate.service -u gigamate-dgpu.service -n 100 --no-pager
   ```
