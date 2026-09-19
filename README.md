# GigaMate — Gigabyte Laptop Management for Linux

**Bringing gimate (Windows) hardware features to Linux (without the AI crap). Control your hardware:** Keyboard RGB backlight, fan monitoring,
power profile switching — all in one system tray app with community-driven model support.

![GigaMate icon](data/gigamate.svg)

---

## ⚠ Disclaimer

**Use this software entirely at your own risk.**

GigaMate writes directly to hardware — USB HID for keyboard RGB and ACPI
for fan/power control. The authors accept no responsibility for any damage,
malfunction, or data loss caused by using this software.

Known risks:
- Certain animated keyboard effects can hang the keyboard firmware
  (requires USB reset to recover). Only `static` mode is safe.
- ACPI profile switching adjusts CPU/GPU power limits.
- To allow control without root, the kernel module exposes `profile` and
  `charge_limit` (and the udev rules expose the keyboard) as world-writable
  (`0666`). Any local user on the machine can therefore change these settings.

Tested on specific Gigabyte models only. Other models may behave differently.

---

## Quick Start

```sh
curl -sSL https://raw.githubusercontent.com/goodeesh/GigaMate/main/install.sh | bash
```

After install, the tray app auto-starts on login. Launch manually with `gigamate-tray`.

---

## Features

- **🖥️ GigaMate Center** — Dedicated desktop control panel (PyQt6) engineered for KDE Plasma, GNOME, and Hyprland
- **🔋 Battery Care & Limiter** — Set max 80% (or custom) charge limit to protect battery longevity; health & cycle monitoring
- **💤 Suspend/Resume Clean State Handler** — Turns off RGB gracefully before sleep, restores power profile, RGB and battery limit on resume
- **🔁 Persistent Settings** — Fan profile, RGB and charge limit are safely re-applied on login, app launch and resume
- **⌨️ Keyboard RGB & Idle Sleep** — Set colours and brightness, plus configurable idle backlight auto-off (tray, GUI, or `gigamate rgb idle`)
- **🌡️ Temperature & Fan Monitoring** — Live CPU and socket thermals, dual fan RPM, and duty cycle readback
- **⚡ Dynamic Power Boost** — NVIDIA Dynamic Boost (~80W boost via `nvidia-powerd`) & AMD SmartShift power balancing
- **🧊 Discrete GPU Undervolt & Clock Cap (NVIDIA)** — A V/F-curve offset ("undervolt") and an optional boost-clock cap for the dGPU. Both are applied **only while the GPU is awake**, cleared when it sleeps, and reset to stock on reboot — so they never keep the dGPU (and battery) awake. They auto-clear while the GPU is awake but idle, and re-apply under load. Enforced by a small background service (`gigamate-dgpu.service`, independent of the tray); controlled from GigaMate Center or the CLI. Auto-application on boot/wake can be turned off in Settings (or `gigamate gpu auto off`), leaving the GPU untouched until you apply manually.
- **Hardware Hotkey Support** — Press the `Mode` key (printed as `F7` on the keycap) to cycle power profiles with native KDE Plasma OSD overlay; press the `GigaMate` key to open GigaMate Center
- **System Power Profile Sync** — Automatically syncs with KDE / GNOME / TLP / `power-profiles-daemon`
- **System Tray App** — Lightweight tray daemon with rich multi-metric hover tooltips

---

## Usage

### GigaMate Center (GUI)

Launch from your desktop application launcher or via command line:
```sh
gigamate center       # or click "GigaMate Center..." in the tray menu
```

### System Tray App

The tray icon provides instant status and quick actions:
```
GigaMate Center...
Status → CPU: 48°C  |  Fan: 1875 RPM  |  Gaming  |  dGPU: Asleep  |  Batt: 85% (AC)
Power Profile → Quiet / Balanced / Performance / Gaming
Battery: 85% (AC)  [x] Battery Care (Cap at 80%)
Colour / Brightness / Backlight idle off
```

