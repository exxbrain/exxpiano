# Building the reusable SD card image

The shipped artifacts are two complete bootable images, each for a clean SD card: one for the Raspberry Pi 2/3/4 (ARMv8, built from `DietPi_RPi234-ARMv8-Trixie.img.xz`) and one for the Raspberry Pi 5 (built from `DietPi_RPi5-ARMv8-Trixie.img.xz`). Each contains DietPi, exxpiano, the full piano and the mechanics bank; both also carry the smaller `lite` piano (picked by itself on a 512 MB board; on a Pi 5 it is one more bank to choose). One ZIP can be flashed to many cards.

## Prerequisites

Use Docker Desktop on macOS or Docker on Linux, the official `DietPi_RPi234-ARMv8-Trixie.img.xz`, and prepare the banks:

```bash
scripts/make-soundfonts.sh -p full -o build
scripts/make-soundfonts.sh -p lite -o build
```

The builder defaults to `build/piano-full.sf2` and `build/mech.sf2`. Use `--piano` and `--mech` for controlled replacements.

**The target.** `--target rpi234` (the default) or `--target rpi5`. For the Pi 5 pass the Pi 5 image of DietPi (`--dietpi DietPi_RPi5-ARMv8-Trixie.img.xz`, or `--download`). The target decides the names (`exxpiano-rpi5-v...-sd.zip`, `manifest-rpi5.json`, `README-rpi5.txt`) and two things inside: the Pi 5 image does not touch the onboard sound settings, because **the Pi 5 has no 3.5 mm jack: it plays only through a USB sound card or audio interface**. The Pi 5 image of v1.10.21 was started on a Raspberry Pi 5 (8 GB) and played through a USB sound card.

Before tagging a release add a section `## <VERSION>` to `CHANGELOG.md`: the workflow appends it to the description of the GitHub Release (`scripts/release-notes.py`) and stops without it.

An image is never released without both banks. If `build/piano-lite.sf2` exists (`scripts/make-soundfonts.sh -p lite`, or `--piano-lite FILE`) it goes into the image too, and the first start uses it on a board with less than 800 MB of memory (a Pi 3 A+); the release workflow always builds it.

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
- `manifest-<target>.json` — versions, base/result SHA-256 values, banks, and the identity scheme;
- `README-<target>.txt` — brief instructions for that release.

The product version comes from `VERSION`; the DietPi version comes from `dietpi/.version` in the base image. A release is rejected when that version cannot be read, either required bank is absent, or the smoke check fails.

> **Setup network password.** Without `SD_AP_PASSWORD` the image keeps the default `exxpianosetup`, the same for every card made from it; the name of the network is unique per card (it ends with the installation ID). With `SD_AP_PASSWORD` all cards of this image share your password. Change it on one device with `WIFI_AP_PASSWORD` in `/etc/exxpiano.conf`.

## GitHub Release

Pushing a tag named `v<VERSION>` starts `.github/workflows/release.yml`. The workflow checks that the tag exactly matches `VERSION`, runs the fast tests, builds the full and lite banks, downloads the pinned DietPi RPi 2/3/4 ARMv8 base from the repository's dedicated prerelease, verifies the SHA-256 recorded in `.github/dietpi-image.env`, builds and smoke-tests the image, and then creates the product GitHub Release. This avoids depending on the upstream download service during a product release while preserving the exact official input. The product release assets are:

- `exxpiano-rpi234-v<VERSION>-dietpi-<DIETPI_VERSION>-sd.zip`;
- `manifest-rpi234.json`, `manifest-rpi5.json`;
- `README-rpi234.txt`, `README-rpi5.txt`;
- `SHA256SUMS`.

The release title names exxpiano, its version, and the supported Raspberry Pi family. Its English description explains the image contents, flashing and first boot, checksum verification, and why an already-booted card must not be cloned.

No release is created if any preceding step fails. For an existing release the title and description are updated and missing assets are added; an asset whose name already exists is never overwritten (the upload fails and the operator decides).

### Download mirror

The images are close to 1 GB, and GitHub can be slow to download them from. The release workflow therefore also copies the release files to a public Cloudflare R2 bucket (`scripts/mirror-to-r2.sh`, into `<bucket>/<tag>/`) and adds a **Faster download (mirror)** section with links to the ZIP files to the release description. R2 does not charge for downloads, and 10 GB of storage is free. Set it up once:

1. In Cloudflare create an R2 bucket and make it public: connect a custom domain to it (recommended) or turn on its `r2.dev` address (Cloudflare limits its rate).
2. Create an R2 API token with **Object Read & Write** on that bucket.
3. In the repository settings add the secrets `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET` and the variable (not a secret) `R2_PUBLIC_URL`, the bucket's public address without a trailing slash.

Without `R2_PUBLIC_URL` the step is skipped. A failed copy does not stop the release: it is published without the mirror links, and the copy can be repeated. For a release published before the mirror existed, or whose copy failed, run the workflow **release-mirror** from the **Actions** tab with its tag: it downloads the release files, checks them against `SHA256SUMS`, copies them to the bucket and adds the mirror links to the published description, leaving the rest of it as it is.

The DietPi build-input prerelease is not a product release. Its tag includes the first eight characters of the verified upstream SHA-256, and its only binary asset is the unchanged official image named by `.github/dietpi-image.env`. Updating DietPi requires a new prerelease/tag and checksum; an existing pinned input is never replaced. Local `--download` remains available and identifies the builder with an HTTP User-Agent accepted by the upstream service.

The Pi 5 image has a pinned copy of its own (`DIETPI_RPI5_*` in `.github/dietpi-image.env`, tag `dietpi-rpi5-armv8-trixie-<8 characters>`). Create it once from the **Actions** tab: run the workflow **dietpi-mirror** with the target `rpi5` (it downloads the official image, checks it against the pin, and makes the pre-release; it stops if the pre-release already exists). Until the pre-release exists, a release downloads the official Pi 5 image and checks it against the checksum DietPi publishes next to it.

Ordinary pushes and pull requests run only the fast tests. The complete stand-in image pipeline runs separately when image builder, DietPi setup, packaging, or their workflow changes; it can also be started manually from GitHub Actions.

## Flashing and first boot

1. In Raspberry Pi Imager choose “Choose OS” → “Use custom” and select the ZIP, or select it in balenaEtcher.
2. Flash a clean SD card, put it in a Raspberry Pi 2, 3 or 4 (the `rpi5` ZIP: in a Pi 5 with a USB sound card), and power it on.
3. First boot takes several minutes and needs no internet, Ethernet, or saved Wi-Fi.
4. The installation creates a persistent random 6-character hexadecimal ID in `/var/lib/exxpiano/installation-id`.
5. The ID produces a unique `exxpiano-Setup-<ID>` network and `exxpiano-<lowercase-id>` hostname. Join the setup network and open `http://10.42.0.1:8080/`; the web UI shows the exact ID, network name, and QR codes.

Cards cloned from the same ZIP receive different IDs on their own first boot. ID creation is atomic and the stored value is reused, so rebooting does not change Wi-Fi or hostname.

## Verification

For ordinary changes run the fast tests:

```bash
python3 -m unittest tests.test_build_sd_image tests.test_wifilib tests.test_exxpiano_wifi tests.test_web tests.test_demo_web
```

Run the full Docker pipeline and image build only when the pipeline itself changes or before a release; GitHub Actions applies the same split automatically. A container smoke test is not a substitute for real Raspberry Pi 2, 3, 4 and 5 checks. On hardware verify `/var/log/exxpiano-firstboot.log`, the unique setup network, web UI, MIDI, sound, and an offline reboot.
