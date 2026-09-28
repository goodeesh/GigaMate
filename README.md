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
Experimental profile support is unconfirmed on your exact unit — it may do
nothing. Sensor monitoring is unaffected.

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
- **🧊 Discrete GPU Undervolt & Clock Control (NVIDIA)** — A V/F-curve offset ("undervolt"), an optional boost-clock cap, and an optional signed **memory-clock offset** (overclock/underclock) for the dGPU. All three are applied **only while the GPU is awake**, cleared when it sleeps, and reset to stock on reboot — so they never keep the dGPU (and battery) awake. They auto-clear while the GPU is awake but idle, and re-apply under load. Enforced by a small background service (`gigamate-dgpu.service`, independent of the tray); controlled from GigaMate Center (one **Apply** commits all three) or the CLI. Auto-application on boot/wake can be turned off in Settings (or `gigamate gpu auto off`), leaving the GPU untouched until you apply manually.
- **Hardware Hotkey Support** — Press the `Mode` key (printed as `F7` on the keycap) to cycle power profiles with native KDE Plasma OSD overlay; press the `GigaMate` key to open GigaMate Center
- **System Power Profile Sync** — Automatically syncs with KDE / GNOME / TLP / `power-profiles-daemon`
- **🧪 Experimental Model Support** — Community-evidenced Gigabyte models (GIGABYTE GAMING A16/A18 family, AERO X16 family) get profile controls as *Experimental*: shown but disabled until you explicitly enable them once, since they're unconfirmed on your exact unit. One report from a real device promotes a model to Confirmed. See [docs/EXPERIMENTAL_MODELS.md](docs/EXPERIMENTAL_MODELS.md).
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
gigamate gpu status              # Show discrete GPU power state (awake/asleep)
gigamate gpu undervolt 100       # Undervolt the NVIDIA dGPU (V/F offset, MHz; 0-255)
gigamate gpu undervolt off       # Reset the dGPU to stock
gigamate gpu undervolt status    # Show dGPU undervolt state
gigamate gpu maxclock 2100       # Cap the dGPU boost clock (MHz; 0-4000)
gigamate gpu maxclock off        # Unlock the dGPU clock
gigamate gpu maxclock status     # Show dGPU max-clock state
gigamate gpu memoffset --probe   # Report whether the memory-clock offset API is available
gigamate gpu memoffset 300       # Overclock the memory clock by +300 MHz
gigamate gpu memoffset -200      # Underclock the memory clock by -200 MHz (saves power/heat)
gigamate gpu memoffset off       # Return the memory clock to stock
gigamate gpu auto off            # Don't auto-apply dGPU tuning on boot/wake
gigamate gpu auto status         # Show dGPU auto-apply state
gigamate profile                 # Show current power profile
gigamate profile gaming          # Switch to Gaming mode
gigamate profile cycle           # Cycle to next mode + trigger OSD
gigamate profile experimental on # Enable experimental (unconfirmed) profiles for this model
gigamate profile report          # Print a paste-able model report (helps confirm models)
gigamate detect                  # Show keyboard + ACPI info
gigamate detect --acpi           # Probe ACPI capabilities
gigamate repair                  # Rebuild + reload the ACPI driver for the running kernel
gigamate repair --status         # Report ACPI driver/helper state only
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

> **Profiles are keyed on your model.** GigaMate exposes profile controls only
> for models with a matching profile. Laptops *without* a Gigabyte USB keyboard
> (e.g. GIGABYTE GAMING A16) key their profile on DMI via `gigamate calibrate
> acpi` — no RGB calibration needed. Unconfirmed models ship as **Experimental**
> (opt-in, may do nothing) rather than being hidden; see
> [docs/EXPERIMENTAL_MODELS.md](docs/EXPERIMENTAL_MODELS.md).

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

### Upgrading from 3.x

Your settings and verified-model behaviour are unchanged. New in 4.0:

