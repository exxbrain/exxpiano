# Building the production SD-card image

[Русский](BUILD-IMAGE.ru.md)

The product is a ready-to-use exxpiano device. Its delivery artifact is one complete DietPi image for one Raspberry Pi board, written directly to a clean SD card. A standalone Debian package or installer archive is not a customer delivery format.

The image targets the DietPi `RPi234 ARMv8` family: Raspberry Pi 2, 3 and 4 supported by that upstream image. Actual hardware verification is tracked separately for every model; the automated stand-in test is not a hardware test.

## Prerequisites

- Run the commands from the repository root and keep several gigabytes of disk space free for the unpacked image and temporary files.
- Python 3 and a running Docker installation (or a Linux root environment with `--no-docker`).
- An official `DietPi_RPi234-ARMv8-*.img.xz` base image.
- The required standard banks `build/piano-standard.sf2` and `build/mech.sf2`. Build them once with `scripts/make-soundfonts.sh -p standard`.
- The serial number of every target board.

Check Docker before starting:

```bash
docker info >/dev/null
```

The build stops before changing the base image when the DietPi image, either bank, or the serial is missing. A finished image is always locked to its board. If the device cannot read its serial at runtime, or reads a different one, the synthesizer MUST NOT start.

## Reading the board serial

Before building the final image, boot the target Raspberry Pi once from a temporary service SD card containing DietPi or Raspberry Pi OS. Run this command on the board:

```bash
awk -F ': ' '/^Serial/ {print tolower($2)}' /proc/cpuinfo
```

Record all 16 hexadecimal digits without whitespace. The builder accepts 8–16 digits for compatibility and left-pads a short value with zeroes, but the production registry should contain the complete value read from the board. Shut the board down with `sudo poweroff`; the service card is not needed in the finished device.

## Build passwords

Do not put production passwords in command arguments: shell history records them and process listings expose them. In Bash or Zsh, read them without echo and export them only for the builder:

```bash
printf 'DietPi password: '; read -r -s SD_PASSWORD; printf '\n'
printf 'Setup network password: '; read -r -s SD_AP_PASSWORD; printf '\n'
export SD_PASSWORD SD_AP_PASSWORD
```

Set both passwords explicitly for a production build. The DietPi password must contain at least 8 characters; the setup-network password must contain 8–63 characters. `--password` and `--ap-password` remain as compatibility overrides, but should not be used in the normal build procedure.

## One board

```bash
python3 scripts/build-sd-image.py \
  --dietpi ~/Downloads/DietPi_RPi234-ARMv8-Trixie.img.xz \
  --serial 00000000d2f2f1de \
  --number auto \
  --country RU --timezone Europe/Moscow \
  --note 'device owner or production note' \
  --dry-run
```

First inspect the printed plan, serial, number, and paths. Then repeat the command without `--dry-run`: only the second run creates the image and registry entry. After it finishes, remove the passwords from the current environment with `unset SD_PASSWORD SD_AP_PASSWORD`.

The command uses the standard banks automatically. `--piano FILE.sf2` and `--mech FILE.sf2` override them for an intentional variant. There is no product mode without banks and no optional lock switch.

`--number auto` selects the next free number from `registry/devices.csv`; `--number serial` uses the last four serial digits; an explicit number is also accepted. Use `--dry-run` to validate the inputs and show the plan without creating anything.

## A batch of boards

Create `boards.csv`:

```csv
serial,number,name,note
00000000d2f2f1de,,,Ivan
00000000aaaabbbb,0042,exxpiano-kitchen,Olga
100000001234cdef,,,
```

Then run:

```bash
python3 scripts/build-sd-image.py \
  --dietpi ~/Downloads/DietPi_RPi234-ARMv8-Trixie.img.xz \
  --serials boards.csv \
  --country RU --timezone Europe/Moscow
```

Passwords come from `SD_PASSWORD` and `SD_AP_PASSWORD` as described above. All rows are validated before the first image is built. The command creates one independently locked image per serial. For a batch, also add `--dry-run` first, inspect the complete plan, and only then rerun without it.

## Output and traceability

For every board, `dist/sd/<number>/` contains:

- `exxpiano-rpi234-v<VERSION>-dietpi-<DIETPI_VERSION>-<number>-sn-<serial>-sd.zip` — the complete flashable image;
- `manifest-<number>.json` — product version, DietPi version and source SHA-256, normalized number and serial, image SHA-256 and bank metadata;
- `README-<number>.txt` — the same device identity and flashing instructions;
- `sticker/` — the printable setup QR codes.

`VERSION` supplies the product version. The DietPi version is read from `dietpi/.version` inside the base image. If that metadata is missing or invalid, no release artifact is produced. The same identity is recorded in `registry/devices.csv`.

Before flashing, verify that the serial, number, and both versions in the ZIP filename match `manifest-<number>.json`. Compare the ZIP's SHA-256 with `image_sha256` in the manifest (`shasum -a 256 <file.zip>` on macOS or `sha256sum <file.zip>` on Linux). `registry/devices.csv` is not stored in Git and may contain owner information; make a protected backup after each batch.

## Flashing a clean SD card

1. Open Raspberry Pi Imager, choose **Use custom**, and select the ZIP itself; do not unpack it. balenaEtcher can also flash the ZIP directly.
2. Select the intended SD card and write the image. All existing data on the selected card will be destroyed.
3. Insert the card into the exact board whose serial is in the filename, connect Ethernet for DietPi's first setup, and power it on.
4. After setup, open `http://exxpiano-<number>.local:8080/`. Without a configured network, use the `exxpiano-Setup-<number>` network and the printed QR codes.

When accepting the first unit of each hardware model, inspect `/var/log/exxpiano-firstboot.log`, `systemctl status exxpiano`, and `journalctl -u exxpiano`; then test MIDI, sound from both banks, and a reboot without Ethernet. An unreadable or mismatched serial MUST leave `exxpiano` stopped. That is the expected protection, not a reason to disable the lock.

## Verification status

`scripts/test-sd-image-pipeline.sh` builds the complete flow on a small DietPi-layout stand-in. `scripts/sd-smoke-test.sh` reads the result back and checks the DietPi boot files, installed application, both banks, their configuration and the serial lock. CI runs this pipeline but publishes no personalized images or package artifacts.

This does not prove real hardware operation. Before release, flash and test separate cards on Raspberry Pi 2, 3 and 4, including first boot, MIDI input, sound, standard piano and mechanics banks, and an offline reboot. Record those results per model.
