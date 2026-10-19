# Building the reusable SD card image

The shipped artifact is one complete bootable image for a clean SD card that starts a **Raspberry Pi 3, 4 or 5** (64-bit). It contains Alpine Linux 3.24 (sys mode on ext4, OpenRC), the exxpiano packages, the full piano and the mechanics bank, and the smaller `lite` piano for 512 MB boards. One ZIP can be flashed to many cards. The Raspberry Pi 2 is not supported.

The image is not made from a ready-made image of a distribution: the builder installs Alpine's packages into an empty root file system, from the branch and the HTTPS mirror pinned in `alpine/repositories`, and `apk` checks the signature of every index and package with the official keys in `alpine/keys/` (their SHA-256 are in `.github/alpine-image.env`). A package that does not match stops the build. A stable branch of Alpine replaces packages with fixed builds and keeps no old versions, so two builds on different days can differ; the manifest of each image lists every package with its exact version.

## Prerequisites

Docker Desktop on macOS (it runs the arm64 builder natively on Apple silicon) or Docker on an arm64 Linux machine. Prepare the banks:

```bash
scripts/make-soundfonts.sh -p full -o build
scripts/make-soundfonts.sh -p lite -o build
```

The builder defaults to `build/piano-full.sf2`, `build/piano-lite.sf2` (when it exists) and `build/mech.sf2`. Use `--piano`, `--piano-lite` and `--mech` for controlled replacements. An image is never made without the piano and the mechanics bank; the first start uses the `lite` bank on a board with less than 800 MB of memory (a Pi 3 A+).

Build the packages (they are signed; see below):

```bash
python3 scripts/build-apks.py
```

This makes `dist/apk/exxpiano/aarch64/` (`exxpiano`, and `py3-mido` and `py3-python-rtmidi`, which Alpine does not have, built unchanged from their pinned releases on PyPI) with its `APKINDEX.tar.gz`, and `dist/apk/<key>.rsa.pub`, the public key the image will trust.

**The signing key.** `EXXPIANO_APK_KEY` holds the private key (PEM) and `EXXPIANO_APK_KEY_NAME` its name (`exxpiano`). Without them the packages are signed with a development key made once in `build/apk-key/` and the builder says so: such an image is for your own devices. The release workflow takes the key from the secret `EXXPIANO_APK_KEY` and stops without it. Make the project key once:

```bash
openssl genrsa -out exxpiano.rsa 4096        # keep it out of the repository; put its contents into the secret EXXPIANO_APK_KEY
```

## Build

Pass passwords through the environment so they do not appear in process arguments:

```bash
printf 'Root password (SSH, or empty for key login only): '; read -r -s SD_PASSWORD; printf '\n'
printf 'Setup network password: '; read -r -s SD_AP_PASSWORD; printf '\n'
export SD_PASSWORD SD_AP_PASSWORD

python3 scripts/build-sd-image.py --country RU --timezone Europe/Moscow --dry-run
python3 scripts/build-sd-image.py --country RU --timezone Europe/Moscow

unset SD_PASSWORD SD_AP_PASSWORD
```

`--dry-run` validates inputs and prints the plan. `--ssh-key ~/.ssh/id_ed25519.pub` puts a public key for root into the image. The full run writes to `dist/sd/`:

- `exxpiano-rpi-v<VERSION>-alpine-<ALPINE_VERSION>-sd.zip`: the flashable image;
- `manifest.json`: the product and Alpine versions, the pinned branch, mirror and key checksums, the package signing key, every installed package with its version, the banks, the image checksums and the identity scheme;
- `README.txt`: brief instructions for that release.

How the image is made (`scripts/sd-image-inner.sh`, in the `sd-builder` container): `apk --root --initdb` installs `alpine-base`, the kernel `linux-rpi`, the firmware `raspberrypi-bootloader` and the Wi-Fi firmware, eudev, OpenSSH, chrony, ifupdown-ng and `exxpiano`; the OpenRC runlevels get the services (eudev replaces mdev, `hostapd` and `dnsmasq` stay off: `exxpiano-wifi` starts them); the console `tty1` shows the status screen instead of a getty; `/etc/network/interfaces` configures only `eth0` (Wi-Fi belongs to `exxpiano-wifi`). The programs of the image then run in a chroot: the Python modules and the MIDI libraries import, the web service answers and makes the QR code of the setup network, the console screen renders. The root file system becomes an ext4 file with `mkfs.ext4 -d`, the boot partition a FAT32 file with `mtools`; no loop device and no privileged container is needed. The image is read back (`debugfs`, `mdir`) and its promises are checked before the ZIP is written: the kernel, the initramfs and the device trees of the Pi 3, 4 and 5, `arm_64bit=1` and `dtparam=audio=on`, the services of every runlevel, no getty and no automatic login, the banks, the demonstration pieces, no installation ID, the Wi-Fi country, the trusted key, and nothing of Debian or DietPi.

