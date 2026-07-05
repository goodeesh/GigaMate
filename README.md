# Gigabyte Keyboard RGB Control

Control the keyboard backlight on Gigabyte Aero and AORUS laptops running Linux.

![Tray app screenshot](data/gigabyte-keyboard-rgb.svg)

## ⚠ Disclaimer

**Use this software entirely at your own risk.**

This tool is shared in the hope it will be useful, but with **absolutely no warranty** expressed or implied. The authors accept no responsibility for any damage, malfunction, or data loss caused by using this software.

**Known risk:** Certain animated-effect commands we tested during development temporarily caused the keyboard firmware to enter an unresponsive state where the backlight cycled through colours and would not respond to further commands. The keyboard recovered after a USB device reset and brightness-0 sequence, but this is not guaranteed in all cases. This tool only exposes safe static-colour controls by default — the CLI exposes the full set of effect codes as documented below, but you use them at your own risk. When in doubt, stick to `static` (the default).

## Tested Hardware

| Manufacturer | Model | CPU | GPU | Keyboard USB ID | Status |
|---|---|---|---|---|---|
| Gigabyte | Aero X16 (EG61VH) | AMD Ryzen AI 350 | RTX 5060 | `0414:8105` | ✅ Confirmed working |

### Likely compatible (untested)

- **Gigabyte Aero 15 W15** — Known to use the same 8-byte USB HID protocol (VID `04d9:8008` → `1044:7a39`). Source: Paul Ridgway's [blockdev.io article](https://blockdev.io/gigabyte-aero-w15-keyboard-and-linux-ubuntu/) (January 2019). Unconfirmed on this repo's code, but the protocol is identical.
- **Gigabyte AORUS laptops** — Use Gigabyte Control Center on Windows, which communicates via the same HID interface family. Untested.
- Other Gigabyte laptops with a USB HID keyboard under VID `0414` may work with a custom PID override.

If you have one of these models, please open an issue with your `lsusb` output and confirm whether it works — verified entries will be added.

### How to find your keyboard's PID

```sh
lsusb | grep -i gigabyte
```

If the PID differs from `8105`, you can override it:

```sh
gigabyte-rgb --pid 0x8105 static purple
```

Or set it permanently in `~/.config/gigabyte-keyboard-rgb/config.json`.

## Installation

### Quick install

```sh
git clone https://github.com/goodeesh/gigabyte-keyboard-rgb.git
cd gigabyte-keyboard-rgb
./install.sh
```

The installer will:
1. Detect your distribution and install system dependencies (pyusb, PyGObject, Gtk 3.0, AppIndicator3)
2. Install the Python package via `pip --user` (or pipx if available)
3. Install a udev rule to `/etc/udev/rules.d/` (requires sudo — enables non-root access)
4. Install and start a systemd user service to launch the tray app on login
5. **Apply your saved colour automatically** on every login

### Manual install

```sh
# System dependencies (Arch)
sudo pacman -S python-pyusb python-gobject gtk3 libappindicator-gtk3

# System dependencies (Debian/Ubuntu)
sudo apt install python3-usb python3-gi python3-gi-cairo gir1.2-appindicator3-0.1 gir1.2-gtk-3.0

# System dependencies (Fedora)
sudo dnf install python3-pyusb python3-gobject gtk3 libappindicator-gtk3

# Install the Python package
pip install --user .

# udev rule (for non-root access)
sudo cp data/99-gigabyte-keyboard-rgb.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules && sudo udevadm trigger

# systemd user service (auto-start tray on login)
cp data/gigabyte-keyboard-rgb.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now gigabyte-keyboard-rgb.service
```

### GNOME users: tray icon support

If you use GNOME Shell, install the AppIndicator extension:

```sh
# Arch
sudo pacman -S gnome-shell-extension-appindicator

# Debian/Ubuntu
sudo apt install gnome-shell-extension-appindicator

# Fedora
sudo dnf install gnome-shell-extension-appindicator
```

After installing, log out and back in, or restart GNOME Shell (`Alt+F2` then `r`).

## Usage

### Tray icon app

After installation, the tray app starts automatically on login via systemd. You'll also find **"Gigabyte Keyboard RGB"** in your application menu (GNOME app grid, KDE kicker, rofi, dmenu, etc.) — clicking it launches the tray icon if the systemd service isn't running.

