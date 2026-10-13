Ready-to-flash **exxpiano** digital piano image for a clean Raspberry Pi 2, 3, or 4 SD card (ARMv8).

The image already includes:

- DietPi;
- exxpiano;
- the Salamander Grand Piano bank: **standard** (about 390 MB, tested) in the `…-standard-sd.zip` image, or **full** (about 700 MB, for boards with 1 GB or more; not yet tested on a real board) in `…-full-sd.zip`;
- the smaller `lite` bank, selected by itself on a board with less than 800 MB of memory (a Pi 3 A+);
- the piano mechanics bank.

## Installation

1. Download one image from this release's assets: `exxpiano-rpi234-…-standard-sd.zip` (a safe choice) or `…-full-sd.zip` (the bigger piano, only for boards with 1 GB or more).
2. Flash the ZIP to a clean SD card with Raspberry Pi Imager (`Choose OS` → `Use custom`) or balenaEtcher. Do not extract the ZIP manually.
3. Insert the card into the Raspberry Pi and power it on. The offline first boot may take several minutes.
4. If no saved network is available, join `exxpiano-Setup-<ID>` (password `exxpianosetup`, the same for every card) and open `http://10.42.0.1:8080/`.
5. Open the device's **Wi-Fi** page to download the QR codes of this very card (join the setup network, open the setup page) as PNG or SVG and print a sticker. The image itself contains no QR codes: the ID is created at the first boot.

On first boot, each installed card automatically creates its own persistent ID.

## Download verification

`SHA256SUMS` contains checksums for the image, manifest, and instructions. `manifest-standard.json` and `manifest-full.json` record the exact product and DietPi versions, included banks, and source-image hash.

> The same original ZIP can be flashed to multiple clean cards. Do not clone a card after its first boot: doing so would also copy its installation ID.
