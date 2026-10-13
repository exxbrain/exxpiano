# Changelog

Every release needs a section `## <VERSION>` here: the release workflow puts it into the description of the GitHub Release and stops without it.

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
