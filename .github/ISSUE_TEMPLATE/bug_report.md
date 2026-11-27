---
name: Bug report
about: Report a bug, regression, or hardware discovery issue in GigaMate
title: "[BUG] "
labels: bug
assignees: ''
---

## Describe the bug

<!-- A clear description of what went wrong. -->

## To reproduce

Steps to reproduce the behaviour:
1. Command or UI action:
2. Expected behaviour:
3. Actual behaviour:

## Environment

- Laptop model & BIOS version:
- CPU / GPU:
- Distro & Desktop / Compositor (e.g. KDE Wayland, GNOME Wayland, X11):
- Kernel (`uname -r`):
- GigaMate version (`gigamate version`):

## Diagnostics (`gigamate doctor` & `gigamate profile report`)

```
<!-- Paste the full output of:
  gigamate doctor
  gigamate profile report
-->
```

## Service logs (if related to tray, idle sleep, hotkeys, or dGPU watcher)

```
<!-- Paste recent logs from:
  journalctl --user -u gigamate.service -u gigamate-dgpu.service -n 80 --no-pager
-->
```
