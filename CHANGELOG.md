# Changelog

Every release needs a section `## <VERSION>` here: the release workflow puts it into the description of the GitHub Release and stops without it.

## 1.10.16

- **Wi-Fi scan on a board whose `wlan0` is down** (a freshly flashed card, or after the setup network): the scan said "Network is down (-100)" and the list of networks stayed empty. The interface is now brought up and the scan is repeated; if it still cannot be brought up, the Wi-Fi section says why (rfkill, the Wi-Fi country).

## 1.10.15

- The release takes DietPi's image for the Raspberry Pi 5 from this repository's own pre-release (pinned by its SHA-256), like the Pi 2/3/4 image; the `dietpi-mirror` workflow can create such a pre-release.
- The stand-in pipeline test knows the new file names (`manifest-rpi234.json`, `README-rpi234.txt`) and also builds the Raspberry Pi 5 image.

## 1.10.14

- **Image for the Raspberry Pi 5** (`exxpiano-rpi5-…-sd.zip`), built from DietPi's own Pi 5 image. It carries only the full piano, and since the Pi 5 has no 3.5 mm jack it plays through a USB sound card or an audio interface. **Not tried on a real Pi 5 yet.** The file names of the manifests and READMEs now carry the target: `manifest-rpi234.json`, `manifest-rpi5.json`.
- Screenshots of the web interface (English and Russian) in the documentation.

## 1.10.13

- **Import of banks from a USB drive:** put `.sf2` / `.sf3` files into the folder `piano-banks/` of a FAT32, exFAT or ext4 drive, plug it in and press **Import** in the **Piano bank** section; the bank is copied into the device's own library and stays after the drive is taken out. The drive is mounted read only, the copy is transactional (a drive pulled out or a full card leaves no half-copied bank), and the bank in use cannot be deleted. **Not tried on a real Raspberry Pi yet:** mounting the drives, the udev rule, pulling a drive out during the copy, and the load of a big copy on playing and power.

## 1.10.12

- **Wi-Fi scan:** when `iw` cannot scan, the list is now also asked from `wpa_supplicant` (`wpa_cli`), and when the list stays empty the Wi-Fi section says why (radio busy, Wi-Fi blocked by rfkill, no adapter, no `iw`).
- **`lite` bank:** soft notes are now as quiet as in `full` (the same dynamics settings). New builder option `--subzones` cuts every layer into several velocity zones that share the same samples at different volume (no extra memory); not used by the presets yet.
- Documentation: the interface is called the web interface; the purpose of the project, the Salamander limit and the comparison with alternatives were added; `standard` is gone from the documents and OpenSpec.

## 1.10.11

- **One image again, with the `full` piano** (about 700 MB, reported to start and play on a board with 1 GB, and by the author's ear sounds better than the earlier 390 MB bank). The `standard` image and preset are gone; `lite` stays for boards with 512 MB.
- Documentation: a comparison with Pianoteq on a Pi, Zynthian and hand-made FluidSynth setups; the status of `full` updated.

## 1.10.10

- The first start no longer restarts the piano service after choosing the smaller bank: the bank could be loaded twice, which makes the start of a 512 MB board longer.
- The release description now carries this changelog.

## 1.10.9

- **Two images per release:** `standard` (the tested piano, about 390 MB) and `full` (about 700 MB, for boards with 1 GB or more; not tested on a real board yet). File names, manifests and READMEs carry the variant.
- New `full` preset in `scripts/make-soundfonts.sh`; the memory reserve on boards with 1 GB or more is lowered from 400 to 200 MB (measured only on a 512 MB board).

## 1.10.8

- `exxpiano-run` replaces a configured bank that does not fit the memory of the board by the largest smaller bank of the library and remembers the choice in `/etc/exxpiano.conf`.

## 1.10.7

- Fix: the release build failed on the image smoke test (“the page is not served”): the check piped a page over 64 KB into `grep -q`.

## 1.10.6 (no release: the build failed, see 1.10.7)

- The image carries the smaller `lite` bank (about 237 MB) next to the full piano; a board with less than 800 MB (Raspberry Pi 3 A+) selects it at the first start. Played on a real Pi 3 A+.
- Memory check of banks on boards under 800 MB keeps 28 % for the system instead of 400 MB.
- The Monitor page lists the programs the kernel killed for lack of memory, with a hint.
- Fix: the built-in 3.5 mm sound is really on in the image (DietPi disabled it in three places).
- The Errors card on the Monitor page shows warnings and errors of the piano services since the boot, and the services' own diagnostics.

## 1.10.5 and earlier

See the git history. Notable before 1.10.5: the status screen on the HDMI console instead of a login prompt, QR codes can be downloaded from the device page only, a key-based deploy script with a password fallback.