> **Root login.** With `SD_PASSWORD` root logs in over SSH with that password. Without it root has no password at all and logs in with a key only: give `--ssh-key`, or put your public key into a file `authorized_keys` on the boot partition of the card (the FAT partition any computer can open). At every start the device adds the keys of that file for root.

> **Setup network password.** Without `SD_AP_PASSWORD` the image keeps the default `exxpianosetup`, the same for every card made from it; the name of the network is unique per card (it ends with the installation ID). With `SD_AP_PASSWORD` all cards of this image share your password. Change it on one device with `WIFI_AP_PASSWORD` in `/etc/exxpiano.conf`.

## GitHub Release

Before tagging a release add a section `## <VERSION>` to `CHANGELOG.md`: the workflow appends it to the description of the GitHub Release (`scripts/release-notes.py`) and stops without it.

Pushing a tag named `v<VERSION>` starts `.github/workflows/release.yml` on a native arm64 runner. The workflow checks that the tag exactly matches `VERSION` and that the secret `EXXPIANO_APK_KEY` is there, runs the fast tests, builds the full and lite banks, builds the signed packages, builds and checks the image, and then creates the product GitHub Release with:

- `exxpiano-rpi-v<VERSION>-alpine-<ALPINE_VERSION>-sd.zip`;
- `manifest.json`;
- `README.txt`;
- `SHA256SUMS`.

The release has no separate `.apk` files: a device is upgraded by `scripts/deploy-to-pi.sh`. The title names exxpiano, its version and the boards. No release is created if any preceding step fails. For an existing release the title and description are updated and missing assets are added; an asset whose name already exists is never overwritten (the upload fails and the operator decides).

Ordinary pushes and pull requests run only the fast tests. `.github/workflows/image-pipeline.yml` runs when packaging, the services or the image builder change (or by hand): it builds the packages, starts the OpenRC services in a container (`scripts/test-openrc-services.sh`), installs, upgrades, rolls back and removes the packages (`scripts/test-apk-upgrade.sh`), and builds a complete image with stand-in banks (`scripts/test-sd-image-pipeline.sh`).

## Flashing and first boot

1. In Raspberry Pi Imager choose “Choose OS” → “Use custom” and select the ZIP, or select it in balenaEtcher.
2. Flash a clean SD card, put it in a Raspberry Pi 3, 4 or 5, and power it on.
3. The first boot takes a minute or two and needs no internet, Ethernet, or saved Wi-Fi: the root partition grows to the whole card (`growpart`, `resize2fs`), the name of the device is made, a board with little memory gets the `lite` bank.
4. The installation creates a persistent random 6-character hexadecimal ID in `/var/lib/exxpiano/installation-id`.
5. The ID produces a unique `exxpiano-Setup-<ID>` network and `exxpiano-<lowercase-id>` hostname. Join the setup network and open `http://10.42.0.1:8080/`; the web UI shows the exact ID, network name, and QR codes.

Cards cloned from the same ZIP receive different IDs on their own first boot. ID creation is atomic and the stored value is reused, so rebooting does not change Wi-Fi or hostname.

## Moving a device from DietPi

A card made from a DietPi image (version 1.10.20 or older) cannot be upgraded to Alpine in place: write the new image. To keep what the device has learned, copy it first (the device is reachable as before):

```bash
scp -r root@<device>:/var/lib/exxpiano ./exxpiano-data          # installation ID, imported banks, profiles, MIDI library
scp root@<device>:/etc/exxpiano.conf ./exxpiano-data/
scp root@<device>:/etc/wpa_supplicant/wpa_supplicant.conf ./exxpiano-data/     # the saved Wi-Fi networks
```

After the first start of the new card copy them back as root and restart the services:

```bash
scp -r ./exxpiano-data/* root@<device>:/tmp/restore/
ssh root@<device> 'cp -a /tmp/restore/. /var/lib/exxpiano/ && mv /var/lib/exxpiano/exxpiano.conf /etc/ && mv /var/lib/exxpiano/wpa_supplicant.conf /etc/wpa_supplicant/ && reboot'
```

The old installation ID then names the device again (setup network and hostname follow it after the reboot).

## Verification

For ordinary changes run the fast tests:

```bash
python3 -m unittest discover -s tests
```

Run the container checks and the image build when packaging or the builder changes or before a release (GitHub Actions applies the same split):

```bash
scripts/test-openrc-services.sh
scripts/test-apk-upgrade.sh
scripts/test-sd-image-pipeline.sh
```

A container is not a Raspberry Pi. **The Alpine image has not been started on real hardware yet.** On a Pi 3, 4 and 5 check `/var/log/exxpiano-firstboot.log`, the grown root partition, the console screen, the unique setup network and joining a Wi-Fi network, the web UI, the sound through the 3.5 mm jack and a USB card, plugging a USB sound card, drive and MIDI keyboard in and out, the latency of FluidSynth compared with DietPi, an upgrade and a rollback with `scripts/deploy-to-pi.sh`, and an offline reboot.