Once running, you'll see a keyboard icon in your system tray.

**Menu:**
- **Colour** — radio list of 11 colours
- **Brightness** — Off, Dim, Full
- **Apply on startup** — toggle auto-apply on login
- **Reset keyboard drivers** — re-attach kernel drivers if the keyboard stops typing
- **Quit** — exit the tray app

### CLI

```sh
gigabyte-rgb static purple        # Set static purple (full)
gigabyte-rgb static blue --level dim   # Dim blue
gigabyte-rgb --cycle                   # Cycle through all colours
gigabyte-rgb off                       # Turn backlight off
gigabyte-rgb detect                    # Scan for compatible keyboards
gigabyte-rgb --reset                   # Re-attach keyboard drivers
gigabyte-rgb --help                    # Full help
```

### Colours

The keyboard firmware has a non-linear colour response — the visible hue depends on both the colour byte (byte 5) and the brightness byte (byte 4) in ways that aren't documented and weren't apparent from the original Aero W15 reverse engineering. This tool exposes the 11 distinct visible colours we confirmed experimentally on the Aero X16 (EG61VH).

Each colour maps to a fixed `(byte5, byte4)` pair per brightness level. The Dim and Full presets use different brightness bytes (and, for some colours, different colour bytes) to keep the hue consistent:

| Name | Dim `(byte5, byte4)` | Full `(byte5, byte4)` |
|---|---|---|
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

**Why does Dim sometimes use a different colour byte?** The Aero X16 firmware has a transitional hue zone around brightness byte `0x4B` where the purple and white colour bytes both produce blush pink instead of dimming their "high-hue" variants (Light Purple / Light Blue). To give Light Purple and Light Blue proper Dim levels we use `0x5A` (the first byte past the pink zone that locks the correct hue). Blush Pink itself is exposed as a single menu entry where Dim uses the purple colour byte (dimmer pink) and Full uses the white colour byte (brighter pink).

> Note: There is no custom RGB mode. The hardware only supports these preset colour bytes plus a rainbow/random cycle (which we do not expose as a "colour" because it triggers an animated effect that cannot be cleanly stopped once started).

## How It Works

The keyboard is a USB HID device (VID `0414`, PID `8105`) with 5 interfaces:

| Interface | Function | Used by this tool |
|---|---|---|
| 0 | Keyboard input (standard HID) | Never touched |
| 1 | Vendor-specific (page `0xFF00`) | Kernel driver detached for commands |
| 2 | Mouse + Feature Report `0x5A` | Never touched |
| **3** | **Vendor-specific (page `0xFF01`)** | **✅ RGB control channel** |
| 4 | Digitizer/touchpad | Never touched |

The tool sends an 8-byte USB HID Feature Report via control transfer on **Interface 3** (`wIndex=3`):

```
[0x08, 0x00, program, speed, brightness, colour, 0x01, checksum]
```

