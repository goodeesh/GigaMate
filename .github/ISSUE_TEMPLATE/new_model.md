---
name: Hardware Compatibility / Discovery Report
about: Share automatic discovery diagnostics for a Gigabyte or AORUS laptop
title: 'Hardware report: [Your Model Name]'
labels: new-model

---

> **Note:** GigaMate 5.0.0 uses **zero-profile automatic discovery** (`WMBD(0xED)` AML + HID LampArray `Usage Page 0x59`). Pre-2025 laptops using the legacy `0x08` RGB protocol without LampArray should use the final `v4.5.3` release instead.

Please paste the full terminal output below so we can inspect how your firmware & HID descriptors behave:

**Laptop model & BIOS version:**

**Output of `gigamate doctor`:**
```
```

**Output of `gigamate profile report`:**
```
```

**Output of `gigamate gpu power probe` (if laptop has a discrete GPU):**
```
```
