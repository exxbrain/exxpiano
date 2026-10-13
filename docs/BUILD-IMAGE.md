# Building the reusable SD card image

The shipped artifact is a complete bootable image for a clean Raspberry Pi 2/3/4 ARMv8 SD card. It already contains DietPi, exxpiano, the standard piano and the mechanics bank. One ZIP can be flashed to many cards.

## Prerequisites

Use Docker Desktop on macOS or Docker on Linux, the official `DietPi_RPi234-ARMv8-Trixie.img.xz`, and prepare the banks:

```bash
scripts/make-soundfonts.sh -p full -o build
scripts/make-soundfonts.sh -p lite -o build
```

The builder defaults to `build/piano-full.sf2` and `build/mech.sf2`. Use `--piano` and `--mech` for controlled replacements. An image is never released without both banks. If `build/piano-lite.sf2` exists (`scripts/make-soundfonts.sh -p lite`, or `--piano-lite FILE`) it goes into the image too, and the first start uses it on a board with less than 800 MB of memory (a Pi 3 A+); the release workflow always builds it.

## Build

Pass passwords through the environment so they do not appear in process arguments:

```bash
printf 'DietPi password: '; read -r -s SD_PASSWORD; printf '\n'
printf 'Setup network password: '; read -r -s SD_AP_PASSWORD; printf '\n'
export SD_PASSWORD SD_AP_PASSWORD

python3 scripts/build-sd-image.py \
  --dietpi ~/Downloads/DietPi_RPi234-ARMv8-Trixie.img.xz \
  --country RU --timezone Europe/Moscow --dry-run

python3 scripts/build-sd-image.py \
  --dietpi ~/Downloads/DietPi_RPi234-ARMv8-Trixie.img.xz \
  --country RU --timezone Europe/Moscow

unset SD_PASSWORD SD_AP_PASSWORD
```

`--dry-run` validates inputs and prints the plan. The full run writes to `dist/sd/`:

- `exxpiano-rpi234-v<VERSION>-dietpi-<DIETPI_VERSION>-sd.zip` — the flashable image;
- `manifest.json` — versions, base/result SHA-256 values, banks, and the identity scheme;
- `README.txt` — brief instructions for that release.

The product version comes from `VERSION`; the DietPi version comes from `dietpi/.version` in the base image. A release is rejected when that version cannot be read, either required bank is absent, or the smoke check fails.

> **Setup network password.** Without `SD_AP_PASSWORD` the image keeps the default `exxpianosetup`, the same for every card made from it; the name of the network is unique per card (it ends with the installation ID). With `SD_AP_PASSWORD` all cards of this image share your password. Change it on one device with `WIFI_AP_PASSWORD` in `/etc/exxpiano.conf`.

## GitHub Release

Pushing a tag named `v<VERSION>` starts `.github/workflows/release.yml`. The workflow checks that the tag exactly matches `VERSION`, runs the fast tests, builds the full and the lite banks, downloads the pinned DietPi RPi 2/3/4 ARMv8 base from the repository's dedicated prerelease, verifies the SHA-256 recorded in `.github/dietpi-image.env`, builds and smoke-tests the image, and then creates the product GitHub Release. This avoids depending on the upstream download service during a product release while preserving the exact official input. The product release assets are:

- `exxpiano-rpi234-v<VERSION>-dietpi-<DIETPI_VERSION>-sd.zip`;
- `manifest.json`;
- `README.txt`;
- `SHA256SUMS`.

The release title names exxpiano, its version, and the supported Raspberry Pi family. Its English description explains the image contents, flashing and first boot, checksum verification, and why an already-booted card must not be cloned.

No release is created if any preceding step fails. For an existing release the title and description are updated and missing assets are added; an asset whose name already exists is never overwritten (the upload fails and the operator decides).

The DietPi build-input prerelease is not a product release. Its tag includes the first eight characters of the verified upstream SHA-256, and its only binary asset is the unchanged official image named by `.github/dietpi-image.env`. Updating DietPi requires a new prerelease/tag and checksum; an existing pinned input is never replaced. Local `--download` remains available and identifies the builder with an HTTP User-Agent accepted by the upstream service.

Ordinary pushes and pull requests run only the fast tests. The complete stand-in image pipeline runs separately when image builder, DietPi setup, packaging, or their workflow changes; it can also be started manually from GitHub Actions.

## Flashing and first boot

1. In Raspberry Pi Imager choose “Choose OS” → “Use custom” and select the ZIP, or select it in balenaEtcher.
2. Flash a clean SD card, put it in a Raspberry Pi 2, 3 or 4, and power it on.
3. First boot takes several minutes and needs no internet, Ethernet, or saved Wi-Fi.
4. The installation creates a persistent random 16-character hexadecimal ID in `/var/lib/exxpiano/installation-id`.
5. The ID produces a unique `exxpiano-Setup-<ID>` network and `exxpiano-<lowercase-id>` hostname. Join the setup network and open `http://10.42.0.1:8080/`; the web UI shows the exact ID, network name, and QR codes.

Cards cloned from the same ZIP receive different IDs on their own first boot. ID creation is atomic and the stored value is reused, so rebooting does not change Wi-Fi or hostname.

## Verification

For ordinary changes run the fast tests:

```bash
python3 -m unittest tests.test_build_sd_image tests.test_wifilib tests.test_exxpiano_wifi tests.test_web tests.test_demo_web
```

Run the full Docker pipeline and image build only when the pipeline itself changes or before a release; GitHub Actions applies the same split automatically. A container smoke test is not a substitute for real Raspberry Pi 2, 3, and 4 checks. On hardware verify `/var/log/exxpiano-firstboot.log`, the unique setup network, web UI, MIDI, sound, and an offline reboot.