### CLI

```sh
gigamate center                  # Launch modern GUI Control Panel
gigamate battery                 # Show battery status, health, and limit
gigamate battery --limit 80      # Set maximum battery charge limit to 80%
gigamate rgb static <colour>     # Set keyboard colour
gigamate rgb off                 # Turn backlight off
gigamate rgb idle 60             # Auto-off backlight after 60s idle (or 'off')
gigamate status                  # Full hardware status
gigamate gpu status              # Show discrete GPU power state
gigamate gpu undervolt 100       # Undervolt the NVIDIA dGPU (V/F offset, MHz; 0-255)
gigamate gpu undervolt off       # Reset the dGPU to stock
gigamate gpu undervolt status    # Show dGPU undervolt state
gigamate gpu maxclock 2100       # Cap the dGPU boost clock (MHz; 0-4000)
gigamate gpu maxclock off        # Unlock the dGPU clock
gigamate gpu maxclock status     # Show dGPU max-clock state
gigamate gpu auto off            # Don't auto-apply dGPU tuning on boot/wake
gigamate gpu auto status         # Show dGPU auto-apply state
gigamate profile                 # Show current power profile
gigamate profile gaming          # Switch to Gaming mode
gigamate profile cycle           # Cycle to next mode + trigger OSD
gigamate detect                  # Show keyboard + ACPI info
gigamate detect --acpi           # Probe ACPI capabilities
gigamate calibrate all           # Complete model calibration
gigamate profile contribute      # Share your profile via PR
```

Legacy `gigabyte-rgb` commands still work with a deprecation notice.

---

## Adding a New Model

Your laptop isn't supported yet? Run these three commands:

```sh
gigamate calibrate rgb              # Map keyboard colours (5 min)
gigamate calibrate acpi             # Probe ACPI + generate a candidate profile
gigamate profile contribute         # Print PR instructions to share
```

No coding required. See [CONTRIBUTING.md](CONTRIBUTING.md) for details.

> **Power profiles are only shown on verified models.** GigaMate never exposes
> profile controls on hardware whose profile switching hasn't been confirmed
> (an EC can accept a write without acting on it). `gigamate calibrate acpi`
> generates a candidate profile for your model — sensors are auto-detected, but
> profile switching stays off until you explicitly confirm it as experimental,
> and it only unlocks for everyone once the profile is shipped as a built-in.

---

## Installation

### One-liner (recommended)

```sh
curl -sSL https://raw.githubusercontent.com/goodeesh/GigaMate/main/install.sh | bash
```

The installer auto-detects your distro, installs dependencies, builds the kernel
module (if headers available), sets up udev rules, and installs a systemd service.

**ACPI kernel module (no AUR required):**

