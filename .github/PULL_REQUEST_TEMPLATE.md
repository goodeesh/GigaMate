## Summary

<!-- Describe your changes cleanly and concisely. -->

## Related Issue / Motivation

<!-- Link any related issue or explain why this change is needed. -->

## Hardware Tested (if applicable)

- **Laptop model:**
- **Distro & Desktop / Compositor:**
- **Kernel (`uname -r`):**

## Checklist

- [ ] `QT_QPA_PLATFORM=offscreen pytest -q` passes with zero failures
- [ ] `bash -n install.sh uninstall.sh` passes (if installer/packaging scripts were modified)
- [ ] Relevant documentation updated (`README.md`, `docs/`)
- [ ] Changes stay focused and scoped without adding hardcoded per-laptop JSON profiles
