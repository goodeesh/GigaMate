# Experimental Models

GigaMate only exposes power-profile controls for models it can attach a profile
to. For models with strong **community evidence** but no in-house confirmation,
controls ship as **Experimental**: visible, but disabled until the user opts in
once (`gigamate profile experimental on`, the tray menu, or GigaMate Center).

Sensors (temperature, fan RPM, fan duty) are **not** gated — they work on every
machine where the `gigamate_acpi` module loads. Only profile *switching* is
model-gated.

## Shipped as Experimental

| Model / DMI match | Profiles | Evidence |
|---|---|---|
| **GIGABYTE GAMING** family — A16 (GA6H gen: CMH/CVH/CTH/CWH/GA6H; 2026 gens; PRO; AMD 3xH/5xH) and A18 | Eco / Balanced / Boost (`0xED` 0–2) | Linux WMI driver validated **A16 CWH**; Windows `FanControl.GigabyteWMI` confirmed **A16 CVH**; user report on **A16 CMH**. Family-gated by the driver on `product_family == "GIGABYTE GAMING"`. 2026 gens presumed same platform. |
| **GIGABYTE AERO X16** family (all variants) | Quiet / Balanced / Performance / Gaming (`0xED` 0–3) | In-house reverse-engineering verified on **EG61VH** (`product_name` `GIGABYTE AERO X16 1VH`); the siblings share the platform. |

The DA6H-generation evidence is triple-sourced:

- `JoseLopez36/gigabyte-laptop-wmi-A16` (Linux kernel driver): `perf_mode 0xED`
  values 0–2 = eco/balanced/boost (GPU TGP); `dynamic_boost 0xE7`; `fan_turbo
  0x7D`; 16-step fan duty table; fan RPM little-endian for the `GIGABYTE GAMING`
  family.
- `justin-mecham/FanControl.GigabyteWMI` (Windows plugin): confirmed **CVH**
  (BIOS FB05) on the same WMI device (`GB_WMIACPI` = AMW0), with a duty table
  extracted from Gigabyte's `ComData.dll` that is **byte-identical** to the
  Linux driver's (`{57, 68, 80, …, 229}` = `{0x39, 0x44, 0x50, …, 0xE5}`).
- Our own user report (issue #19, A16 CMH): module loads; temp/fan RPM/duty
  sensors work; fan RPM sane; GPU power limit 55→70 W under load (matches the
  driver's boost ≈70 W, max 80 W). The `0xED` *profile semantics* were not
  independently confirmed on the CMH, hence **Experimental**, not Verified.

## Blocked (known, not shipped)

These product families use a **different EC command set** — the older fan-mode
WMBD codes (`0x57` silent, `0x71` gaming, `0x67` custom, `0x70` auto, `0x6A`
fixed, `0x6B` custom speed) — not `0xED`. Shipping `0xED` profiles for them
would be knowingly dead controls.

| Model / family | Why blocked | Route to support |
|---|---|---|
| AORUS 2025+ (MASTER/ELITE AM6H/AE6H/…) | `AORUS` product family → older command set | Implement per-family fan-mode commands in `gigamate_acpi` |
| Older Aero / AORUS (pre-2025) | Same — fan-mode command set | Same as above |

## Not supported

| Model / family | Why |
|---|---|
| Sabre, U-series | Not AMW0; different EC, no compatible interface |
| GIGABYTE GAMING ≤ 2024 (G5/G7/A5/A7) | Rebadged Clevo (no AMW0) — see `wessel-novacustom/clevo-keyboard` |

These never reach profile UI: the module only binds when an AMW0 device exists,
and the UI requires a working ACPI backend.

## Promotion protocol (Experimental → Verified)

1. A user on an experimental model runs `gigamate profile report` and pastes the
   output in a comment on <https://github.com/goodeesh/GigaMate/issues>.
2. If the report (or a follow-up observation) shows switching changes fan RPM or
   GPU power, the maintainer flips the profile's `acpi.confidence` to
   `"verified"` (per-variant or per-family).
3. Users keep working; the family leaves the experimental tier.

## Adding a candidate

A model/family can be added as a candidate here (documented, not shipped) when a
credible external source documents a compatible command set. To ship it:
create a `dmi_<slug>.json` profile (see `docs/PROFILE_SCHEMA.md`) with
`acpi.confidence: "experimental"` and the documented profile set.
