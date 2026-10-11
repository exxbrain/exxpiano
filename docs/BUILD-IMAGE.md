# How to build an SD card image (bound to the board's serial number)

For whoever prepares the devices. The result is one zip per Raspberry Pi 3 board that is written to a card and sets itself up at power-on. The image is bound to the
**serial number of the board** it will run on: the number and name of the device, its setup network and QR sticker, an entry in the register.

## What you need (once)

| What | Why | How |
|---|---|---|
| A Mac (or Linux) with **Python 3** | the script | already there |
| **Docker Desktop**, running | the image is made in a Linux container | install from docker.com and start it; check with `docker info` |
| Sound banks `.sf2` | they go into the image | `scripts/make-soundfonts.sh`; made ones are in `build/archive/` |
| The **DietPi** image for Pi 2/3/4 (ARMv8, Trixie) | the base | download `DietPi_RPi234-ARMv8-Trixie.img.xz` from https://dietpi.com/ (Downloads) and keep it, for example in `~/Downloads/` |
| Disk space | an image is about 1.5 GB, a zip 0.5-1 GB per device | check `df -h` |

Downloading by hand is simpler and safer. There is also `--download` (it fetches into `build/cache/` and checks the checksum) but it has not been tried against the real site.

> To see what the page looks like you do not need an image: `python3 scripts/demo-web.py --open` (a simulated device on your computer).

## Step 1. Find the serial number of the board

Every Raspberry Pi has a serial number burnt into the processor. You only need it when you bind the image to a board (otherwise skip this step and `--serial` below).

- The board runs with any card, over SSH: `grep Serial /proc/cpuinfo` -> for example `Serial : 00000000d2f2f1de`.
- The board already has our software: the serial is shown on the page under **Wi-Fi -> Device name**, and by `python3 /opt/piano-synth/deviceid.py`.

Write down the 16 digits (the last 8 are enough: `d2f2f1de`).

## Step 2. One image for one board

```bash
cd ~/Projects/pi-salamander-piano
python3 scripts/build-sd-image.py \
  --dietpi ~/Downloads/DietPi_RPi234-ARMv8-Trixie.img.xz \
  --piano build/archive/Salamander-stereo-6L-9s-v3.sf2 \
  --serial d2f2f1de --lock --number serial \
  --password 'root password, at least 8 characters' --ap-password 'sticker password, 8-63 characters' \
  --country RU --timezone Europe/Moscow --note 'for Ivan, 12 Oct'
```

The options:

- `--serial` the board the card is for; without it the image is generic and takes its number from the Wi-Fi address at the first start.
- `--lock` the synthesizer **does not start** on another board (without it, only a red warning on the page). It is bookkeeping and a lock against copying a card to another board, not strong
  protection: whoever has root on the device can edit `/etc/piano-synth.conf`.
- `--number serial` the device number = the last 4 digits of the serial (`F1DE`); `--number auto` takes the next free one (`0001`, `0002` ...); or give it by hand (`--number 0007`).
- `--password` the DietPi global password (root and dietpi users). Without it the default `dietpi` stays: **always set your own for a device you hand over**.
- `--ap-password` the password of the setup network `Piano-Setup-<number>` (default `pianosetup`, also printed on the sticker).
- `--mech` the mechanics bank (default `build/archive/Salamander-mech.sf2`), `--no-banks` build without banks (install them later).
- `--dry-run` check everything and show the plan, make nothing. **Start with it.**

The first run builds the builder container image (a minute). The image itself takes 1-3 minutes. At the end the script reads the image back and starts its programs in a container as a test.

## Step 3. A batch from a list of boards

A file `boards.csv` (the first line with the column names is required; `number`, `name`, `note` are optional; lines starting with `#` are skipped):

```csv
serial,number,name,note
00000000d2f2f1de,,,Ivan
00000000aaaabbbb,0042,piano-kitchen,Olga
100000001234cdef,,,
```

```bash
python3 scripts/build-sd-image.py --dietpi ~/Downloads/DietPi_RPi234-ARMv8-Trixie.img.xz --piano build/archive/Salamander-stereo-6L-9s-v3.sf2 \
  --serials boards.csv --lock --password '...' --ap-password '...' --country RU --timezone Europe/Moscow
```

Everything is checked before the first card (repeated serials and numbers, wrong values). If a build fails in the middle the script names the cards that are done (they are in the register); run the rest again.

## What you get

In `dist/sd/<number>/`:

- `piano-synth-<number>-sd.zip` the card image;
- `sticker/setup-codes-Piano-Setup-<number>.html` a sticker with two QR codes (join the network, open the page) and captions; open it in a browser and print;
- `README-<number>.txt` what the device is, how to write the card, what it is called.

And a line in the register `registry/devices.csv` (number, name, serial, version, date, checksum of the image, banks, note). The register is kept out of git: back it up. Commands:
`python3 scripts/sd-registry.py list | show 0007 | find ivan | note 0007 "sold 12 Oct"`.

## Step 4. Write the card and switch on

1. **Raspberry Pi Imager**: "Choose OS" -> "Use custom" -> pick the zip (no need to unzip). Or **balenaEtcher**: "Flash from file" -> the zip.
2. Put the card in the Pi, **plug in an Ethernet cable** (DietPi's own first-run setup downloads its updates once), switch on.
3. Wait a few minutes (DietPi sets itself up, the card grows). Then the device is at `http://piano-<number>.local:8080/`, and with no network it starts its own `Piano-Setup-<number>` network: scan the QR codes of the sticker.

## Checking, and when something is wrong

- **The image has not been started on a real Raspberry Pi** (the whole pipeline is tested on a stand-in image and in a container). Write the first card, watch it start, and read
  `cat /var/log/piano-synth-firstboot.log` and `journalctl -u piano-synth -u piano-web -u piano-wifi`.
- A red bar on the page "this card was made for another board": the board's serial differs from the one in the image. If the board was replaced, build a new card for its serial or remove `DEVICE_SERIAL` from `/etc/piano-synth.conf`.
- `Docker is not running` / `Cannot connect to the Docker daemon`: start Docker Desktop.
- No space: free the disk; the work files are in `build/sd-work/` (safe to delete).
- Loop devices left busy after a crash: `docker run --rm --privileged piano-sd-builder losetup -D`.
- An x86 Linux machine (not an Apple Silicon Mac): running the arm64 image's programs needs `qemu-user-static` and binfmt; an Apple Silicon Mac does not.
- The DietPi address on dietpi.com changed: download the image by hand and give it with `--dietpi`.
- To test the builder itself without a real DietPi: `scripts/sd-fake-dietpi.sh` makes a stand-in image, `scripts/sd-smoke-test.sh` runs the programs of a finished image.

## Updating devices that are already out

An image is built once; a running device is updated over the network with `scripts/deploy-to-pi.sh <address>` (it rolls back on failure). A new software version reaches the next images by itself (the version comes from `VERSION`).
