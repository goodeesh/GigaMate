# Contributing to GigaMate

Thanks for your interest in contributing! GigaMate is a community-driven project
that makes Gigabyte laptop management possible on Linux. Whether you're adding
support for a new laptop model, fixing a bug, or improving documentation —
every contribution counts.

---

## Code of Conduct

Be kind, patient, and constructive. This is a hobby project maintained in
spare time. Clear communication goes a long way.

---

## Ways to Contribute

| Area | What's involved | Skill level |
|------|----------------|-------------|
| **Add a new laptop model** | Run calibration tools, submit a profile via PR | **No coding needed** |
| **Test on new hardware** | Install, run `gigamate status`, report results | No coding |
| **Report bugs** | Open an issue with steps to reproduce | No coding |
| **ACPI research** | Probe additional WMBC/WMBD commands | Intermediate (ACPI) |
| **Code improvements** | Kernel module, Python, packaging | Developer |
| **Documentation** | Typos, clarifications, translations | No coding |
| **Packaging** | AUR, COPR, Flatpak, Debian packages | Developer |

---

## Adding a New Model (No Coding Required)

The most valuable contribution you can make is adding support for your
Gigabyte laptop model. The calibration tools do all the work — you just
need to run them and submit the result.

### Step 1: Install GigaMate

```sh
curl -sSL https://raw.githubusercontent.com/goodeesh/GigaMate/main/install.sh | bash
```

Or if you prefer to review first:
```sh
git clone https://github.com/goodeesh/GigaMate.git
cd GigaMate
./install.sh
```

### Step 2: Calibrate keyboard RGB

```sh
gigamate calibrate rgb
```

This interactive session (~5 minutes) sends colour samples to your keyboard
and asks you to name what you see. It saves a profile to
`~/.config/gigamate/profiles/{VID}_{PID}.json`.

### Step 3: Probe ACPI capabilities

```sh
gigamate detect --acpi
```

This automatically probes all ACPI commands and detects:
- Temperature sensors (CPU, socket)
- Fan RPM readback
- Fan duty cycle readback
- Power profile switching (Quiet/Balanced/Performance/Gaming)

The results are appended to your device profile.

### Step 4: Test

```sh
gigamate status                   # Verify readings
gigamate profile gaming           # Test profile switching
```

### Step 5: Contribute via Pull Request

```sh
gigamate profile contribute
```

This prints step-by-step instructions to fork the repo, add your profile,
and open a Pull Request. You'll need a GitHub account and basic git knowledge.

**Prefer not to use git?** Open a
[GitHub issue](https://github.com/goodeesh/GigaMate/issues/new)
with your profile JSON attached, and a maintainer will add it.

---

## Development Setup

```sh
# Fork the repo on GitHub, then:
git clone https://github.com/<your-username>/GigaMate.git
cd GigaMate

# Create a virtual env (optional but recommended)
python -m venv .venv
source .venv/bin/activate

# Install in editable mode with test deps
pip install -e .
pip install pytest

# Run the tests
python -m pytest tests/ -v

# Test the CLI against real hardware (if you have a supported keyboard)
gigamate rgb detect
gigamate status
```

---

## Making Changes

1. **Create a branch** from `main`:
   ```sh
   git checkout -b fix/short-description
   ```

2. **Make your changes.** Keep commits focused — one logical change per commit.

3. **Add or update tests** in `tests/` if your change touches:
   - `protocol.py` — USB RGB protocol
   - `config.py` — Config persistence
   - `acpi.py` — ACPI communication
   - `profiles.py` — Profile system
   - `cli.py` — CLI commands

4. **Run tests locally:**
   ```sh
   python -m pytest tests/ -v
   ```

5. **Commit and push:**
   ```sh
   git add -A
   git commit -m "Short imperative commit message"
   git push -u origin fix/short-description
   ```

6. **Open a Pull Request:**
   ```sh
   gh pr create --repo goodeesh/GigaMate --base main
   ```
   Or use the GitHub web UI.

---

## Pull Request Guidelines

- Use the [PR template](.github/PULL_REQUEST_TEMPLATE.md) — it auto-populates
- Reference any issues your PR closes (e.g. "Closes #12")
- If your PR adds a new model:
  - Include the profile JSON in `src/gigamate/profile_data/`
  - Update the hardware table in `README.md`
  - Include `lsbusb` output as evidence
- If your change affects the ACPI layer, mention what hardware you tested on
- Keep the scope focused — one model per PR, one fix per PR

---

## Testing ACPI Without Hardware

Use the mock backend:

```sh
GIGAMATE_ACPI_MOCK=1 gigamate status
```

This returns plausible static values (65°C, 3580 RPM, Balanced profile)
for testing the Python layer without requiring a Gigabyte laptop.

---

## Kernel Module Development

The `gigamate_acpi` kernel module lives in `src/gigamate_acpi/`.

### Building

```sh
cd src/gigamate_acpi
make CC=clang LLVM=1    # Use clang if kernel was built with it
```

### Testing

```sh
# Load
sudo insmod gigamate_acpi.ko

# Verify
ls /sys/devices/platform/gigamate_acpi/
cat /sys/devices/platform/gigamate_acpi/temp1_input

# Profile switching
echo 3 | sudo tee /sys/devices/platform/gigamate_acpi/profile

# Unload
sudo rmmod gigamate_acpi
```

### Adding new ACPI commands

The module uses `acpi_wmbc_read()` and `acpi_wmbd_write()` helpers.
See `docs/research.md` for the full ACPI command reference.

1. Add a `DEVICE_ATTR_RO` for the new sensor
2. Add the `show` function that calls `acpi_wmbc_read()`
3. Add the attribute to `gigamate_acpi_dev_attrs[]` in `probe()`
4. Add corresponding read in `acpi.py` → `ModuleBackend`

---

## Profile JSON Schema (v2)

Profiles define keyboard RGB and optional ACPI capabilities.

### Minimal RGB-only profile

```json
{
  "name": "Gigabyte Aorus 15BKF",
  "vid": "0x0414",
  "pid": "0x7A43",
  "interfaces": [1, 3],
  "control_interface": 3,
  "colour_map": {
    "red":   {"0": [1, 0],   "1": [1, 25],  "2": [1, 100]},
    "green": {"0": [2, 0],   "1": [2, 25],  "2": [2, 100]}
  }
}
```

### Full profile with ACPI

```json
{
  "name": "Gigabyte Aero X16 (EG61VH)",
  "version": 2,
  "vid": "0x0414",
  "pid": "0x8105",
  "interfaces": [1, 3],
  "control_interface": 3,
  "colour_map": { ... },
  "acpi": {
    "has_fan_control": true,
    "has_temperature": true,
    "has_power_profiles": true,
    "fan_count": 2,
    "fan_labels": ["CPU Fan", "GPU Fan"],
    "profiles": {
      "0": {"name": "Quiet", "desc": "Low fan noise"},
      "3": {"name": "Gaming", "desc": "Maximum GPU power"}
    },
    "backend": "module"
  }
}
```

See [docs/PROFILE_SCHEMA.md](docs/PROFILE_SCHEMA.md) for the complete reference.

---

## Questions?

Open an issue with the `question` label.
