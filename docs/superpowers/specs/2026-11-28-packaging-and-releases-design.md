# GigaMate — Native Packaging, Daemon Layout & Multi-Distro Release Pipeline

**Date:** 2026-11-28  
**Status:** Draft for Review (repo untouched while parallel branch work is in progress)  
**Target:** GigaMate v5.0+  
**Canonical Repo Target (when ready to commit):** `docs/superpowers/specs/2026-11-28-packaging-and-releases-design.md`

---

## 1. Goals & Scope

Transition GigaMate from a `curl | bash` installer (`install.sh`) into a first-class Linux product distributed via signed native package repositories while keeping `install.sh` as a fallback for unsupported distributions and local development.

**In Scope:**
1. Split native packages (`gigamate` + `gigamate-dkms`) following standard Linux FHS paths.
2. Clean, minimal daemon and privilege architecture (2 systemd user services, 3 single-shot Polkit helpers, consolidated Polkit rules, vendor udev rules, no root daemon).
3. Package-aware in-app update notifications (`updates.py`) and 100% transparent migration from legacy `install.sh` installations without losing `~/.config/gigamate/`.
4. Automated GitHub Actions CI/CD release pipeline building, GPG-signing, and publishing APT (Debian/Ubuntu/Pop!_OS/Mint), DNF (Fedora), Zypper (openSUSE), Pacman (Arch/CachyOS) repositories on GitHub Pages, plus automated AUR publishing.
5. Containerized multi-distro verification tests and live CachyOS verification before public release.

**Out of Scope (Future Follow-ups):**
- Upstream PR to `CachyOS/CachyOS-PKGBUILDS` or Chaotic-AUR (follow-up once v5.0 is stable and on AUR).
- Fedora COPR or Ubuntu Launchpad PPA mirrors (can be added later without changing package structure).
- Flatpak/Snap (unsuitable due to DKMS kernel module, Polkit helpers, and udev/PCI hardware access).

---

## 2. Package Split & FHS Filesystem Layout

### 2.1 Two-Package Architecture

Every supported distribution ships two packages built from the same repository tag:

| Package | Architecture | Responsibility |
|---|---|---|
| `gigamate-dkms` | `all` / `noarch` / `any` | Kernel driver source (`gigamate_acpi`) + DKMS config + module autoload |
| `gigamate` | `all` / `noarch` / `any` | Python app, CLI/Tray/Center/Watcher, systemd user units, Polkit policies/rules, udev rules, helpers, desktop entries, icons |

- `gigamate` declares a dependency on `gigamate-dkms` (Recommends on Debian/Fedora if optional UI-only installs are ever desired; Depends by default so `install gigamate` delivers the complete experience out of the box).
- Splitting `gigamate-dkms` allows app-only updates without forcing a kernel module rebuild when the driver version has not changed.

### 2.2 Standard FHS Paths

Native packages never write to `$HOME` or overwrite admin-managed files in `/etc`.

