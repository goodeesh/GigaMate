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

USB-keyed profiles are named `{VID}_{PID}.json` (4-digit uppercase hex USB ids).

```
0414_8105.json   → VID=0x0414, PID=0x8105
1044_7A43.json   → VID=0x1044, PID=0x7A43
```

DMI-keyed profiles (no Gigabyte USB keyboard) are named `dmi_<slug>.json`:

```
dmi_GIGABYTE_GAMING.json
dmi_gigabyte_gaming_a16_cmh.json
```

---

## Schema overview

```json
{
  "version": 3,
  "name": "Full Model Name",
  "vid": "0x0414",
  "pid": "0x8105",
  "dmi": { "product_names": ["GIGABYTE GAMING A16 CMH"], "product_name_prefixes": [], "product_families": ["GIGABYTE GAMING"] },
  "interfaces": [1, 3],
  "control_interface": 3,
  "colour_map": { ... },
  "acpi": { ... }
}
```

A profile needs **either** a USB key (`vid`+`pid`) **or** a DMI key (`dmi`),
never neither. DMI-keyed profiles are for laptops without a Gigabyte USB
keyboard (e.g. GIGABYTE GAMING A16) and are named `dmi_<slug>.json`.

---

## Top-level fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `version` | int | optional (default 1) | Schema version. v3 adds DMI keys and `acpi.confidence`. |
| `name` | string | yes | Human-readable model name, e.g. `"Gigabyte Aero X16 (EG61VH)"` |
| `vid` | string | see note | USB Vendor ID as hex string, e.g. `"0x0414"` |
| `pid` | string | see note | USB Product ID as hex string, e.g. `"0x8105"` |
| `dmi` | object | see note | DMI key for ACPI-only laptops (below). Required when `vid`/`pid` are absent. |
| `interfaces` | array of int | yes | USB interfaces to detach for RGB control, e.g. `[1, 3]` |
| `control_interface` | int | yes | USB interface for ctrl_transfer, typically `3` |
| `colour_map` | object | yes | Keyboard RGB colour definitions (empty for ACPI-only profiles) |
| `acpi` | object | no | ACPI/fan/power profile capabilities |
| `hotkeys` | object | no | Hardware hotkey definitions (e.g. mode/performance switch, vendor key) |

---

## DMI section (ACPI-only models)

```json
"dmi": {
  "product_names": ["GIGABYTE GAMING A16 CMH"],
  "product_name_prefixes": ["GIGABYTE AERO X16"],
  "product_families": ["GIGABYTE GAMING"]
}
```

| Field | Type | Description |
|-------|------|-------------|
| `product_names` | array of string | Exact `/sys/class/dmi/id/product_name` matches |
| `product_name_prefixes` | array of string | `product_name` startswith matches (covers all variants of a line) |
| `product_families` | array of string | Exact `product_family` matches (broadest; used last) |

Matching (see `profiles.match_dmi_profile`) is **specificity-first**: exact
name / prefix beats family. All DMI matching additionally requires the DMI
vendor to be Gigabyte and the chassis to be a laptop; profile switching also
requires a working ACPI backend. RGB profiles must use a USB key.

**Resolution precedence** (`profiles.resolve_model`): user USB → built-in USB →
user DMI → built-in DMI. The first entry that *declares* a profile set wins; a
USB-keyed profile with no `acpi` never suppresses a later DMI profile's
profiles.

### `acpi.confidence`

`"verified"` (default) or `"experimental"`. Experimental profiles are for
community-evidenced, unconfirmed models: their controls are shown but disabled
until the user opts in (`experimental_profiles_enabled`). An experimental
profile must declare `has_power_profiles` and a non-empty `profiles` set. See
[docs/EXPERIMENTAL_MODELS.md](EXPERIMENTAL_MODELS.md).

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
| `has_power_profiles` | bool | `false` | Whether power profile switching (WMBD 0xED) works on **this model**. This is an authoritative per-model contract: GigaMate only exposes profile controls (tray menu, Center, `gigamate profile`, hotkey cycle, boot/resume re-apply) when a matching profile declares `true` **and** provides a non-empty `profiles` map. Backend detection alone (e.g. the AMW0 WMI interface answering sensor reads) never enables the UI, because different Gigabyte EC generations implement different commands and an EC can accept a profile write without acting on it. |
| `fan_count` | int | `0` | Number of fans (1 or 2 typically) |
| `fan_labels` | array of string | `[]` | Human-readable fan names, e.g. `["CPU Fan", "GPU Fan"]` |
| `sensor_labels` | object | `{}` | Friendly names for temperature sensors |
| `profiles` | object | `{}` | Available power profiles (see below) |
| `backend` | string | `"module"` | Preferred ACPI backend: `"module"` or `"acpi_call"` |
| `confidence` | string | `"verified"` | `"verified"` (confirmed on real hardware) or `"experimental"` (community-evidenced, opt-in; requires `has_power_profiles` + `profiles`) |

