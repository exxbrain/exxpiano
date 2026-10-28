# Changelog

Every release needs a section `## <VERSION>` here: the release workflow puts it into the description of the GitHub Release and stops without it.

## Unreleased

- **The reverb is heard, and it has its own section, Effects.** Turned on, it stayed silent: FluidSynth starts every channel with a reverb send (MIDI CC91) of 0, the banks set no send of their own and nothing else sent one. The bridge now sets the send on the piano channel and on the channels of the mechanics (so both sound in the same room) whenever it meets the synthesizer and whenever the setting changes; imported banks get it too. CC91 itself would not do: FluidSynth's default modulator turns even CC91 = 127 into a send of 20 %, which was hardly heard on the device (about +0.5 dB in the pauses of La Campanella at 40). The bridge sets the send through the SoundFont NRPN of the generator reverbEffectsSend, 0-127 = 0-50 % (the whole 100 % drowned the piano), on both ways FluidSynth starts, and CC91 to 0; a keyboard's reverb knob still adds its 20 % on top. The section **Effects** has the switch, the amount (`REVERB_SEND`, 0-127, default 64, a quarter), the room size and the damping (`REVERB_ROOM`, `REVERB_DAMP`; `REVERB_WIDTH` and `REVERB_LEVEL` in the file only). They apply at once, through FluidSynth's command pipe, without a restart; when the synthesizer does not take them, they are kept and the page asks for a restart. Whoever had `REVERB=1` will now hear it. The reverb stays off by default. **Tried on a Pi 3 B with the Zoom U-24 at 64x2:** the switch and the room change while FluidSynth 2.4 plays, without a restart; at a send of 50 % the tail of a chord is 6-11 dB above the dry one; La Campanella with the reverb on: no xrun, the cores at 25-30 % on average, the audio thread from 18 % to 33 %. **Not tried yet:** the 3.5 mm jack, the plain-fluidsynth way; the default room is to be chosen by ear.

## 2.0.1

- **The Alpine image tried on a Raspberry Pi 5 with a USB sound card** (by the owner, with the 2.0 image): it boots, the card is chosen by itself, the piano sounds natural, with beautiful hammers and string resonance; pulling the card out is shown on the page (below).
- **A saved Wi-Fi network survives a power cut.** The network, `/etc/exxpiano.conf` and the profiles of the banks were written to a temporary file and renamed, without `fsync`: when the power was pulled soon after, ext4 could bring the old file back, and the Pi had forgotten the network (reported on a Raspberry Pi 5). Every write of settings on the card now waits until the file and its folder are on the card. Tried on the device: the network is still there after the power is pulled right after joining it.
- **A USB sound card pulled out is no longer shown as the output.** The page took the output from the last "audio output" line of the synthesizer; after the card was pulled out (a Pi 5, which has no 3.5 mm jack) the restarted synthesizer found no card and logged no new such line, so the page went on showing the card that was gone. The output now reads "no sound card" when the synthesizer said so afterwards, or "… is gone" when that card is not there any more. Reported and tried on a Raspberry Pi 5 with a USB card.

## 2.0.0

The device on Alpine Linux, with the preview piano and the first note within 15 s of power-on. **Checked before the release on the image of 2.0.0-rc1, written to a clean card, on a Raspberry Pi 3 Model B (1 GB) with a KeyLab Essential 49 mk3** (docs/BUILDING.md, "Before a major release"): the piano with the full bank and the mechanics, a bank switch and the MIDI player; the first note within 15 s of power-on, the preview heard, the move to the full bank without a pause; the setup network, joining a home network, forgetting it and the 5 minutes of the setup network.

