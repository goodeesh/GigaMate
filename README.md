# GigaMate — Gigabyte Laptop Management for Linux

**Bringing gimate (Windows) features to Linux.** Keyboard RGB backlight, fan monitoring,
power profile switching — all in one system tray app with community-driven model support.

![GigaMate icon](data/gigamate.svg)

---

## Quick Start

```sh
curl -sSL https://raw.githubusercontent.com/goodeesh/GigaMate/main/install.sh | bash
```

After install, the tray app auto-starts on login. Launch manually with `gigamate-tray`.

---

## Features

- **Keyboard RGB** — Set colour and brightness from tray or CLI
- **Temperature monitoring** — CPU and system temperatures
- **Fan monitoring** — RPM and duty cycle readback
- **Power profiles** — Switch between Quiet/Balanced/Performance/Gaming
- **System tray app** — All controls in one place, live status updates
- **Community model profiles** — Add your laptop model without coding

---

## Usage

### System tray app

The tray icon shows colour, brightness, power profile, and live status:

```
Colour → 11+ colours depending on your model
Brightness → Off / Dim / Full
Power Profile → Quiet / Balanced / Performance / Gaming
Status → CPU: 56°C  |  Fan: 1875 RPM  |  Gaming
Apply on startup
Reload profiles
```

### CLI

```sh
gigamate rgb static <colour>     # Set keyboard colour
gigamate rgb off                 # Turn backlight off
gigamate rgb detect              # Scan for keyboards
gigamate rgb calibrate           # Interactive RGB calibration
gigamate status                  # Full hardware status
gigamate profile                 # Show current power profile
gigamate profile gaming          # Switch to Gaming mode
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
gigamate detect --acpi              # Probe ACPI capabilities
gigamate profile contribute         # Print PR instructions to share
```

No coding required. See [CONTRIBUTING.md](CONTRIBUTING.md) for details.

---

## Installation

### One-liner (recommended)

```sh
curl -sSL https://raw.githubusercontent.com/goodeesh/GigaMate/main/install.sh | bash
```

The installer auto-detects your distro, installs dependencies, builds the kernel
module (if headers available), sets up udev rules, and installs a systemd service.

### Manual install

See the [Installation wiki page](https://github.com/goodeesh/GigaMate/wiki/Installation)
for per-distro manual steps.

### Upgrading from gigabyte-keyboard-rgb

Settings migrate automatically from `~/.config/gigabyte-keyboard-rgb/` on first run.

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
├── tests/                    # 65+ unit tests
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
- **Packaging** — Help with AUR, COPR, Flatpak

## Uninstalling

```sh
curl -sSL https://raw.githubusercontent.com/goodeesh/GigaMate/main/uninstall.sh | bash
```

---

## License

MIT License — see [LICENSE](LICENSE).
