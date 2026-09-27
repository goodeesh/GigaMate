# Contributing to GigaMate

The most valuable contribution you can make is adding support for your
Gigabyte laptop model. The calibration tools do all the work — just
run them and submit the result.

---

## Adding a New Model

### Step 1: Install

```sh
curl -sSL https://raw.githubusercontent.com/goodeesh/GigaMate/main/install.sh | bash
```

### Step 2: Calibrate keyboard RGB

```sh
gigamate calibrate rgb
```

This sends colour samples to your keyboard and asks you to name them.
It saves a profile to `~/.config/gigamate/profiles/`.

### Step 3: Probe ACPI

```sh
gigamate detect --acpi
```

This detects temperature sensors, fan RPM, duty cycles, and power profiles.

### Step 4: Test

```sh
gigamate status                   # Verify readings
gigamate profile gaming           # Test profile switching
```

### Step 5: Contribute via Pull Request

```sh
gigamate profile contribute
```

Prints instructions to fork the repo, add your profile file, and open a PR.

**Prefer not to use git?** Open a
[GitHub issue](https://github.com/goodeesh/GigaMate/issues/new)
with your profile JSON attached.

---

## Pull Request Guidelines

- If your PR adds a new model:
  - Include the profile JSON in `src/gigamate/profile_data/`
  - Include `lsusb` output as evidence
- Keep one model per PR

---

## Profile JSON Schema

Profiles define keyboard RGB and optional ACPI capabilities.

### Minimal RGB-only

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
  "version": 3,
  "vid": "0x0414",
  "pid": "0x8105",
  "interfaces": [1, 3],
  "control_interface": 3,
  "colour_map": { ... },
  "acpi": {
    "has_fan_control": true,
    "has_temperature": true,
    "has_power_profiles": true,
    "confidence": "verified",
    "fan_count": 2,
    "fan_labels": ["CPU Fan", "GPU Fan"],
    "profiles": {
      "0": {"name": "Quiet"},
      "3": {"name": "Gaming"}
    },
    "backend": "module"
  }
}
```

### ACPI-only profile (no Gigabyte USB keyboard)

Laptops without a Gigabyte USB keyboard (e.g. GIGABYTE GAMING A16) key their
profile on **DMI** and are named `dmi_<slug>.json`:

```json
{
  "name": "Gigabyte GAMING A16 CMH",
  "version": 3,
  "dmi": {"product_names": ["GIGABYTE GAMING A16 CMH"]},
  "interfaces": [],
  "control_interface": 0,
  "colour_map": {},
  "acpi": {
    "has_fan_control": true,
    "has_temperature": true,
    "has_power_profiles": true,
    "confidence": "experimental",
    "fan_count": 2,
    "fan_labels": ["CPU Fan", "GPU Fan"],
    "profiles": {
      "0": {"name": "Eco"},
      "1": {"name": "Balanced"},
      "2": {"name": "Boost"}
    },
    "backend": "module"
  }
}
```

Use `"confidence": "experimental"` for any model whose profile switching you
have **not** confirmed on real hardware — it ships as opt-in and is promoted to
`"verified"` once a user reports it works. See
[docs/EXPERIMENTAL_MODELS.md](docs/EXPERIMENTAL_MODELS.md) for the registry and
promotion protocol.

See [docs/PROFILE_SCHEMA.md](docs/PROFILE_SCHEMA.md) for the full reference.

---

## Questions?

Open an issue with the `question` label.