- Community-evidenced models (GIGABYTE GAMING A16/A18 family, AERO X16 family)
  now get profile controls as **Experimental** — disabled until you enable them
  once (they're unconfirmed on your exact unit).
- Laptops without a Gigabyte USB keyboard (e.g. GAMING A16) are now supported
  via DMI-keyed profiles; `gigamate calibrate acpi` works without RGB calibration.
- `gigamate profile report` prints a paste-able summary to help confirm
  experimental models.

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

GigaMate deliberately does **not** show live GPU metrics (temperature, clocks,
power, VRAM). Tools such as MangoHud and nvtop already do that well, and on a
hybrid-graphics laptop every NVML read risks resuming a dGPU the kernel has
powered down — the documented cause of dGPUs that never sleep. GigaMate shows
what it is *configured and applied* to do, and otherwise leaves the dGPU alone.

The tuning itself does need NVML, so writes and the few reads it requires go
through a small privileged helper (`/usr/lib/gigamate/gigamate-dgpu-nvml`,
invoked via `pkexec`) that performs exactly one operation and exits — an open
NVML handle would keep the dGPU awake. The watcher service publishes the applied
state to a runtime state file, which the tray, Center, dashboard and CLI all
read, so no UI process ever blocks on a privileged call, and every UI path is
gated on the dGPU being awake (`dgpu_tune.nvml_allowed()`).

The memory clock can only be *pinned* to a clock the GPU already reports
(`nvmlDeviceGetMaxClockInfo`), never pushed beyond it — on a mobile part that
makes it a guaranteed-max pin or a power-saving cap, not a true overclock. The
selector only offers the values the driver enumerates.

Each laptop model is described by a JSON profile defining its RGB colour map
and ACPI capabilities. See [docs/PROFILE_SCHEMA.md](docs/PROFILE_SCHEMA.md).

For details on the USB RGB protocol and ACPI reverse engineering, see
[docs/RGB_PROTOCOL.md](docs/RGB_PROTOCOL.md) and [docs/research.md](docs/research.md).

---

## Built-in Profiles

**Verified** (confirmed on real hardware):

| Key | Match | Model | Profiles |
|-----|-------|-------|----------|
| USB `0414:8105` | Keyboard VID:PID | Gigabyte Aero X16 (EG61VH) | Quiet / Balanced / Performance / Gaming |
| DMI `product_family: GIGABYTE GAMING` | DMI | Gigabyte GAMING A16 / A18 family | Eco / Balanced / Boost |

**Experimental** (community-evidenced, unconfirmed — opt-in per machine, see
[docs/EXPERIMENTAL_MODELS.md](docs/EXPERIMENTAL_MODELS.md)):

| Key | Match | Model | Profiles |
|-----|-------|-------|----------|
| DMI `product_name prefix: GIGABYTE AERO X16` | DMI | Gigabyte AERO X16 family (all variants) | Quiet / Balanced / Performance / Gaming |

Experimental profiles appear **disabled** until you enable them once (tray menu,
GigaMate Center, or `gigamate profile experimental on`). Sensors (temps, fan RPM,
duty) work everywhere the ACPI module loads — the experimental tier only gates
profile switching.

User profiles in `~/.config/gigamate/profiles/` override built-ins (USB-keyed
files `{VID}_{PID}.json`; DMI-keyed files `dmi_<slug>.json`).

---

## Experimental Models & Reporting

GigaMate only shows profile controls it can stand behind. For models with strong
community evidence but no in-house confirmation, controls ship as
**Experimental**: visible, but disabled until you opt in.

- **Enable:** tray menu → "Enable experimental profiles…", GigaMate Center →
  Enable button, or `gigamate profile experimental on`
- **Report what they do on your unit:** `gigamate profile report`, then paste the
  output in a comment on <https://github.com/goodeesh/GigaMate/issues>. One
  confirmed report promotes the model to Verified for everyone.

Models with a *different* EC command set (AORUS 2025+, older Aero/AORUS) are
documented as blocked in [docs/EXPERIMENTAL_MODELS.md](docs/EXPERIMENTAL_MODELS.md)
until their command sets are implemented.

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
