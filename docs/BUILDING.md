# Building exxpiano yourself

English | [Русский](BUILDING.ru.md)

Only for building the banks and the image yourself, or working on the code. To play, the ready image of the [latest release](https://github.com/exxbrain/exxpiano/releases/latest) is enough (see the [README](../README.md)). The SD card image itself: [BUILD-IMAGE.md](BUILD-IMAGE.md).

## What you need to build

| | |
|---|---|
| A Mac or Linux machine for the banks | `python3`, `ffmpeg` (with `ffprobe`), `curl`, `tar` with xz; ~2 GB of free space |

## Deployment in short

The shipped product is a ready device with exxpiano already installed. One complete DietPi image is reusable across compatible Raspberry Pi 2/3/4 boards (a Pi 5 has an image of its own, built with `--target rpi5`); a separate package or installer bundle is not a delivery format.

1. Build the banks once: `scripts/make-soundfonts.sh -p full` and `-p lite` (the second reuses the samples downloaded by the first). This creates the required `build/piano-full.sf2` and `build/mech.sf2` and the smaller `build/piano-lite.sf2` for boards with 512 MB.
2. Build the reusable image with `scripts/build-sd-image.py --dietpi <DietPi_RPi234-ARMv8...img.xz>`.
3. Flash `exxpiano-rpi234-v<VERSION>-dietpi-<DIETPI_VERSION>-sd.zip` directly to a clean SD card with Raspberry Pi Imager or balenaEtcher.
4. Insert the card into a compatible Raspberry Pi 2, 3 or 4 and power it on.

See [the image-building guide](BUILD-IMAGE.md) for the complete procedure and current verification status.
Version tags named `v<VERSION>` build this image and publish it together with its manifest, README, and checksums as assets of a GitHub Release.

## Build the sound banks (on a Mac/Linux)

The banks themselves are not stored in the repository (hundreds of megabytes, and Salamander's license is CC-BY, see
[NOTICE.md](../NOTICE.md)). A script makes them from the original archive:

```bash
scripts/make-soundfonts.sh -p full -o build          # downloads ~412 MB and builds
```

If you already have Salamander unpacked (a folder with `SalamanderGrandPianoV3Retuned.sfz`), point to it: `-s /path/to/folder`.

Result: `build/mech.sf2` (36 MB) and `build/piano-<preset>.sf2`.

### Choosing the sample package (`-v`)

Salamander is published by [FreePats](https://freepats.zenvoid.org/Piano/acoustic-grand-piano.html) in several packages. The builders
find the sample files through the paths written in the SFZ, and convert everything to 44.1 kHz / 16 bit, so the
resulting banks have the same size whichever package you take; the source only changes how faithful the conversion is
and how much you download.

```bash
scripts/make-soundfonts.sh -p full -v 48k24 -o build     # take the 48 kHz / 24 bit WAV package
```

| `-v` | Package | Download | Status |
|---|---|---|---|
| `44k16` (default) | SFZ WAV, 44.1 kHz 16 bit | 412 MB | built and used on a real Pi |
| `48k24` | SFZ WAV, 48 kHz 24 bit | 1.26 GB | checked on a copy of the `44k16` set converted to this format (the rendered audio matches the `44k16` build, difference 67 dB below the signal); the real 1.26 GB download was **not** tried |
| `48k24-flac` | SFZ FLAC, 48 kHz 24 bit (2020-06-02 package) | 742 MB | **experimental**: the code path (FLAC, other folders) is tested on a synthetic set, but the real archive's layout and SFZ file name were not inspected; if the SFZ is not found, pass `-n NAME` |

For any other Salamander SFZ package use `-u URL` (and `-n NAME` if its SFZ file is named differently). The layer
table in the builder assumes the 16 velocity layers of the V3 set. FreePats also offers a ready-made SF2 of Salamander;
it is not used here because it has no mechanics presets and no layer reduction.

| Preset | Memory on the Pi | What it is | `GAIN` | Tested on a Pi 3 |
|---|---|---|---|---|
| `lite` | ~250 MB | 6 layers, stereo, 9 s tails | 0.6 | a close variant (CPU load ~30%) |
| `full` | ~700 MB | 14 layers, stereo, 12 s tails; for a board with 1 GB (the memory reserve for such boards is 200 MB). **Not for 512 MB** | 0.6 | starts and plays on a board with 1 GB; by ear it sounds better than the earlier 390 MB bank and significantly better than the ready-made light Salamander SoundFont of the official library (one listener, not a blind test); the free memory after loading was not recorded, watch `free -m` |

The whole file is read into RAM. A Pi 3 Model B/B+ has 1 GB; a **Pi 3 Model A+ has only 512 MB (about 414 MB usable)**, and `full` does not fit there:
on a real Pi 3 A+ the kernel killed FluidSynth with a 390 MB bank at about 370 MB (`Out of memory: Killed process (fluidsynth)`) and the service restarted again and again. Use `lite`
(237 MB; **played on a real Pi 3 A+, it works**, including the automatic choice at the first start). `lite` against `full`: 6 velocity layers
instead of 14, tails of 9 s instead of 12 s, and the softest note is louder (−12 dB instead of −17 dB), so the dynamic range is somewhat smaller. Check the free memory yourself (`free -m`, the `available` column).

## Upgrading a running Pi

```bash
scripts/deploy-to-pi.sh 192.168.1.95        # or user@host; logs in by SSH key
```

It builds the package, sends it to the Pi and upgrades it there: it keeps `/etc/exxpiano.conf`, backs the old version up first, checks that
the synthesizer and the bridge come back, and rolls back by itself if they do not (`deploy/upgrade-on-pi.sh`, by hand: `deploy/rollback-on-pi.sh`).
The sound banks are not touched.

**Passwords during an upgrade.** The script logs in with your SSH key and asks no password and no host-key question. On a device that does not have your key yet (a new card, a reinstalled image) it installs the key with `ssh-copy-id`: the SSH password is asked **that one time**. You need a key on your computer (`ls ~/.ssh/*.pub`; if there is none: `ssh-keygen -t ed25519`). If the key cannot be installed, the script falls back to a plain password login (asked once) rather than stopping: nothing is ever asked on the device itself, which has no monitor. DietPi's password is `dietpi` unless `SD_PASSWORD` (`--password`) was set when the image was built. A host key that has CHANGED still stops the script: that is a reason to check, not to ignore.

## Where things live

Everything is in this one folder. Git ignores the large generated parts, so they can never be committed (the pre-commit hook also refuses any
staged file over 5 MB):

| Folder | What | In git |
|---|---|---|
| `pi/`, `tools/`, `scripts/`, `deploy/`, `dietpi/`, `packaging/`, `tests/`, `docs/` | the program, image builder, internal packaging, tests | yes |
| `build/` | built banks (`mech.sf2`, `piano-<preset>.sf2`), the downloaded Salamander in `build/salamander-44k16/`, older banks in `build/archive/` | no |
| `dist/sd/` | the reusable complete SD image and its metadata | no |

## Look at the web interface without a Raspberry Pi

```bash
python3 scripts/demo-web.py --open            # http://127.0.0.1:8080/
```

The real web interface and the real server code run on your computer against a **simulated device** (services, CPU and temperature that move, Wi-Fi networks, three banks, the audio
interface). Change the sliders, switch the bank, rename the device, add or forget a Wi-Fi network (a name with "fail" does not connect, "Cafe" is open), scan the QR codes. Options:
`--mode ap` (setup mode: the device with no network), `--installation-id 012ABC`, `--hostname NAME`, `--throttled`,
`--bind 0.0.0.0` (to look from a phone on the same network), `--token SECRET`. Nothing is written outside a temporary folder.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

They check the mechanics logic (`FXCore`), the structure of the `.deb` packages, and that the SF2 builders accept the
different sample packages (44.1 kHz/16 bit and 48 kHz/24 bit, WAV and FLAC, different folder layouts; this part needs
`ffmpeg` and is skipped without it). No hardware is needed. The ALSA exchange is not covered by tests (checked on a real Pi),
and the audio quality of a built bank was checked by hand through FluidSynth.

`scripts/lint.sh` runs the code checks: `ruff` on the Python code (errors, and cleanliness: likely bugs, needless code, files left open, sorted imports, naming; the rules and the house-style exceptions are in `ruff.toml`), `shellcheck` on the shell scripts, and no file over 5 MB in git. CI runs the tests and these checks on every push and pull request (`.github/workflows/build.yml`).
