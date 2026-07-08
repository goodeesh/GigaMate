# GigaMate — Gigabyte Laptop Management for Linux

**Bringing gimate (Windows) features to Linux.** Keyboard RGB backlight, fan monitoring,
power profile switching — all in one system tray app with community-driven model support.

![GigaMate icon](data/gigamate.svg)

---

## Features

| Feature | Description | How to use |
|---------|-------------|------------|
| 🎨 **Keyboard RGB** | 11 colours, 3 brightness levels | `gigamate rgb static purple` |
| 🌡️ **Temperature monitoring** | CPU & socket temp from ACPI sensors | `gigamate status` |
| 🔧 **Fan monitoring** | RPM + duty cycle for CPU & GPU fans | `gigamate status` |
| ⚡ **Power profiles** | Switch Quiet/Balanced/Performance/Gaming | `gigamate profile gaming` |
| 🖥️ **System tray app** | All controls in one menu, live status | `gigamate-tray` |
| 🔌 **Kernel module** | Direct ACPI access via sysfs | Auto-installed |
| 👥 **Community profiles** | Add your model without coding | `gigamate calibrate --all` |

---

## ⚠ Disclaimer

**Use this software entirely at your own risk.** No warranty expressed or implied.
See [LICENSE](LICENSE).

---

## Quick Start

```sh
git clone https://github.com/goodeesh/GigaMate.git
cd GigaMate
./install.sh        # Self-contained: detects distro, installs deps,
                    # builds kernel module, sets up service + udev
```

After install, the tray app auto-starts on login. You can also launch it manually:

```sh
gigamate-tray        # System tray control centre
```

---

## Tested Hardware

| Manufacturer | Model | CPU | GPU | USB ID | ACPI | Status |
|---|---|---|---|---|---|---|
| Gigabyte | Aero X16 (EG61VH) | AMD Ryzen AI 350 | RTX 5060 | `0414:8105` | ✅ AMW0 | ✅ Full support |