| Component | Legacy (`install.sh`) | Native Package Path | Owner / Mode |
|---|---|---|---|
| CLI & UI launchers (`gigamate`, `gigamate-tray`, `gigamate-center`, `gigamate-dgpu-watch`) | `~/.local/bin/` | `/usr/bin/` | `root:root 0755` |
| Python package (`gigamate/`) | pip user-site / venv | `/usr/lib/gigamate/py/gigamate/` | `root:root 0644` |
| Install-method marker | *(none)* | `/usr/lib/gigamate/install-method` | `root:root 0644` |
| Privileged helpers (`gigamate-dgpu-gate`, `gigamate-dgpu-nvml`, `gigamate-repair`) | `/usr/lib/gigamate/` | `/usr/lib/gigamate/` | `root:root 0755` |
| Systemd user units (`gigamate.service`, `gigamate-dgpu.service`) | `~/.config/systemd/user/` | `/usr/lib/systemd/user/` | `root:root 0644` |
| Systemd user preset (`90-gigamate.preset`) | *(none)* | `/usr/lib/systemd/user-preset/` | `root:root 0644` |
| udev rules (`70-gigamate.rules`, `99-gigamate-dgpu-ac.rules`, `99-gigamate-smartshift.rules`) | `/etc/udev/rules.d/` | `/usr/lib/udev/rules.d/` | `root:root 0644` |
| Polkit actions (`org.gigamate.{dgpu,dgpu-gate,repair}.policy`) | `/usr/share/polkit-1/actions/` | `/usr/share/polkit-1/actions/` | `root:root 0644` |
| Consolidated Polkit rule (`50-gigamate.rules`) | 4 files in `/etc/polkit-1/rules.d/` | `/usr/share/polkit-1/rules.d/50-gigamate.rules` | `root:root 0644` |
| Desktop entries (`gigamate.desktop`, `gigamate-center.desktop`) | `~/.local/share/applications/` | `/usr/share/applications/` | `root:root 0644` |
| Icons (`data/*.svg`, including `checkbox-checked.svg`) | `~/.local/share/icons/hicolor/scalable/apps/` | `/usr/share/icons/hicolor/scalable/apps/` | `root:root 0644` |
| DKMS source (`gigamate-dkms`) | `/usr/src/gigamate_acpi-<ver>/` | `/usr/src/gigamate_acpi-<ver>/` | `root:root 0644` |
| Kernel module autoload (`gigamate-dkms`) | `/etc/modules-load.d/gigamate_acpi.conf` | `/usr/lib/modules-load.d/gigamate_acpi.conf` | `root:root 0644` |

### 2.3 Python Version Independence (`/usr/lib/gigamate/py/`)

Installing into `/usr/lib/gigamate/py/gigamate/` with thin `/usr/bin/gigamate*` Python launchers:

```python
#!/usr/bin/python3
import sys
sys.path.insert(0, "/usr/lib/gigamate/py")
from gigamate.cli import main
raise SystemExit(main())
```

**Why this matters:**
- Zero breakage when a distro upgrades Python (`3.11 → 3.12 → 3.13 → 3.14`), including rolling-release **Arch / CachyOS** prebuilt `.pkg.tar.zst` packages.
- Still uses the distribution's native system `python3`, `PyQt6`, and `evdev` packages — zero bundled wheels, zero virtualenvs, zero `pip`.

### 2.4 Per-Distro Dependencies