| Byte | Purpose | Values |
|---|---|---|
| 0 | Instruction | Always `0x08` |
| 1 | Padding | Always `0x00` |
| 2 | Program | `0x01` = static (use this; others are buggy) |
| 3 | Speed | `0x01` (fastest) to `0x0A` (slowest) |
| 4 | Brightness | `0x00` (off) to `0x64` (max); hue is non-linear in this byte for some colour bytes — see [Colours](#colours) |
| 5 | Colour | `0x01`–`0x07` colour family; the visible hue depends on byte 4 as well (see [Colours](#colours)) |
| 6 | Padding | Always `0x01` |
| 7 | Checksum | `(255 - sum(bytes 0-6)) & 0xFF` |

The tool is based on Paul Ridgway's reverse engineering of the Gigabyte Aero W15 keyboard protocol (see [Acknowledgements](#acknowledgements)).

## Effect codes (advanced / experimental)

These are available via the CLI but **not exposed in the tray menu** because some of them can leave the keyboard in an unresponsive state. Use at your own risk.

| Code | Effect | Notes |
|---|---|---|
| `0x01` | Static | ✅ Safe — the default |
| `0x02` | Breathing | Tested, may hang |
| `0x03` | Wave | Tested, may hang |
| `0x04` | Fade on keypress | Tested, may hang |
| `0x05` | Marquee | Untested |
| `0x06` | Ripple | Untested |
| `0x07` | Flash on keypress | Untested |
| `0x08` | Neon | Tested, may hang |
| `0x09` | Rainbow Marquee | Tested, rainbow colour forced |
| `0x0A` | Raindrop | Untested |
| `0x0B` | Circle Marquee | Untested |
| `0x0C` | Hedge | Untested |
| `0x0D` | Rotate | Untested |
| `0x33`–`0x37` | Custom 1–5 | Per-key layouts, protocol documented |

## Project structure

```
gigabyte-keyboard-rgb/
├── src/gigabyte_keyboard_rgb/
│   ├── __init__.py         # Package metadata
│   ├── protocol.py         # USB protocol: constants, command building, send
│   ├── cli.py              # CLI interface (gigabyte-rgb command)
│   ├── config.py           # JSON config persistence
│   └── tray.py             # AppIndicator3 tray app (gigabyte-rgb-tray)
├── data/
│   ├── 99-gigabyte-keyboard-rgb.rules   # udev rule
│   ├── gigabyte-keyboard-rgb.service    # systemd user unit
│   ├── gigabyte-keyboard-rgb.svg        # tray icon
│   └── gigabyte-keyboard-rgb-tray.desktop
├── install.sh              # Cross-distro installer
├── uninstall.sh            # Uninstaller
├── tests/test_protocol.py  # Checksum tests
├── pyproject.toml          # PEP 621 build metadata
├── LICENSE                 # MIT license
├── README.md               # This file
└── .gitignore
```

## Acknowledgements

This project stands on the shoulders of several pioneering reverse-engineering efforts:

- **[Paul Ridgway](https://blockdev.io/gigabyte-aero-w15-keyboard-and-linux-ubuntu/)** — Original reverse engineering of the 8-byte USB HID protocol on the Gigabyte Aero W15 (January 2019). His [blockdev.io article](https://blockdev.io/gigabyte-aero-w15-keyboard-and-linux-ubuntu/) documented the command format, colour/effect tables, and custom layout protocol that this tool is built on.
- **[GitHub: paul-ridgway/aero-keyboard](https://github.com/paul-ridgway/aero-keyboard)** — Ruby implementation of the protocol.
- **[GitHub: yurikhan/aero-keyboard-rgb](https://github.com/yurikhan/aero-keyboard-rgb)** — Python port of Paul's work, which guided our Python implementation approach.
- **[Martin Koppehel](https://github.com/b4ckspace/aero-rgb-linux)** — Early libusb-based C utility for Aero keyboards.
- **[Nesh108/MyAorusKeyboardSDK](https://github.com/Nesh108/MyAorusKeyboardSDK)** — Windows SDK for the AORUS keyboard that helped validate the protocol structure.
- **[PyUSB](https://pyusb.github.io/pyusb/)** — The Python USB library that makes sending control transfers to the keyboard possible.
- **Linux kernel** — USB HID subsystem, sysfs HID descriptors, and udev device management.
- **The Opensource community** — Countless forum posts, GitHub issues, and wiki pages on USB HID reverse engineering that guided our approach.

**Note on ACPI/WMI:** We also investigated the EC/WMI interface (port `0x72`/`0x73`, WMI GUIDs `ABBC0F6F`, `ABBC0F75`) as a potential RGB control path. While the DSDT tables reference keyboard backlight registers (`KBLL` at EC offset `0x31`), these proved to control a simple brightness level (0–100) rather than per-key RGB, and the WMI methods returned fixed 72-byte responses that did not correspond to RGB commands. We document this dead end so future contributors do not waste time investigating it.

## License

MIT License — see [LICENSE](LICENSE).

## Contributing

Contributions are welcome! Areas that need the most help:

- **Testing on more hardware** — If you have a Gigabyte Aero or AORUS laptop running Linux, please test and report!
- **Per-key custom layouts** — The protocol supports sending per-key RGB data (documented in Paul's article). Implementing a custom layout editor or loading presets would be a great addition.
- **Effects safety** — Investigate which effect codes are safe and which cause firmware hangs.
- **Packaging** — Help packaging for more distributions (Flatpak, Fedora COPR, Arch AUR, etc.).
- **Translation** — Internationalise the tray menu labels.

## Uninstalling

```sh
./uninstall.sh
```

Or manually: `pip uninstall gigabyte-keyboard-rgb` + remove the udev rule and systemd unit.
