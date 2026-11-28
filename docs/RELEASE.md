# GigaMate Release & Packaging Runbook

Maintainer runbook for building, verifying, signing, and publishing GigaMate native packages (Arch/Pacman, AUR, Debian/Ubuntu APT, Fedora DNF, openSUSE Zypper) and GitHub Releases.

---

## 1. One-Time Setup

### 1.1 Dedicated GPG Release Signing Key

Generate a dedicated RSA-4096 (or Ed25519/RSA) GPG key used exclusively to sign packages and repository metadata (`APT`, `RPM`, and `Pacman`):

```sh
gpg --full-generate-key
```

Recommended parameters:
- **Key type:** RSA and RSA (`4096` bits) — widely supported by `rpm`/`rpmsign`, `apt`, and `pacman-key`
- **Expiration:** e.g. `2y` (can be extended later without changing the fingerprint)
- **Real name:** `GigaMate Release Signing Key`
- **Email:** maintainer release email (e.g. `goodeesh@users.noreply.github.com`)
- **Passphrase:** strong passphrase (store in your password manager)

Export the ASCII-armored private key and add it to **GitHub Repository Settings → Secrets and variables → Actions**:

```sh
# Find the key fingerprint
gpg --list-secret-keys --keyid-format LONG "GigaMate Release Signing Key"

# Export the armored private key
gpg --armor --export-secret-keys <KEY_FPR>
```

Configure these GitHub Actions secrets:
- `GPG_PRIVATE_KEY`: full output of `gpg --armor --export-secret-keys <KEY_FPR>`
- `GPG_PASSPHRASE`: passphrase protecting the signing key (leave unset only if the key has no passphrase)

> [!NOTE]
> If `GPG_PRIVATE_KEY` is not configured, the `publish-repos` job safely skips GitHub Pages deployment while `publish-release` still uploads packages and checksums to GitHub Releases.

### 1.2 GitHub Pages (`gh-pages` branch)

1. Go to **GitHub Repository Settings → Pages**.
2. Set **Source** to **Deploy from a branch**.
3. Select branch **`gh-pages`** and folder **`/ (root)`**, then save.
4. Once published, repositories are served at `https://goodeesh.github.io/GigaMate/repo/` (with `gigamate.asc`, `gigamate.gpg`, `apt/`, `rpm/fedora/`, `rpm/opensuse/`, and `arch/any/`).

### 1.3 Optional: AUR Auto-Publish SSH Key

To automatically push updated `PKGBUILD`, `.SRCINFO`, and `gigamate.install` to AUR on stable releases:

1. Generate a dedicated Ed25519 key pair (no passphrase):
   ```sh
   ssh-keygen -t ed25519 -C "gigamate-release-bot" -f ~/.ssh/id_gigamate_aur -N ""
   ```
2. Add the public key (`~/.ssh/id_gigamate_aur.pub`) to your AUR account at **https://aur.archlinux.org/account/**.
3. Add the private key (`~/.ssh/id_gigamate_aur`) to GitHub Repository Secrets as **`AUR_SSH_PRIVATE_KEY`**.
4. *(First publish only)* Ensure the `gigamate` package base exists on `ssh://aur@aur.archlinux.org/gigamate.git`.

> [!NOTE]
> If `AUR_SSH_PRIVATE_KEY` is unset (or if the tag is a prerelease), the `publish-aur` job skips automatically without failing the workflow.

---

## 2. Dry-Run Testing Before Releasing

### 2.1 Local Arch / CachyOS Build & Migration Check

Build `gigamate` and `gigamate-dkms` locally from your working tree into `dist/arch/`:

```sh
./packaging/build-local-arch.sh
```

To live-test installation (or migration from `install.sh`) on CachyOS/Arch:

```sh
# Optional pre-test config snapshot (migration never deletes ~/.config/gigamate)
cp -a ~/.config/gigamate ~/.config/gigamate.pre-pkg-backup

# Install both split packages locally
sudo pacman -U dist/arch/gigamate-dkms-*.pkg.tar.zst dist/arch/gigamate-[0-9]*.pkg.tar.zst

# Verify DKMS, CLI, user services, and doctor diagnostics
which gigamate
dkms status
systemctl --user status gigamate.service gigamate-dgpu.service
gigamate doctor
```

