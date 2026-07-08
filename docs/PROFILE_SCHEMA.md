# GigaMate Device Profile Schema v2

> Each Gigabyte laptop model is described by a single JSON profile that
> defines its keyboard RGB and ACPI capabilities. Profiles live in
> `src/gigamate/profile_data/` (built-in) or
> `~/.config/gigamate/profiles/` (user, override built-in).

---

## Table of Contents

1. [File naming](#file-naming)
2. [Schema overview](#schema-overview)
3. [Top-level fields](#top-level-fields)
4. [Keyboard RGB section](#keyboard-rgb-section)
5. [ACPI section (optional)](#acpi-section-optional)
6. [Examples](#examples)
7. [Creating a profile](#creating-a-profile)
8. [Contributing a profile](#contributing-a-profile)

---

## File naming

Profiles are named `{VID}_{PID}.json` where VID and PID are 4-digit
hexadecimal USB identifiers (uppercase).

```
0414_8105.json   → VID=0x0414, PID=0x8105
1044_7A43.json   → VID=0x1044, PID=0x7A43
```

---

## Schema overview

```json
{
  "version": 2,
  "name": "Full Model Name",
  "vid": "0x0414",
  "pid": "0x8105",
  "interfaces": [1, 3],
  "control_interface": 3,
  "colour_map": { ... },
  "acpi": { ... }
}
```

---

## Top-level fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `version` | int | optional (default 1) | Schema version. v2 adds ACPI support. |
| `name` | string | yes | Human-readable model name, e.g. `"Gigabyte Aero X16 (EG61VH)"` |
| `vid` | string | yes | USB Vendor ID as hex string, e.g. `"0x0414"` |
| `pid` | string | yes | USB Product ID as hex string, e.g. `"0x8105"` |
| `interfaces` | array of int | yes | USB interfaces to detach for RGB control, e.g. `[1, 3]` |
| `control_interface` | int | yes | USB interface for ctrl_transfer, typically `3` |
| `colour_map` | object | yes | Keyboard RGB colour definitions |
| `acpi` | object | no | ACPI/fan/power profile capabilities |

---

## Keyboard RGB section

The `colour_map` maps colour names to their byte-level representation
at each brightness level.

```json
"colour_map": {
  "red": {
    "0": [1, 0],
    "1": [1, 25],
    "2": [1, 100]
  },
  "purple": {
    "0": [6, 0],
    "1": [6, 25],
    "2": [6, 50]
  }
}
```

### colour_map sub-fields

| Key | Type | Description |
|-----|------|-------------|
| Colour name | string | Lowercase with underscores, e.g. `"light_purple"`, `"blush_pink"` |
| `"0"` | `[byte5, byte4]` | Off (brightness = 0) — typically `[byte5, 0]` |
| `"1"` | `[byte5, byte4]` | Dim brightness level |
| `"2"` | `[byte5, byte4]` | Full brightness level |

Each entry is a `[byte5, byte4]` pair encoding the USB HID command:

```
[0x08, 0x00, program, speed, byte4, byte5, 0x01, checksum]
```

- `byte5` = colour family (0x01–0x07)
- `byte4` = brightness/intensity (0x00–0x64)

> **Note:** The colour appearance is non-linear — the same `byte5` with
> different `byte4` values may produce different hues. The profile captures
> empirically determined pairs that look correct.

---

## ACPI section (optional)

The `acpi` section defines what ACPI/WMI features the laptop supports
via its AMW0 WMI device.

```json
"acpi": {
  "has_fan_control": true,
  "has_temperature": true,
  "has_power_profiles": true,
  "fan_count": 2,
  "fan_labels": ["CPU Fan", "GPU Fan"],
  "sensor_labels": {
    "temp_cpu": "CPU Temp",
    "temp_socket": "Socket Temp"
  },
  "profiles": {
    "0": {"name": "Quiet", "desc": "Low fan noise, capped GPU power"},
    "1": {"name": "Balanced", "desc": "Balanced performance and noise"},
    "2": {"name": "Performance", "desc": "High performance, more fan noise"},
    "3": {"name": "Gaming", "desc": "Maximum GPU power"}
  },
  "backend": "module"
}
```

### acpi sub-fields

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `has_fan_control` | bool | `false` | Whether fan RPM/duty monitoring is available |
| `has_temperature` | bool | `false` | Whether CPU/socket temperature sensors are available |
| `has_power_profiles` | bool | `false` | Whether power profile switching (WMBD 0xED) works |
| `fan_count` | int | `0` | Number of fans (1 or 2 typically) |
| `fan_labels` | array of string | `[]` | Human-readable fan names, e.g. `["CPU Fan", "GPU Fan"]` |
| `sensor_labels` | object | `{}` | Friendly names for temperature sensors |
| `profiles` | object | `{}` | Available power profiles (see below) |
| `backend` | string | `"module"` | Preferred ACPI backend: `"module"` or `"acpi_call"` |

### profiles sub-fields

Each key is a string profile ID (`"0"` through `"3"`) mapping to:

| Field | Type | Description |
|-------|------|-------------|
| `name` | string | Display name, e.g. `"Quiet"`, `"Gaming"` |
| `desc` | string | Optional description, e.g. `"Maximum GPU power"` |

---

## Examples

### Minimal RGB-only profile (v1 backward compat)

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

### Full RGB + ACPI profile

See the [built-in Aero X16 profile](../src/gigamate/profile_data/0414_8105.json)
for a complete example with all 11 colours and full ACPI section.

### ACPI-only profile (for laptops with non-Gigabyte USB keyboard)

```json
{
  "name": "Gigabyte Aero 17 (Custom Keyboard)",
  "vid": "0x0414",
  "pid": "0x9999",
  "interfaces": [],
  "control_interface": 0,
  "colour_map": {},
  "acpi": {
    "has_fan_control": true,
    "has_temperature": true,
    "has_power_profiles": true,
    "fan_count": 2,
    "fan_labels": ["CPU Fan", "GPU Fan"],
    "sensor_labels": {
      "temp_cpu": "CPU Temp",
      "temp_socket": "PCH Temp"
    },
    "profiles": {
      "0": {"name": "Quiet", "desc": "Low noise"},
      "1": {"name": "Balanced", "desc": "Default"},
      "2": {"name": "Performance", "desc": "High performance"},
      "3": {"name": "Gaming", "desc": "Max GPU"}
    },
    "backend": "module"
  }
}
```

---

## Creating a profile

### Automatic (recommended)

1. **Keyboard RGB:** `gigamate calibrate rgb`
   - Interactive session (~5 min) that sends colour samples and asks you to name them
   - Saves to `~/.config/gigamate/profiles/{VID}_{PID}.json`

2. **ACPI capabilities:** `gigamate detect --acpi`
   - Automatically probes all ACPI commands and detects what works
   - Appends the `acpi` section to your existing profile

3. **Combined:** `gigamate calibrate --all`
   - Runs both steps above in sequence

### Manual

Create a JSON file following the schema above. Validate it:

```python
from gigamate.profiles import DeviceProfile, validate_profile
profile = DeviceProfile.from_dict(json.load(open("my_profile.json")))
errors = validate_profile(profile)
if errors:
    print("Validation errors:", errors)
else:
    print("Profile is valid!")
```

---

## Contributing a profile

Once your profile is ready:

```sh
# Print step-by-step PR instructions
gigamate profile contribute

# Or auto-create a Pull Request (requires gh CLI)
gigamate profile contribute --pr
```

The profile will be added to `src/gigamate/profile_data/` and shipped
with the next release.

---

## Schema validation

Profiles are validated automatically in CI:

```yaml
# .github/workflows/tests.yml validates all built-in profiles
- run: python -c "
    from gigamate.profiles import load_builtin_profiles, validate_profile
    for pid, profile in load_builtin_profiles().items():
        errors = validate_profile(profile)
        assert not errors, f'{pid}: {errors}'
    print('All profiles valid')
    "
```