- **The device runs on Alpine Linux 3.24 (64-bit) instead of DietPi.** One image for the Raspberry Pi 3, 4 and 5 (`exxpiano-rpi-v<VERSION>-alpine-<ALPINE_VERSION>-sd.zip`, `manifest.json`, `README.txt`), built from Alpine's packages of a pinned branch and mirror and checked with Alpine's keys; no ready-made image of a distribution is used any more. **The Raspberry Pi 2 is no longer supported.**
- **OpenRC instead of systemd:** the services restart after a crash (`supervise-daemon`), the Wi-Fi supervisor only after a failure; `/etc/exxpiano.conf` is read as the environment of the services by `exxpiano-env` and never run by a shell. The devices are handled by eudev, the console `tty1` shows the status screen from `/etc/inittab`, the first start (growing the card, the name, the `lite` bank on 512 MB) is the OpenRC service `exxpiano-firstboot`.
- **Signed `.apk` packages instead of `.deb`** (`scripts/build-apks.py`): `exxpiano`, `exxpiano-soundfonts`, and `py3-mido` and `py3-python-rtmidi`, which Alpine does not have. An upgrade (`scripts/deploy-to-pi.sh`) refuses a package the device does not trust, keeps the version before and rolls back to it with `apk` when the synthesizer or the bridge does not keep running (the rollback is tried in an Alpine container, `scripts/test-apk-upgrade.sh`; **not yet on a device**).
- **Service messages** in the Monitor come from the system log (`/var/log/messages`, busybox syslog) and the kernel's lines in it instead of journald; the drive import now logs too.
- **Wi-Fi:** `exxpiano-wifi` runs `wpa_supplicant` and `udhcpc` on `wlan0` itself (no `ifupdown` stanza), takes the Wi-Fi country from `WIFI_COUNTRY`, and stays out of the way of NetworkManager, iwd or connman. The host name is set without `hostnamectl`.
- **Root login:** with the password given to the build, or without one by key only; keys in `authorized_keys` on the boot partition are added for root at every start.
- **Moving from DietPi:** cards with 1.10.22 or older are not upgraded in place; copy the data, write the new image and copy it back (docs/BUILD-IMAGE.md).
- **Tried on a Raspberry Pi 3 Model B (1 GB):** the image boots offline, grows to the card, names itself `exxpiano-<id>`, shows the status screen, starts every service; the full piano loads (with 16 MB for the graphics processor: `gpu_mem=16` in `config.txt`), the KeyLab Essential 49 mk3 reaches the bridge and the synthesizer, the web interface switches banks, an upgrade through `apk` works. *La Campanella* from the MIDI player was recorded at the output with a bank built before 1.10.21 (260 s): peaks -3.3 dBFS, no clipped sample, no gap in the stream, no xrun; FluidSynth took up to about one core in all at the climax. Found and fixed on the board: the ALSA sequencer modules were not loaded (no MIDI at all), the full piano did not fit (76 MB for the graphics processor), the generic name `exxpiano` stayed, leftover level meters after restarts of the synthesizer, and a crashed or stopped meter left the synthesizer silent (a fault of earlier versions too).
- **A preview piano plays while the piano bank loads**, at the start and after a switch of the bank in the web interface, which no longer restarts the synthesizer. `piano-preview.sf2` (24 MB, `scripts/make-soundfonts.sh -p preview`: 3 velocity layers, stereo at 22.05 kHz, 3 s tails) is in the image; the new `exxpiano-synth.py` (FluidSynth through its library) plays it with the mechanics, reads the big bank in a second synthesizer object and moves it over at a moment with no key and no pedal down. Measured on a Raspberry Pi 3 Model B with the full bank: the preview sounds 5 s after the start of the service, the full bank 37 s after it; notes wait at most 0.5 ms while the bank loads (31.7 s with a plain load); a switch to `lite` took 17 s, back to `full` 38 s, with no second of silence and no restart, the memory of the old bank given back first. Without a usable preview bank (`PREVIEW_SF2` empty, missing or damaged) everything works as before. Not yet: a recording of the move to look for clicks, listening by ear, a rollback, the Pi 3 A+, Pi 4 and Pi 5.
- **The first note within 15 s of power-on** (measured once: 14.5 s on a Raspberry Pi 3 Model B with the full bank, 20 s before). The synthesizer, the bridge, the first start and the mixer levels start in the boot runlevel before the network; with the preview nothing is checked before the first note (the synthesizer checks the piano bank and the memory in the background); the mechanics load right after the preview; `usercfg.txt` gets `boot_delay=0` and `initial_turbo=30`.
- **The 3.5 mm jack starts at full volume** (+4 dB instead of the driver's -20 dB), set once by the first start of a new card. **Not tried on a freshly written card yet** (covered by automatic tests; on the Pi 3 B the jack was at +4 dB, set by hand).
- **The box can be on no network at all.** Without a saved network the setup network is on for 5 minutes after every start and after the last network is forgotten (a phone connected to it keeps it on), then the Wi-Fi is off. The network in use can now be forgotten even when it is the only way in: the page asks first. Tried on a Raspberry Pi 3 Model B: forgetting the network, the setup network with the list of networks for 5 minutes, then the Wi-Fi off, and the setup network again after a restart. Found and fixed there: hostapd was started before wpa_supplicant had left the network (no setup network after "Forget"), and the list of networks came out empty right after leaving one.
- **The setup network comes up when the cable is pulled out.** The route of the unplugged cable stayed behind, marked `linkdown` (busybox `udhcpc` does not remove it), the Wi-Fi supervisor took the Pi for connected and never started the setup network; such routes no longer count. Tried on a Raspberry Pi 3 Model B: the setup network came up, a phone joined it by its password, the Pi joined the home network in 6 s and kept it.
- **Not tried yet on hardware:** the Pi 3 A+, Pi 4 and Pi 5; a USB sound card and a USB drive plugged in through eudev; the QR code of the setup network; a rollback on the device; listening by ear and the latency compared with DietPi.

## 2.0.0-rc1

The release candidate of 2.0.0, with the same changes; published as a pre-release and checked on a Raspberry Pi 3 Model B (see 2.0.0).

## 1.10.22

- **The piano no longer sounds on key release after a restart.** The mechanics channels (hammer noise and string resonance, played when a key is released) could lose their presets and fall back to program 0, which is the piano itself: then every release started a piano note that was never stopped. The bridge selected the presets only once, when the synthesizer appeared, and passed everything from the keyboard on, so a keyboard's GM/GS reset (system exclusive), program change or bank select reached the synthesizer. Now the bridge drops those messages and selects the presets of all seven channels again every 2 seconds, so whatever resets them is healed within one scan. Reported after playing while the synthesizer was still starting; **the exact trigger was not seen on the device**. Checked with the real FluidSynth library (a GM reset puts the mechanics channels on the piano; the bridge restores them within one scan, and the repeated preset selection does not cut notes held by the pedal), and by ear on a Raspberry Pi 3 Model B with the new bridge after a reboot, playing while it started: the piano sounds on key press. Not yet checked on a Pi 5.
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
