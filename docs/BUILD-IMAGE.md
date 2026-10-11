# Building the production SD-card image

[Русский](BUILD-IMAGE.ru.md)

The product is a ready-to-use exxpiano device. Its delivery artifact is one complete DietPi image for one Raspberry Pi board, written directly to a clean SD card. A standalone Debian package or installer archive is not a customer delivery format.

The image targets the DietPi `RPi234 ARMv8` family: Raspberry Pi 2, 3 and 4 supported by that upstream image. Actual hardware verification is tracked separately for every model; the automated stand-in test is not a hardware test.

## Prerequisites

- Python 3 and a running Docker installation (or a Linux root environment with `--no-docker`).
- An official `DietPi_RPi234-ARMv8-*.img.xz` base image.
- The required standard banks `build/piano-standard.sf2` and `build/mech.sf2`. Build them once with `scripts/make-soundfonts.sh -p standard`.
- The serial number of every target board. Read it from `Serial` in `/proc/cpuinfo`; the builder normalizes 8–16 hexadecimal digits to 16 lowercase digits.

The build stops before changing the base image when the DietPi image, either bank, or the serial is missing. A finished image is always locked to its board. If the device cannot read its serial at runtime, or reads a different one, the synthesizer MUST NOT start.

## One board

```bash
python3 scripts/build-sd-image.py \
  --dietpi ~/Downloads/DietPi_RPi234-ARMv8-Trixie.img.xz \
  --serial 00000000d2f2f1de \
  --number auto \
  --password 'DietPi password, at least 8 characters' \
  --ap-password 'setup network password' \
  --country RU --timezone Europe/Moscow \
  --note 'device owner or production note'
```

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
  --password 'DietPi password' --ap-password 'setup network password' \
  --country RU --timezone Europe/Moscow
```

All rows are validated before the first image is built. The command creates one independently locked image per serial.

## Output and traceability

For every board, `dist/sd/<number>/` contains:

- `exxpiano-rpi234-v<VERSION>-dietpi-<DIETPI_VERSION>-<number>-sn-<serial>-sd.zip` — the complete flashable image;
- `manifest-<number>.json` — product version, DietPi version and source SHA-256, normalized number and serial, image SHA-256 and bank metadata;
- `README-<number>.txt` — the same device identity and flashing instructions;
- `sticker/` — the printable setup QR codes.

`VERSION` supplies the product version. The DietPi version is read from `dietpi/.version` inside the base image. If that metadata is missing or invalid, no release artifact is produced. The same identity is recorded in `registry/devices.csv`.

## Flashing a clean SD card

1. Open Raspberry Pi Imager, choose **Use custom**, and select the ZIP itself; do not unpack it. balenaEtcher can also flash the ZIP directly.
2. Select the clean SD card and write the image.
3. Insert the card into the exact board whose serial is in the filename, connect Ethernet for DietPi's first setup, and power it on.
4. After setup, open `http://exxpiano-<number>.local:8080/`. Without a configured network, use the `exxpiano-Setup-<number>` network and the printed QR codes.

## Verification status

`scripts/test-sd-image-pipeline.sh` builds the complete flow on a small DietPi-layout stand-in. `scripts/sd-smoke-test.sh` reads the result back and checks the DietPi boot files, installed application, both banks, their configuration and the serial lock. CI runs this pipeline but publishes no personalized images or package artifacts.

This does not prove real hardware operation. Before release, flash and test separate cards on Raspberry Pi 2, 3 and 4, including first boot, MIDI input, sound, standard piano and mechanics banks, and an offline reboot. Record those results per model.