### profiles sub-fields

Each key is a string profile ID (`"0"` through `"3"`) mapping to:

| Field | Type | Description |
|-------|------|-------------|
| `name` | string | Display name, e.g. `"Quiet"`, `"Gaming"` |
| `desc` | string | Optional description, e.g. `"Maximum GPU power"` |

---

## Hotkeys section (optional)

The `hotkeys` section defines hardware hotkeys (such as the Mode performance
switch and the GigaMate vendor key) emitted via vendor HID reports. The Mode
key sits on the keycap printed as `F7`: pressed alone it emits the vendor
mode-switch report, and `Fn` + the key produces a regular F7.

```json
"hotkeys": {
  "mode_switch": {
    "interface": 2,
    "report_id": 4,
    "payload": "000084",
    "key_name": "Mode"
  },
  "open_center": {
    "interface": 2,
    "report_id": 4,
    "payload": "000091",
    "key_name": "GigaMate"
  }
}
```

### hotkeys sub-fields

| Field | Type | Description |
|-------|------|-------------|
| `interface` | int | USB interface number emitting the report (typically 2) |
| `report_id` | int | HID report ID (e.g. 4) |
| `payload` | string | Expected byte payload in hex (e.g. `"000084"`) |
| `key_name` | string | Human-readable key name (e.g. `"Mode"`, `"GigaMate"`). Do **not** use the function-row label (e.g. `"F7"`): bare press emits the vendor report, and `Fn` + the key produces the function key. |

Hotkeys are strictly model-scoped: the daemon only watches reports defined by
the profile matching the detected keyboard (falling back to the built-in
profile for the same VID:PID) or by explicit `hotkey_overrides` in
`config.json`. Unknown models are never affected. Capture unknown button
signatures with `gigamate hotkeys watch` (see [Contributing](#contributing-a-profile))
and submit them as part of a profile.

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

### ACPI-only profile (DMI-keyed, no Gigabyte USB keyboard)

```json
{
  "name": "Gigabyte GAMING A16 CMH",
  "version": 3,
  "dmi": {
    "product_names": ["GIGABYTE GAMING A16 CMH"]
  },
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
    "sensor_labels": {
      "temp_cpu": "CPU Temp",
      "temp_socket": "Socket Temp"
    },
    "profiles": {
      "0": {"name": "Eco", "desc": "Lowest GPU power"},
      "1": {"name": "Balanced", "desc": "Default GPU power"},
      "2": {"name": "Boost", "desc": "Max GPU TGP"}
    },
    "backend": "module"
  }
}
```

(DMI-keyed profiles are stored as `dmi_<slug>.json`. `gigamate calibrate acpi`
generates one automatically when no Gigabyte USB keyboard is detected.)

---

## Creating a profile

### Automatic (recommended)

1. **Keyboard RGB:** `gigamate calibrate rgb`
   - Interactive session (~5 min) that sends colour samples and asks you to name them
   - Saves to `~/.config/gigamate/profiles/{VID}_{PID}.json`

2. **ACPI capabilities:** `gigamate calibrate acpi`
   - Probes the ACPI interface and generates/updates your model profile
   - Sensors (temperature, fan RPM/duty) are detected automatically and saved
   - `has_power_profiles` is **never auto-detected**: it stays `false` unless you
     explicitly confirm the experimental profile set, because profile semantics
     vary per EC generation and cannot be proven from software. When the DMI
     product family matches a documented layout (Aero/AORUS → 4 fan-curve
     profiles; `GIGABYTE GAMING` → eco/balanced/boost GPU-TGP modes) those names
     are offered as a starting template, clearly labelled unverified.
   - Enabling profile support this way only unlocks the controls for *your*
     local profile; it stays hidden for everyone else until the profile is
     shipped as a built-in (see Contributing).

3. **Combined:** `gigamate calibrate all`
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

### Capturing hardware button signatures

Vendor keys emit raw HID reports. Capture them:

```sh
gigamate hotkeys watch      # press the buttons, Ctrl-C to stop
gigamate hotkeys list       # show the configured mappings
```

Take the `iface`, `report_id` and `payload` bytes from the capture, add them
to the profile's `hotkeys` section — or to `hotkey_overrides` in
`~/.config/gigamate/config.json` to test a mapping without a release — and
validate as above.

---

## Contributing a profile

Once your profile is ready:

```sh
# Print step-by-step PR instructions
gigamate profile contribute
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