**Your model not listed?** See [Adding a new model](#adding-a-new-model).

---

## Installation

### Quick install (recommended)

```sh
git clone https://github.com/goodeesh/GigaMate.git
cd GigaMate
./install.sh
```

The installer will:
1. Detect your distribution and install system dependencies
2. Build and install the `gigamate_acpi` kernel module (if kernel headers available)
3. Install the Python package via `pip --user` or `pipx`
4. Install a udev rule for non-root USB access
5. Install and start a systemd user service (auto-start on login)
6. Migrate settings from old `gigabyte-keyboard-rgb` config if present

### Manual install

```sh
# System dependencies (Arch)
sudo pacman -S python-pyusb python-gobject gtk3 libappindicator-gtk3 linux-headers

# System dependencies (Debian/Ubuntu)
sudo apt install python3-usb python3-gi python3-gi-cairo \
  gir1.2-appindicator3-0.1 gir1.2-gtk-3.0 linux-headers-$(uname -r)

# System dependencies (Fedora)
sudo dnf install python3-pyusb python3-gobject gtk3 libappindicator-gtk3 kernel-devel

# Build kernel module (requires kernel headers)
cd src/gigamate_acpi
make CC=clang LLVM=1      # Use clang if kernel was built with it
sudo make install
sudo modprobe gigamate_acpi

# Install Python package
pip install --user .

# udev rule
sudo cp data/99-gigamate.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules

# systemd user service
cp data/gigamate.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now gigamate.service
```

### GNOME users: tray icon support

Install the AppIndicator extension:

```sh
# Arch
sudo pacman -S gnome-shell-extension-appindicator

# Debian/Ubuntu
sudo apt install gnome-shell-extension-appindicator

# Fedora
sudo dnf install gnome-shell-extension-appindicator
```

Log out and back in, or restart GNOME Shell (`Alt+F2` then `r`).

### Upgrading from gigabyte-keyboard-rgb

The installer automatically migrates your settings from
`~/.config/gigabyte-keyboard-rgb/` to `~/.config/gigamate/` on first run.

The old `gigabyte-rgb` CLI command still works and shows a deprecation notice
pointing to the new `gigamate` command.

---

## Usage

### System tray app (primary interface)

After installation, look for **GigaMate** in your application menu or run
`gigamate-tray`. The tray icon provides:

```
Colour
  ○ Red  ○ Green  ○ Yellow …  ○ Light Purple •  …  ○ Blush Pink
Brightness
  ○ Off  ○ Dim  ○ Full •
──────────────────────────────────
Power Profile
  ○ Quiet  ○ Balanced  ○ Performance  ○ Gaming •
──────────────────────────────────
Status: CPU: 56°C  |  Fan: 1875 RPM  |  Gaming
──────────────────────────────────
☐ Apply on startup
Reload profiles
──────────────────────────────────
About
──────────────────────────────────
Quit
```

The status line updates automatically every 5 seconds.

### CLI reference

```sh
# RGB keyboard
gigamate rgb static <colour>        # Set colour
gigamate rgb off                    # Turn backlight off
gigamate rgb detect                 # Scan for keyboards
gigamate rgb cycle                  # Cycle through colours
gigamate rgb calibrate              # Interactive RGB calibration

# System status
gigamate status                     # Full hardware status

# Power profiles
gigamate profile                    # Show current profile
gigamate profile gaming             # Switch to Gaming
gigamate profile quiet              # Switch to Quiet/eco

# Detection
gigamate detect                     # Show keyboard + ACPI info
gigamate detect --acpi              # Detailed ACPI capability probe

# Calibration
gigamate calibrate rgb              # Keyboard RGB only
gigamate calibrate acpi             # ACPI auto-probe only
gigamate calibrate all              # Combined

# Version
gigamate version
```

### Legacy CLI

The old `gigabyte-rgb` command syntax still works:

```sh
gigabyte-rgb static purple          # Same as 'gigamate rgb static purple'
gigabyte-rgb --calibrate            # Same as 'gigamate rgb calibrate'
gigabyte-rgb detect                 # Same as 'gigamate rgb detect'
```

---

## Adding a new model

GigaMate supports any Gigabyte Aero/AORUS laptop with a community-driven
profile system. Adding a new model takes ~10 minutes and requires **no coding**.

### Step 1: Calibrate keyboard RGB

```sh
gigamate calibrate rgb
```

This interactive session sends colour samples to your keyboard and asks you
to name them. It saves a profile to `~/.config/gigamate/profiles/`.

### Step 2: Probe ACPI capabilities

```sh
gigamate detect --acpi
```

This automatically probes all ACPI commands and detects what sensors and
power profiles your laptop supports.

### Step 3: Combine and test

```sh
gigamate calibrate all               # RGB + ACPI in one command
gigamate status                      # Verify readings look correct
gigamate profile gaming              # Test profile switching
```

### Step 4: Contribute your profile

```sh
gigamate profile contribute          # Prints PR instructions
```

This shows step-by-step instructions to:
1. Fork the repository on GitHub
2. Add your profile file
3. Open a Pull Request

Your profile will be reviewed and added to the next release, making it
available for all users with the same laptop model.

---

## Built-in profiles

| VID:PID | Model | Since | RGB | ACPI |
|---------|-------|-------|-----|------|
| `0414:8105` | Gigabyte Aero X16 (EG61VH) | v0.1.0 | ✅ 11 colours | ✅ Full |

User profiles in `~/.config/gigamate/profiles/` override built-ins with the same VID:PID.

---

## How It Works

### Architecture

```
┌─────────────────────────────────────────┐
│         Tray App (gigamate-tray)        │
│  ┌───────────────────────────────────┐  │
│  │  CLI (gigamate)                   │  │
│  └──────────┬────────────────────────┘  │
└─────────────┼───────────────────────────┘
              │
┌─────────────▼───────────────────────────┐
│           Python Modules                │
│  • acpi.py      ACPI/WMI interface      │
│  • protocol.py  USB HID RGB protocol    │
│  • profiles.py  Device profile system   │
│  • config.py    Persistent settings     │
└──────┬──────────────────────┬───────────┘
       │                      │
┌──────▼──────────┐  ┌───────▼───────────┐
│ gigamate_acpi.ko │  │ USB HID (pyusb)  │
│ (kernel module)  │  │ Interface 3      │
│ ACPI WMBD/WMBC   │  │ 8-byte commands  │
│ via sysfs        │  │                  │
└──────────────────┘  └──────────────────┘
```

### Keyboard RGB

The keyboard uses an 8-byte USB HID Feature Report on Interface 3:

```
[0x08, 0x00, program, speed, brightness, colour, 0x01, checksum]
```

See [Colours](#colours) for the 11 empirically determined colour mappings.

### ACPI Fan & Power Control

The laptop exposes an AMW0 WMI device (`\_SB.PCI0.AMW0`) with two methods:
- **WMBC** — read sensors (temperature, RPM, duty cycle)
- **WMBD** — write commands (power profile switching)

The kernel module provides a sysfs interface at `/sys/devices/platform/gigamate_acpi/`:

| File | R/W | ACPI call | Description |
|------|-----|-----------|-------------|
| `temp1_input` | R | `WMBC 0xE1` | CPU temperature (°C) |
| `temp2_input` | R | `WMBC 0xE2` | Socket temperature (°C) |
| `fan1_input` | R | `WMBC 0xE4` | Fan 1 RPM |
| `fan2_input` | R | `WMBC 0xE5` | Fan 2 RPM |
| `pwm1` | R | `WMBC 0x46` | CPU fan duty (%) |
| `pwm2` | R | `WMBC 0x47` | GPU fan duty (%) |
| `pwm1_total` | R | `WMBC 0x50` | Total fan duty (%) |
| `profile` | R/W | `WMBD 0xED` | Power profile (0=Quiet … 3=Gaming) |

Full ACPI command reference in [docs/research.md](docs/research.md).

---

## Colours

The keyboard firmware has a non-linear colour response. The tool exposes
11 empirically determined colours:

| Name | Dim `(byte5, byte4)` | Full `(byte5, byte4)` |
|------|---------------------|----------------------|
| Red | `(0x01, 0x19)` | `(0x01, 0x64)` |
| Green | `(0x02, 0x19)` | `(0x02, 0x64)` |
| Yellow | `(0x03, 0x19)` | `(0x03, 0x64)` |
| Blue | `(0x04, 0x19)` | `(0x04, 0x64)` |
| Orange | `(0x05, 0x19)` | `(0x05, 0x32)` |
| Dark Yellow | `(0x05, 0x4B)` | `(0x05, 0x64)` |
| Purple | `(0x06, 0x19)` | `(0x06, 0x32)` |
| Light Purple | `(0x06, 0x5A)` | `(0x06, 0x64)` |
| White | `(0x07, 0x19)` | `(0x07, 0x32)` |
| Light Blue | `(0x07, 0x5A)` | `(0x07, 0x64)` |
| Blush Pink | `(0x06, 0x4B)` | `(0x07, 0x4B)` |

> **Technical note:** The Aero X16 firmware has a transitional hue zone around
> brightness byte `0x4B`. To give Light Purple and Light Blue proper dim levels,
> we use `0x5A` (past the pink zone). Blush Pink is a single entry where Dim
> uses the purple colour byte and Full uses the white colour byte.

There is no custom RGB mode — only preset colour bytes.

---

## Project Structure

```
GigaMate/
├── src/
│   ├── gigamate/               # Python package
│   │   ├── __init__.py         # Version, docstring
│   │   ├── __main__.py         # python -m gigamate support
│   │   ├── acpi.py             # ACPI communication layer
│   │   ├── cli.py              # CLI (gigamate command)
│   │   ├── config.py           # JSON config persistence
│   │   ├── paths.py            # Shared path constants
│   │   ├── profiles.py         # Device profiles + calibration
│   │   ├── protocol.py         # USB HID RGB protocol
│   │   ├── tray.py             # AppIndicator3 tray app
│   │   └── profile_data/       # Built-in JSON profiles
│   │       └── 0414_8105.json
│   └── gigamate_acpi/          # Kernel module source
│       ├── Makefile
│       └── gigamate_acpi.c
├── data/
│   ├── 99-gigamate.rules       # udev rule
│   ├── gigamate.service        # systemd user unit
│   ├── gigamate.svg            # tray icon
│   └── gigamate.desktop        # desktop entry
├── docs/
│   ├── research.md             # Hardware research notes
│   └── PROFILE_SCHEMA.md       # v2 profile JSON schema
├── tests/
│   ├── test_acpi.py            # ACPI layer tests
│   ├── test_profiles.py        # Profile tests
│   └── test_protocol.py        # Protocol tests
├── install.sh                  # Cross-distro installer
├── uninstall.sh                # Uninstaller
├── pyproject.toml              # PEP 621 build metadata
├── CHANGELOG.md
├── CONTRIBUTING.md
└── README.md                   # This file
```

---

## Acknowledgements

- **[Paul Ridgway](https://blockdev.io/gigabyte-aero-w15-keyboard-and-linux-ubuntu/)** — Original reverse engineering of the 8-byte USB HID protocol
- **[paul-ridgway/aero-keyboard](https://github.com/paul-ridgway/aero-keyboard)** — Ruby protocol implementation
- **[yurikhan/aero-keyboard-rgb](https://github.com/yurikhan/aero-keyboard-rgb)** — Python port
- **[b4ckspace/aero-rgb-linux](https://github.com/b4ckspace/aero-rgb-linux)** — Early C utility
- **[Nesh108/MyAorusKeyboardSDK](https://github.com/Nesh108/MyAorusKeyboardSDK)** — Windows SDK
- **[PyUSB](https://pyusb.github.io/pyusb/)** — Python USB library
- **Linux kernel** — USB HID, ACPI, sysfs subsystems
- **The open-source community** — Countless forum posts, GitHub issues, and wiki pages

---

## License

MIT License — see [LICENSE](LICENSE).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Key areas:
- **Testing on new hardware** — Calibrate + submit profile via PR
- **ACPI research** — Investigate additional WMBC/WMBD commands
- **Packaging** — Help with distro packages (AUR, COPR, Flatpak)
- **Translations** — Internationalise the tray menu

## Uninstalling

```sh
./uninstall.sh
```

Or manually: `pip uninstall gigamate` + remove udev rule + systemd unit.
