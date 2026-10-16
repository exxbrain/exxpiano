# Changelog

Every release needs a section `## <VERSION>` here: the release workflow puts it into the description of the GitHub Release and stops without it.

## 1.10.22

- **The piano no longer sounds on key release after a restart.** The mechanics channels (hammer noise and string resonance, played when a key is released) could lose their presets and fall back to program 0, which is the piano itself: then every release started a piano note that was never stopped. The bridge selected the presets only once, when the synthesizer appeared, and passed everything from the keyboard on, so a keyboard's GM/GS reset (system exclusive), program change or bank select reached the synthesizer. Now the bridge drops those messages and selects the presets of all seven channels again every 2 seconds, so whatever resets them is healed within one scan. Reported on a Raspberry Pi 5 after playing while the synthesizer was still starting; **the exact trigger was not seen on the device**, and the fix is covered by automatic tests only.
- **The Pi 5 image also carries the `lite` bank**, as one more bank to choose on the web page.
- **The Monitor shows throttling and under-voltage on a Pi 5 without `vcgencmd`** (its firmware node has another path).
- The documentation says that the keys give no piano sound until the bank has loaded (several minutes on the first start), and records the check on a Raspberry Pi 5 with a Zoom U-24.

## 1.10.21

- **Low notes no longer stop abruptly when the pedal is lifted.** Every key of the piano banks had the same release, 0.7 s, so on a pedal lift the bass dropped out at once (heard at 0:37 of the La Campanella demonstration from bar 1:48). The release now follows the key: 2.6 s at A0, 1.5 s at C3, 0.95 s at C5, 0.7 s at the top, log-interpolated in between (`RELEASE_BY_KEY` in `tools/build_piano_sf2.py`); an SFZ release of 5 s or more is kept as before. It is in the banks built from this version on (`full` and `lite`); a bank built earlier or imported from a USB drive does not change. Measured offline on that moment: the level dip went from about 10 dB to about 5 dB. **Heard on a Raspberry Pi in the demonstration pieces.**
- **A half-pedal in a MIDI file no longer stops the notes.** Recordings of real pianists (the demonstration pieces) let the sustain value dip to 40-63 for a few tens of milliseconds; FluidSynth takes 64 as the limit and stopped every sounding note in each dip (a D sharp at 0:31 of the same piece). The MIDI player now counts the pedal as up only below 40 (`FILE_PEDAL_DOWN` in `pi/exxpiano-fx.py`). A keyboard's pedal is not changed: a switch pedal sends only 0 and 127. Measured offline: that D sharp stays 4-6 dB louder through the dip. **Heard on a Raspberry Pi in the demonstration pieces.**
- **256 voices by default instead of 192.** With 192 the end of La Campanella (the final fortissimo with the pedal) still ran out of voices and the synthesizer cut sounding notes: about ten clicks in a recording from a Raspberry Pi with 1.10.20. With 256 they are gone, at a load average of 0.3-0.7 during the piece. An existing `/etc/exxpiano.conf` keeps its value: set `POLYPHONY=256` there or in the **Audio output** section.

## 1.10.20

- **Crackling in the loud, dense places is fixed at its cause.** Two things made it: (1) **clipping** in the synthesizer: the sum of many loud voices with the pedal went over full scale, mostly in the right channel (La Campanella at the old gain 1.15: 3596 clipped samples in the second half, the loudest sample 4.6 dB over full scale); (2) the synthesizer **cutting sounding notes** when it ran out of voices. The remedies below are measured on a Pi 3 Model B with the pieces that came with the device; **not yet heard by the maintainers on other music or on other boards**.
- **The master volume is a scale in dB around the loudest value that does not clip** (`GAIN` 0.6, the new default; it was 1.0): 0 dB is that value, the slider goes from -34 to +5 dB, only its last 13% (orange) can make a loud passage crackle. The explanation is under the slider.
- **Level meter of both channels** under the volume slider on **Home**: the peak and the held peak of the left and the right channel in dBFS and a red mark with a counter when samples hit full scale (within 0.06 dB of it), so clipping can be seen. The synthesizer plays through the ALSA device `exxpiano_tap` (`/etc/alsa/conf.d/60-exxpiano-tap.conf`), which copies the stream into a pipe read by `exxpiano-meter.py`; it costs about 3% of one core; `METER=0` plays to the card directly, and the card is also used directly when the ALSA file or the program is missing. Tried on the 3.5 mm jack of a Pi 3 Model B only (not on a USB sound card).
- **Sound card volume** in **Audio output** (the headphone jack is loud): the hardware volume of the card that plays, in dB, applied at once and kept after a reboot (`alsactl store`); the row is hidden for a card without such a control.
- **Several synthesis threads** (`CPU_CORES`, default 3, one core always stays free; also a setting in **Audio output**): the voices are shared between the cores. On a Pi 3 Model B with La Campanella the busiest thread went from 44% (one core, 128 voices) to 23-28% (3 cores, 192 voices); no underruns, the buffer never below 9.9 ms. **Polyphony is 192 by default** (was 128); 256 was measured too (busiest thread 25-35%).
- **Less string resonance that nobody hears** (`--res-floor -64` by default; `--res-burst` and `--res-makeup` are optional): resonance voices quieter than -64 dBFS are not started (about two thirds of them, all of group V3 above C4); they were 35-70 dB under the piano and only took voices. About 11% of the energy of the resonance itself changes, which is about 45 dB under the music; judged by measurement, not by ear. `--res-floor -200` starts all of them as before.
- Model on La Campanella (offline, the same banks): clicks from lost voices 91 at 128 voices before, 5 at 192 voices with the resonance floor.
- Not tried on a real Raspberry Pi: the Pi 5 image, the USB drive, a USB sound card with the level meter, deleting a bank, and all of this on other music.