- The `gigamate_acpi` kernel module is built and installed via **DKMS**
  (installed from your distro's *official* repositories — never from AUR).
- DKMS auto-rebuilds the module whenever your kernel is updated, so fan/temp/
  power control keeps working across kernel upgrades.
- You need the **kernel headers matching your running kernel** (`uname -r`).
  The installer picks the right package for Arch-family kernels
  (CachyOS/zen/lts/hardened); on Debian/Ubuntu it uses `linux-headers-$(uname -r)`,
  on Fedora/SUSE `kernel-devel`.
- If **Secure Boot** is enabled, enroll the DKMS signing key once after install:
  `sudo mokutil --import /var/lib/dkms/mok.pub`, then reboot and confirm in the
  MOK manager. Without this the module will not load after reboot.

### Manual install

See the [Installation wiki page](https://github.com/goodeesh/GigaMate/wiki/Installation)
for per-distro manual steps.

### Upgrading from gigabyte-keyboard-rgb

Settings migrate automatically from `~/.config/gigabyte-keyboard-rgb/` on first run.

### Upgrading from 2.0.x

Your existing keyboard/startup/idle settings are kept. GigaMate 3.0 adds GigaMate
Center, battery care and suspend/resume handling; the new features are **opt-in**
and configured on first launch of the Center (you can re-run setup any time from
Settings → *Run Setup Again*). The first update hop is performed by the old 2.0.x
updater (`curl … install.sh | bash`), which is not checksum-verified; subsequent
updates use the pinned, checksum-verified updater. GigaMate Center requires
PyQt6 (installed automatically; on older distros it may come from pip).

---

## First-run setup

On first launch, GigaMate Center runs a short setup wizard. Nothing on your
hardware is changed until you choose it:

- Keyboard colour/brightness and whether to apply it at startup
- Idle backlight auto-off
- Battery charge limit (only where supported)
- System power-profile sync (only on supported Gigabyte laptops)

Unsupported hardware (non-Gigabyte, no kernel module, no compatible keyboard,
or no battery limit) is clearly reported and the corresponding options are
skipped. Existing 2.0.x users get an "upgrade" variant that keeps their current
keyboard/idle settings and additionally offers the new features.

---

## How It Works

GigaMate has two hardware backends:

| Backend | Purpose | Communication |
|---------|---------|---------------|
| **USB HID** | Keyboard RGB | 8-byte control transfers via pyusb |
| **Kernel module** | Fan, temp, power | ACPI WMBD/WMBC via sysfs |

The kernel module (`gigamate_acpi.ko`) exposes sensors at
`/sys/devices/platform/gigamate_acpi/`. The Python layer auto-detects
which backends are available and degrades gracefully if one is missing.
If the `acpi_call` module happens to be installed on your system, it is
used automatically as an optional fallback — GigaMate never requires it.

The **dGPU monitor** reads the NVIDIA GPU's power state from
`/sys/bus/pci/devices/<bdf>/power/{runtime_status,power_state}`. These are
plain kernel power-management reads — GigaMate never calls `nvidia-smi`, so
checking the state does **not** wake the GPU.

Each laptop model is described by a JSON profile defining its RGB colour map
and ACPI capabilities. See [docs/PROFILE_SCHEMA.md](docs/PROFILE_SCHEMA.md).

For details on the USB RGB protocol and ACPI reverse engineering, see
[docs/RGB_PROTOCOL.md](docs/RGB_PROTOCOL.md) and [docs/research.md](docs/research.md).

---

## Built-in Profiles

| VID:PID | Model |
|---------|-------|
| `0414:8105` | Gigabyte Aero X16 (EG61VH) |

User profiles in `~/.config/gigamate/profiles/` override built-ins for the same VID:PID.

---

## Project Structure

```
GigaMate/
├── src/gigamate/             # Python package
├── src/gigamate_acpi/        # Kernel module source
├── data/                     # Service, udev, icon, desktop
├── docs/                     # Research notes + profile schema
├── tests/                    # 240+ unit tests
├── install.sh / uninstall.sh
├── README.md / CONTRIBUTING.md
└── pyproject.toml / LICENSE
```

---

## Acknowledgements

- **[Paul Ridgway](https://blockdev.io/gigabyte-aero-w15-keyboard-and-linux-ubuntu/)** — Original USB HID protocol reverse engineering
- **[paul-ridgway/aero-keyboard](https://github.com/paul-ridgway/aero-keyboard)** — Ruby implementation
- **[yurikhan/aero-keyboard-rgb](https://github.com/yurikhan/aero-keyboard-rgb)** — Python port
- **[PyUSB](https://pyusb.github.io/pyusb/)** — Python USB library

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Key areas:
- **Adding a new model** — Calibrate + submit profile via PR
- **ACPI research** — Investigate additional commands on your model
- **Packaging** — Help with distro packages (Flatpak, COPR, etc.)

## Uninstalling

```sh
curl -sSL https://raw.githubusercontent.com/goodeesh/GigaMate/main/uninstall.sh | bash
```

---

## License

MIT License — see [LICENSE](LICENSE).