### 2.2 Full CI Dry-Run (`workflow_dispatch`)

Before pushing a release tag, run the full build + 4-distro container verification matrix (`ubuntu`, `fedora`, `opensuse`, `arch`) without publishing anything:

- **GitHub UI:** Go to **Actions → Release → Run workflow**, keep **`dry_run: true`** checked (and optionally specify a `version` override), and click **Run workflow**.
- **GitHub CLI:**
  ```sh
  gh workflow run release.yml -f dry_run=true
  ```

This executes `build-packages` and `verify-packages` (running `./packaging/test-containers.sh` across all 4 distros) while skipping `publish-release`, `publish-repos`, and `publish-aur`.

---

## 3. Cutting a Release

1. **Bump version** in [`src/gigamate/__init__.py`](../src/gigamate/__init__.py) (`__version__ = "X.Y.Z"`), and update any release notes / `README.md` version references if needed.
2. **Run local tests** to confirm a clean tree:
   ```sh
   pytest -q
   ```
3. **Commit, tag, and push:**
   ```sh
   git add src/gigamate/__init__.py
   git commit -m "chore(release): bump version to vX.Y.Z"
   git tag -a vX.Y.Z -m "GigaMate vX.Y.Z"
   git push origin main vX.Y.Z
   ```

### What CI Does Automatically on `v*` Tags

1. **`build-packages`**: Builds `.deb` (Debian/Ubuntu), `.rpm` (Fedora & openSUSE), `.pkg.tar.zst` (Arch), AUR `PKGBUILD`/`.SRCINFO`, `install.sh.sha256`, `gigamate.tar.gz.sha256`, and `SHA256SUMS`.
2. **`verify-packages`**: Runs `./packaging/test-containers.sh` in clean containers for `ubuntu`, `fedora`, `opensuse`, and `arch` (install, migration check, CLI smoke test, and uninstall).
3. **`publish-release`**: Creates or updates the GitHub Release for `vX.Y.Z` and uploads all packages and checksums. Tags containing `-a`, `-b`, `-rc`, `alpha`, or `beta` are automatically marked as **prereleases**.
4. **`publish-repos`** *(stable tags only, requires `GPG_PRIVATE_KEY`)*: Signs all packages and repository metadata via `./packaging/publish-repos.sh` and deploys the static tree to the `gh-pages` branch.
5. **`publish-aur`** *(stable tags only, requires `AUR_SSH_PRIVATE_KEY`)*: Pushes the generated `PKGBUILD`, `.SRCINFO`, and `packaging/arch/gigamate.install` to `ssh://aur@aur.archlinux.org/gigamate.git`.

---

## 4. Abort / Rollback Runbook

If anything fails or looks wrong during local verification, a prerelease, or a release cut:

### 4.1 Rollback Local CachyOS / Arch System (< 1 minute)

```sh
# 1. Remove the native packages (pacman removes /usr files and DKMS hook removes module)
sudo pacman -Rns gigamate gigamate-dkms

# 2. Switch back to a known-good branch or tag and re-run install.sh
git checkout main
./install.sh

# 3. Restore config backup only if needed (install.sh already preserves ~/.config/gigamate)
# cp -a ~/.config/gigamate.pre-pkg-backup/. ~/.config/gigamate/
```

### 4.2 Rollback Git / CI / Published Release State

1. **Unmerged packaging branch:**
   ```sh
   git branch -D <release-branch>
   ```
2. **Bad or test prerelease tag & GitHub Release:**
   ```sh
   # Remove remote and local tag
   git push --delete origin vX.Y.Z
   git tag -d vX.Y.Z

   # Delete the GitHub Release (if already created)
   gh release delete vX.Y.Z --yes
   ```
3. **Revert `gh-pages` repository state (if `publish-repos` already deployed):**
   - Revert `gh-pages` to the previous known-good commit and force-push:
     ```sh
     git fetch origin gh-pages
     git checkout -B gh-pages origin/gh-pages
     git reset --hard HEAD~1
     git push --force-with-lease origin gh-pages
     git checkout main
     ```
   - Or, if no prior stable repo existed yet, disable GitHub Pages temporarily in **Settings → Pages** and ship via `install.sh` with zero user impact.
