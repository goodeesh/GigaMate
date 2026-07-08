---
name: New model support
about: Add a device profile for your Gigabyte laptop
title: 'Add support for [Your Model Name]'
labels: new-model

---

## Device information

- **Model name:**
- **USB VID:PID:**
- **ACPI available:** yes / no (run `gigamate detect --acpi` to check)

## Changes

- [ ] Added `src/gigamate/profile_data/{VID}_{PID}.json`

## Checklist

- [ ] I have run `gigamate calibrate rgb` — keyboard colours are mapped
- [ ] I have run `gigamate detect --acpi` — ACPI capabilities are probed (if available)
- [ ] I have tested: `gigamate status` shows correct sensor readings
- [ ] I have tested: `gigamate profile gaming` switches power profile (if ACPI)
- [ ] Profile JSON passes validation (`python -m pytest tests/`)

## Additional notes