| Distro | `gigamate` Depends | `gigamate-dkms` Depends |
|---|---|---|
| **Debian / Ubuntu / Pop!_OS / Mint** | `python3 (>= 3.10)`, `python3-pyqt6`, `python3-evdev`, `polkitd \| policykit-1`, `gigamate-dkms` | `dkms`, `make`, `gcc \| clang` |
| **Fedora / RHEL-family** | `python3 >= 3.10`, `python3-pyqt6`, `python3-evdev`, `polkit`, `gigamate-dkms` | `dkms`, `kernel-devel`, `make`, `gcc` |
| **openSUSE (Leap / Tumbleweed)** | `python3 >= 3.10`, `(python3-qt6 or python3-PyQt6)`, `python3-evdev`, `polkit`, `gigamate-dkms` | `dkms`, `kernel-default-devel`, `make`, `gcc` |
| **Arch / CachyOS** | `python`, `python-pyqt6`, `python-evdev`, `polkit`, `gigamate-dkms` | `dkms` *(Arch's built-in `dkms` pacman hook handles builds for `linux`, `linux-lts`, `linux-cachyos`, etc.)* |

---

## 3. Daemon, Service & Privilege Architecture

### 3.1 Background Services (2 User Services, Zero Root Daemon)

GigaMate keeps its two lightweight **systemd user services** and intentionally avoids introducing a long-running root daemon:

1. **`/usr/lib/systemd/user/gigamate.service`**:
   - `ExecStart=/usr/bin/gigamate-tray`
   - Hosts `GigaMateDaemon` (power profiles, fan monitoring, battery care, RGB, idle dim, hotkeys, sleep hooks) + PyQt6 system tray.
2. **`/usr/lib/systemd/user/gigamate-dgpu.service`**:
   - `ExecStart=/usr/bin/gigamate-dgpu-watch`
   - Isolated from `gigamate.service` so NVML or dGPU wake/gate operations can never disrupt the tray, fan control, or RGB.
   - **iGPU-only safety:** `gigamate-dgpu-watch` checks for a discrete NVIDIA/AMD GPU (or `/run/gigamate-dgpu-gate.json`) on startup and exits cleanly with exit code `0` when none is present. With `Restart=on-failure`, systemd will not restart-loop it on iGPU-only laptops.
3. **Global Auto-Start (`/usr/lib/systemd/user-preset/90-gigamate.preset`)**:
   ```ini
   enable gigamate.service
   enable gigamate-dgpu.service
   ```
   Package post-install runs `systemctl --global enable gigamate.service gigamate-dgpu.service` so all desktop users get automatic startup on login without the package writing into `/home/*`.

### 3.2 Privileged Operations (Single-Shot Polkit Helpers)

All root operations remain strictly scoped to the 3 existing audited single-shot helpers invoked via `pkexec`:
- `/usr/lib/gigamate/gigamate-dgpu-gate` (`org.gigamate.dgpu-gate.control`)
- `/usr/lib/gigamate/gigamate-dgpu-nvml` (`org.gigamate.dgpu.control`)
- `/usr/lib/gigamate/gigamate-repair` (`org.gigamate.repair.driver`)

**Consolidation:**
- Merge the 4 legacy Polkit rule files (`50-gigamate-dgpu.rules`, `50-gigamate-dgpu-gate.rules`, `50-gigamate-repair.rules`, `50-gigamate-powerd.rules`) into a single vendor rule `/usr/share/polkit-1/rules.d/50-gigamate.rules` permitting `wheel` / `sudo` group members to invoke those 3 actions + `nvidia-powerd.service` management.
- Keep `70-gigamate.rules` (`TAG+="uaccess"`), `99-gigamate-dgpu-ac.rules`, and `99-gigamate-smartshift.rules` in `/usr/lib/udev/rules.d/`.
- Add a DMI vendor match (`ATTR{[dmi/id]sys_vendor}=="*GIGABYTE*"` / `*AORUS*` / `*GIGA-BYTE*`) directly inside `99-gigamate-smartshift.rules` so it only triggers on Gigabyte laptops without requiring install-time shell detection.

### 3.3 DKMS Kernel Driver (`gigamate-dkms`)

- **Arch / CachyOS:** Ships `/usr/src/gigamate_acpi-<ver>/` and `/usr/lib/modules-load.d/gigamate_acpi.conf`. Arch's built-in `dkms` pacman hook automatically builds and installs `gigamate_acpi` on package install and on every kernel upgrade (`linux-cachyos`, `linux-zen`, `linux-lts`, `linux`), including CachyOS's clang/LLVM kernels.
- **Debian / Ubuntu / Fedora / openSUSE:** Package `postinst` registers and builds via `dkms add` + `dkms install` (with `CC=clang LLVM=1` fallback if default gcc build fails on a clang-built kernel) + `modprobe gigamate_acpi`; `prerm` cleans up via `dkms remove gigamate_acpi/<ver> --all`.

---

## 4. Package-Aware Updater & Transparent Migration

### 4.1 Install-Method Detection (`src/gigamate/updates.py`)

Add `detect_install_method() -> str`:
1. Read `/usr/lib/gigamate/install-method` if present → returns one of `"apt"`, `"dnf"`, `"zypper"`, `"pacman"`, `"aur"`.
2. Fallback (if marker missing): check `shutil.which("gigamate")` ownership via `pacman -Qo`, `dpkg -S`, or `rpm -qf`.
3. Otherwise returns `"script"`.

**Updater Behavior:**
- **All install methods:** Retain the battery-efficient 24h GitHub Releases check so the tray's orange update badge notifies users when a new release is published.
- **When `"script"`:** Existing checksum-verified `install.sh --update` flow (unchanged).
- **When package-managed (`apt`/`dnf`/`zypper`/`pacman`/`aur`):**
  - Disable `install.sh` self-execution (never overwrite or shadow package-owned files).
  - Clicking **Update available** opens a clean dialog showing the version diff and exact native upgrade command (with **Copy** and **Run in Terminal** buttons):
    - `apt`: `sudo apt update && sudo apt upgrade gigamate gigamate-dkms`
    - `dnf`: `sudo dnf upgrade gigamate gigamate-dkms`
    - `zypper`: `sudo zypper update gigamate gigamate-dkms`
    - `pacman`: `sudo pacman -Syu gigamate gigamate-dkms`
    - `aur`: `paru -Syu gigamate gigamate-dkms` (falling back to `yay` if `paru` is absent)

### 4.2 Transparent Automatic Migration (`install.sh` → Native Package)

Users upgrading from `install.sh` run their distro's package install command and do **nothing else**:

1. **System-level cleanup (automatic in package `postinst`):**
   - Checks for legacy `install.sh` files in `/etc/udev/rules.d/` (`70-gigamate.rules`, `99-gigamate-dgpu-ac.rules`, `99-gigamate-smartshift.rules`), `/etc/polkit-1/rules.d/` (`50-gigamate-dgpu.rules`, `50-gigamate-dgpu-gate.rules`, `50-gigamate-repair.rules`, `50-gigamate-powerd.rules`), and `/etc/modules-load.d/gigamate_acpi.conf`.
   - Removes each file only if it contains GigaMate's header signature (or moves unrecognized modified files to `*.pre-pkg.bak` so custom admin edits are never lost).
   - Reloads udev rules (`udevadm control --reload-rules && udevadm trigger --subsystem-match=hidraw --subsystem-match=usb`) and enables global systemd user presets.
2. **Immediate user-level cleanup for `$SUDO_USER` (in `postinst`):**
   - If `$SUDO_USER` is set and non-root, runs a guarded cleanup as `$SUDO_USER` removing legacy `~/.local/bin/gigamate*` (only if pip entry points importing `gigamate`), `~/.config/systemd/user/gigamate*.service` (only if pointing to `.local/bin`), and `~/.local/share/` desktop/icon copies, then runs `systemctl --user daemon-reload && systemctl --user restart gigamate.service`.
   - **`~/.config/gigamate/` is never touched** — all user profiles, RGB, undervolt, and battery settings are preserved.
3. **Self-healing user-level cleanup on first startup (`migrate_legacy_user_install()`):**
   - When packaged `gigamate-tray` or `gigamate` starts for any other user account on the system, it runs the same guarded, idempotent cleanup once and sets `"migrated_to_pkg": true` in `~/.config/gigamate/update_state.json`.
4. **Diagnostic safety net (`gigamate doctor`):**
   - Reports any remaining shadowed binaries or `/etc/` overrides if detected.
5. **Fallback installer (`install.sh` & `uninstall.sh`):**
   - Kept in the repository for unsupported distros and local development; `uninstall.sh` gains `--keep-config` for manual cleanup if ever needed.

---

## 5. CI/CD Release Pipeline, GPG Signing & Repository Structure

### 5.1 Repository Layout in Git

```text
packaging/
├── stage.sh                        # Builds canonical FHS trees for gigamate & gigamate-dkms
├── scripts/
│   ├── postinst-app.sh             # Shared Debian/RPM post-install (legacy cleanup + global preset)
│   ├── postrm-app.sh               # Shared Debian/RPM post-remove (udev/systemd reload)
│   ├── postinst-dkms.sh            # Debian/RPM DKMS add/install + modprobe
│   └── prerm-dkms.sh               # Debian/RPM DKMS remove --all
├── nfpm/
│   ├── gigamate-deb.yaml
│   ├── gigamate-dkms-deb.yaml
│   ├── gigamate-fedora.yaml
│   ├── gigamate-dkms-fedora.yaml
│   ├── gigamate-opensuse.yaml
│   └── gigamate-dkms-opensuse.yaml
└── arch/
    ├── PKGBUILD.tmpl               # Split package (gigamate + gigamate-dkms)
    └── gigamate.install            # Pacman post_install / post_upgrade / post_remove hooks
```

### 5.2 GitHub Pages Repository Structure (`gh-pages` branch)

Published at `https://goodeesh.github.io/GigaMate/repo/`:

```text
repo/
├── gigamate.asc                    # ASCII-armored GPG public key
├── gigamate.gpg                    # Binary GPG public key (for APT signed-by)
├── apt/
│   ├── dists/stable/
│   │   ├── InRelease               # Inline-signed Release
│   │   ├── Release
│   │   ├── Release.gpg             # Detached GPG signature
│   │   └── main/binary-all/{Packages,Packages.gz}
│   └── pool/main/{gigamate_<ver>_all.deb, gigamate-dkms_<ver>_all.deb}
├── rpm/
│   ├── fedora/
│   │   ├── repodata/{repomd.xml, repomd.xml.asc, ...}
│   │   └── {gigamate-<ver>.fedora.noarch.rpm, gigamate-dkms-<ver>.fedora.noarch.rpm}
│   └── opensuse/
│       ├── repodata/{repomd.xml, repomd.xml.asc, ...}
│       └── {gigamate-<ver>.opensuse.noarch.rpm, gigamate-dkms-<ver>.opensuse.noarch.rpm}
└── arch/
    └── any/
        ├── gigamate.db.tar.gz (+ .sig)
        ├── gigamate.files.tar.gz (+ .sig)
        └── {gigamate-<ver>-1-any.pkg.tar.zst (+ .sig), gigamate-dkms-<ver>-1-any.pkg.tar.zst (+ .sig)}
```

### 5.3 GitHub Actions Workflow (`.github/workflows/release.yml`)

Triggered on `push` of tags matching `v*`:

1. **Job `build-packages` (ubuntu-latest):**
   - Runs `packaging/stage.sh <version>`.
   - Builds `.deb` (Debian/Ubuntu) and `.rpm` (Fedora, openSUSE) packages using `nFPM`.
   - Builds Arch `.pkg.tar.zst` + `.SRCINFO` inside an `archlinux:base-devel` container using `makepkg`.
   - Generates `.sha256` checksums for all artifacts (including legacy `install.sh` + source tarball).
2. **Job `verify-packages` (matrix: `ubuntu:24.04`, `fedora:41`, `opensuse/tumbleweed`, `archlinux:latest`):**
   - Installs the newly built packages inside clean distro containers from a local test repo.
   - Verifies GPG signature validation, FHS file placement, `gigamate --help`, `gigamate version`, Polkit/udev/systemd unit syntax, and clean package removal.
   - **Gates publishing:** if any distro container test fails, nothing is published to GitHub Pages or AUR.
3. **Job `publish-release`:**
   - Uploads all `.deb`, `.rpm`, `.pkg.tar.zst`, and `.sha256` assets to the GitHub Release.
4. **Job `publish-repos` (stable tags only, or separate `testing` channel for prereleases):**
   - Imports `GPG_PRIVATE_KEY` (with `GPG_PASSPHRASE` from GitHub Secrets).
   - Signs RPMs (`rpmsign`), generates APT repo (`dpkg-scanpackages` + `apt-ftparchive` + `gpg --clearsign`), RPM repos (`createrepo_c` + `gpg --detach-sign`), and Arch repo (`repo-add --sign`).
   - Deploys `repo/` to GitHub Pages (`gh-pages` branch).
5. **Job `publish-aur` (stable tags only):**
   - Pushes updated `PKGBUILD` + `.SRCINFO` to `ssh://aur@aur.archlinux.org/gigamate.git` using `AUR_SSH_PRIVATE_KEY` (skipped cleanly if secret not yet configured).

### 5.4 One-Time Maintainer Setup Required

1. Generate a dedicated GPG signing key (`GigaMate Release Signing Key`) and add `GPG_PRIVATE_KEY` + `GPG_PASSPHRASE` to GitHub Repository Secrets.
2. Enable GitHub Pages from the `gh-pages` branch in repository settings.
3. *(Optional for AUR)* Register an AUR account, add SSH public key to AUR, and store `AUR_SSH_PRIVATE_KEY` in GitHub Secrets.

---

## 6. Verification & Rollout Plan

Before announcing native repos to users:
1. **Unit tests:** Tests for `detect_install_method()`, package-aware update dialog commands, `migrate_legacy_user_install()` (including custom-file preservation), and `gigamate-dgpu-watch` clean exit `0` on iGPU-only systems.
2. **Container end-to-end tests:** Run the 4-distro container suite (`ubuntu:24.04`, `fedora:41`, `opensuse/tumbleweed`, `archlinux:latest`) verifying GPG-signed repo installation, migration simulation, and clean uninstall.
3. **Live CachyOS verification:** Build `.pkg.tar.zst` locally on the maintainer's CachyOS laptop, install over the existing `install.sh` setup, verify transparent migration, DKMS build against `linux-cachyos` (clang/LLVM), tray + Center + dGPU watcher + Polkit helpers.
4. **Prerelease dry-run:** Tag a prerelease (e.g. `v5.0.0-rc1`) to exercise the full GitHub Actions → GitHub Pages pipeline end-to-end before publishing `v5.0.0` stable.

---

## 7. Branching Strategy, Local CachyOS Test Run & Abort / Rollback Plan

### 7.1 Branch Isolation
- All packaging, daemon/updater, and CI work lives on a dedicated branch **`feat/native-packaging`** created from **`feat/dgpu-power-gate`** (after the parallel agent session finishes its work on `feat/dgpu-power-gate`).
- `feat/dgpu-power-gate` and `main` remain untouched until the full local CachyOS test run and multi-distro container suite pass.
- No public GitHub Pages repo, GPG-signed release, or AUR upload happens until `feat/native-packaging` is merged and explicitly tagged.

### 7.2 Local CachyOS Proof-of-Concept Run (Before Merging or Publishing)
1. **Pre-test snapshot:** Back up `~/.config/gigamate/` to `~/.config/gigamate.pre-pkg-backup/` (even though migration never touches it) and record current `gigamate version`, `dkms status`, `systemctl --user status gigamate gigamate-dgpu`, and `which -a gigamate`.
2. **Local build:** Run `packaging/stage.sh` and `makepkg -sf` in `packaging/arch/` to produce local `gigamate-<ver>-1-any.pkg.tar.zst` and `gigamate-dkms-<ver>-1-any.pkg.tar.zst` directly on CachyOS.
3. **Live migration test:** Install both packages locally via `sudo pacman -U ./gigamate-dkms-*.pkg.tar.zst ./gigamate-*.pkg.tar.zst` right over the existing `install.sh` installation.
4. **Verification checklist:**
   - Legacy `~/.local/bin/gigamate*`, `~/.config/systemd/user/gigamate*.service`, `/etc/udev/rules.d/*gigamate*`, `/etc/polkit-1/rules.d/50-gigamate-*.rules`, and `/etc/modules-load.d/gigamate_acpi.conf` are automatically cleaned up.
   - `~/.config/gigamate/` is 100% intact (profiles, RGB, battery care, undervolt, dGPU gate config).
   - `which gigamate` resolves to `/usr/bin/gigamate`; `gigamate version` and `gigamate doctor` pass with zero warnings.
   - `dkms status` shows `gigamate_acpi` built & installed for the running CachyOS kernel (`lsmod | grep gigamate_acpi`).
   - `systemctl --user status gigamate.service gigamate-dgpu.service` shows both loaded from `/usr/lib/systemd/user/` and active.
   - Tray icon, GigaMate Center UI, fan/power profile switching, keyboard RGB, dGPU power-gate/undervolt, and Polkit helpers all work without unexpected password prompts.
   - `updates.py` detects `"pacman"` install method and shows native `pacman -Syu` instructions instead of `install.sh`.

### 7.3 Abort / Rollback Plan (Zero-Risk Return to Current Setup)

If anything fails or feels too risky at **any** stage (during implementation, container tests, or live CachyOS migration):

1. **Rollback local CachyOS system (< 1 minute):**
   ```bash
   # 1. Remove the native packages (pacman cleanly removes all /usr files & DKMS hook removes module)
   sudo pacman -Rns gigamate gigamate-dkms

   # 2. Switch back to feat/dgpu-power-gate and re-run the current install.sh
   git checkout feat/dgpu-power-gate
   ./install.sh

   # 3. Restore config backup only if needed (install.sh already preserves ~/.config/gigamate)
   # cp -a ~/.config/gigamate.pre-pkg-backup/. ~/.config/gigamate/
   ```
2. **Rollback Git / CI state:**
   - Delete the unmerged `feat/native-packaging` branch (`git branch -D feat/native-packaging`) — `feat/dgpu-power-gate` and `main` remain 100% clean.
   - If a test prerelease tag was pushed, delete the prerelease tag (`git push --delete origin v5.0.0-rc1`), delete the draft GitHub Release, and disable/clear `gh-pages`.
   - Ship v5.0.0 with `install.sh` as before — zero user impact.

