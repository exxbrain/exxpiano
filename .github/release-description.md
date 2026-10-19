A ready-to-flash **exxpiano** digital piano image for a clean SD card: one image `exxpiano-rpi-…` for the Raspberry Pi 3, 4 and 5 (64-bit Alpine Linux). **The Alpine Linux image has been tried on a Raspberry Pi 3 Model B, not yet on a Pi 4 or Pi 5**; the Pi 5 has no 3.5 mm jack, so it needs a USB sound card or an audio interface.

The image already includes:

- Alpine Linux 3.24 (64-bit, OpenRC);
- exxpiano;
- the Salamander Grand Piano bank (**full**, about 700 MB; reported to start and play on a board with 1 GB, the free memory afterwards was not recorded);
- the smaller `lite` bank, selected by itself on a board with less than 800 MB of memory (a Pi 3 A+), and one more bank to choose on the others;
- the piano mechanics bank;
- two demonstration recordings of real pianists in the MIDI player (Liszt, *Un Sospiro* and *La Campanella*, International Piano-e-Competition).

**Rights.**
- exxpiano itself (scripts, web interface): **MIT** ([LICENSE](https://github.com/exxbrain/exxpiano/blob/main/LICENSE)).
- The sounds: [**Salamander Grand Piano V3**](https://freepats.zenvoid.org/Piano/SalamanderGrandPiano/) by Alexander Holm, [**CC-BY**](https://creativecommons.org/licenses/by/3.0/). The `.sf2` banks in the image are derivative works (converted: fewer velocity layers, shorter tails, level-matched); credit: "Salamander Grand Piano V3 by Alexander Holm, CC-BY. Converted to SF2".
- The two recordings are from the [**MAESTRO dataset v3.0.0**](https://magenta.tensorflow.org/datasets/maestro) (Google LLC), [**CC BY-NC-SA 4.0**](https://creativecommons.org/licenses/by-nc-sa/4.0/): **non-commercial use only**, with attribution, shared under the same license, and this applies to these files, not to the rest of the image. If you use exxpiano commercially, delete them (**Delete** in the MIDI player). Details: [`assets/midi/NOTICE.md`](https://github.com/exxbrain/exxpiano/blob/main/assets/midi/NOTICE.md).
- Alpine Linux and its packages in the image keep their own licenses.

## Installation

1. Download `exxpiano-rpi-…-sd.zip` from this release's assets (the same image for the Raspberry Pi 3, 4 and 5).
2. Flash the ZIP to a clean SD card with Raspberry Pi Imager (`Choose OS` → `Use custom`) or balenaEtcher. Do not extract the ZIP manually.
3. Insert the card into the Raspberry Pi and power it on. The offline first boot takes a minute or two: the card's root partition grows to the whole card, then the piano bank loads; until it has loaded the keys give no piano sound.
4. If no saved network is available, join `exxpiano-Setup-<ID>` (password `exxpianosetup`, the same for every card) and open `http://10.42.0.1:8080/`.
5. Open the **Wi-Fi** section of the device's web interface to download the QR codes of this very card (join the setup network, open the web interface in the setup network) as PNG or SVG and print a sticker. The image itself contains no QR codes: the ID is created at the first boot.

On first boot, each installed card automatically creates its own persistent ID.

## Download verification

`SHA256SUMS` contains checksums for the image, manifest, and instructions. `manifest.json` records the product and Alpine versions, the pinned Alpine mirror and keys, every installed package with its version, and the included banks.

> The same original ZIP can be flashed to multiple clean cards. Do not clone a card after its first boot: doing so would also copy its installation ID.