## 1.10.19

- **Two real pieces come with the device:** Liszt's *Un Sospiro* and *La Campanella* (recordings of real pianists, International Piano-e-Competition) are put into the MIDI library once at the first installation; a piece you delete does not come back. They are taken unchanged from the **MAESTRO dataset (Google), CC BY-NC-SA 4.0**: non-commercial use, with attribution, under the same license; the code stays MIT (`assets/midi/NOTICE.md`).
- **New section "About"** in the web interface: the links (source, releases, troubleshooting, changelog, problems), the version and the licenses of the code, the sounds and the demonstration recordings; a copyright line with a link to it is at the bottom of every section. The release description and the READMEs carry the same rights, with links.
- **New default mechanics settings** (what the owner tuned on a real board): hammer noise +1.5 dB, randomness 1.6, pedal noise +4.5 dB (the knock dependence on touch stays 0.8, the resonance 0). They apply to new cards and installations; a device that is already set up keeps its own settings. The defaults in the bridge program now match the documented ones.
- **The "Errors" card of the Monitor is called "Service messages"**: it also lists the notes of the piano's own scripts (grey), not only warnings and errors, and it says it is not the whole log.
- **The MIDI player and Home name the piece** as the library does (it was empty or a service name such as `control track`); units in the Russian interface are МБ and ГБ.
- **The release title says "SD card images for Raspberry Pi 2/3/4/5"**, the banner and the descriptions no longer speak only about Salamander (any SoundFont bank can be added; the mechanics are tuned on Salamander).
- **Screenshots** moved to the top of the README and retaken from the demo with the real pieces, the memory tiles, the Delete buttons and the seek slider (`scripts/make-screenshots.py` makes them).
- **Not tried on a real Raspberry Pi:** pressing **Delete** on a bank, the exactness of the seek and of the timing of a long piece (the first 5 seconds of a piece were measured: 0.1 s off), the demonstration pieces inside a built image, the Pi 5 image, the USB drive.

## 1.10.18

- **Wi-Fi on a fresh card:** `ifup wlan0` failed with "unknown interface wlan0" because a fresh DietPi has no `wlan0` stanza. The supervisor now writes the stanza, the `wpa_supplicant` header and the country, and unblocks rfkill before it brings the network up. A cable in a USB network adapter (or any wired port without a configuration) gets its address by DHCP (`ifupdown` and `isc-dhcp-client` are now dependencies). **Not tried on every board:** the USB Ethernet adapter was only seen on one Pi 3 B+.
- **A shorter installation ID: 6 hexadecimal characters** instead of 16 (`exxpiano-Setup-012ABC`, hostname `exxpiano-012abc`). A device that already has a long ID keeps it, so its names and printed QR codes do not change. With 24 bits two devices in one room almost never share a name, but it is not a guarantee.
- **MIDI player: seek and timing.** A slider and the **−10 s / +10 s** buttons move to any moment of the piece without stopping it (the sounding notes are let go, the pedal gets the state the file has there); the time shows elapsed / total / left and runs smoothly. A file whose track is called `control track`, `Track 1` or similar is named after the file.
- **Deleting banks.** The **Delete** button is on every bank that is not in use, an installed one as well as an imported one (not the current bank, the last good one or the mechanics bank; not while a bank switches). The question warns that it cannot be undone. OpenSpec change `delete-installed-banks`.
- **Memory in the bank list**, as in the Monitor: memory in use, the largest bank that fits (with the reserve of the system and the mechanics bank), free space on the card, a bar of the current bank and every bank's share of the limit.
- **A piece comes with the device:** Liszt's *Un Sospiro*, a recording of a real pianist (International Piano-e-Competition 2011, Yamaha Disklavier), is put into the MIDI library once at the first installation; a piece you delete does not come back. It is taken unchanged from the **MAESTRO dataset (Google), license CC BY-NC-SA 4.0**: non-commercial use, with attribution, under the same license; the code stays MIT. Delete the piece if you use exxpiano commercially (`assets/midi/NOTICE.md`).
- **Not tried on a real Raspberry Pi:** pressing **Delete** on a bank, the exactness of the seek and of the timing of a long piece, the demonstration piece inside a built image (the image check was extended, but the image was not built for this release by hand), the Pi 5 image, the USB drive. Tested by 402 automatic tests and in the demo.

## 1.10.17

- **MIDI player** (its own section of the menu): put `.mid` / `.midi` files into the folder `piano-midi/` of a USB drive, import them, and they play on the piano you have chosen, with the mechanics (hammers, dampers, resonance, pedal) and your touch curve. Stop and repeat; a piece goes on when another section is open, and Home shows what is playing. Only the notes and the sustain pedal of a file are played (no program or bank changes, no drums), and the files are limited (4 MB, 30 minutes). **Not tried on a real Raspberry Pi yet:** the exactness of the timing, the load of a dense file, the USB drive.
- **Quicker start without a USB sound card:** `exxpiano-run` no longer waits up to 8 seconds for a USB sound card when none is on its way (nothing waiting for its driver, USB had time to enumerate); a USB keyboard that is a capture-only audio card does not hold the start back either. The start time was not measured on a real Pi.
- The specifications are reorganized into a two-level tree (domain/capability) without changing a requirement.

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
