# exxpiano

English | [Русский](README.ru.md)

A digital piano appliance for Raspberry Pi 2/3/4 ARMv8: **Salamander Grand Piano V3** (Yamaha C5) through FluidSynth, plus
**piano mechanics** - hammer and damper noise, string resonance, pedal noise - which the SF2 format cannot
express by itself, so a small bridge program adds them.

```
USB MIDI keyboard ─► exxpiano-fx.py ─► FluidSynth ─► audio output
                        │             ├─ piano.sf2  (the piano, channel 0)
                        └─ mechanics ─►└─ mech.sf2   (channels 1-6)
```

Everything starts by itself when the Pi powers on, and keyboards connect automatically: the bridge rescans the MIDI
ports every 2 seconds. Connecting a keyboard while the Pi is running is supported by design, but only connecting at boot
was tested on a Pi.

## Tested on

- Raspberry Pi 3 Model B (1 GB), DietPi (Debian, FluidSynth 2.4.4), Arturia KeyLab Essential 49 mk3 keyboard,
  onboard 3.5 mm output;
- building the banks: macOS (Apple Silicon), FluidSynth 2.6.1, ffmpeg.

Other boards, keyboards and sound cards were not tested. I recommend a USB sound card (see "Audio output and latency"),
but this setup was not measured with one.

## What you need

| | |
|---|---|
| A Raspberry Pi 2, 3 or 4 supported by the DietPi RPi234 ARMv8 image | an SD card containing the product image |
| **A 5.1 V / 2.5 A power supply and a short cable** | see "Power supply" - this is not a formality |
| A USB MIDI keyboard | class-compliant, no drivers needed |
| A Mac or Linux machine to build the banks | `python3`, `ffmpeg` (with `ffprobe`), `curl`, `tar` with xz; ~2 GB of free space |

## Deployment in short

The shipped product is a ready device with exxpiano already installed. One complete DietPi image is reusable across compatible Raspberry Pi 2/3/4 boards; a separate package or installer bundle is not a delivery format.

1. Build the standard banks once: `scripts/make-soundfonts.sh -p standard`. This creates the required `build/piano-standard.sf2` and `build/mech.sf2`.
2. Build the reusable image with `scripts/build-sd-image.py --dietpi <DietPi_RPi234-ARMv8...img.xz>`. No board serial is entered.
3. Flash `exxpiano-rpi234-v<VERSION>-dietpi-<DIETPI_VERSION>-sd.zip` directly to a clean SD card with Raspberry Pi Imager or balenaEtcher.
4. Insert the card into a compatible Raspberry Pi 2, 3 or 4 and power it on.

See [the image-building guide](docs/BUILD-IMAGE.md) for the complete procedure and current verification status.
Version tags named `v<VERSION>` build this image and publish it together with its manifest, README, and checksums as assets of a GitHub Release.

## Quick start

### 1. Build the sound banks (on a Mac/Linux)

The banks themselves are not stored in the repository (hundreds of megabytes, and Salamander's license is CC-BY, see
[NOTICE.md](NOTICE.md)). A script makes them from the original archive:

```bash
scripts/make-soundfonts.sh -p standard -o build      # downloads ~412 MB and builds
```

If you already have Salamander unpacked (a folder with `SalamanderGrandPianoV3Retuned.sfz`), point to it: `-s /path/to/folder`.

Result: `build/mech.sf2` (36 MB) and `build/piano-<preset>.sf2`.

#### Choosing the sample package (`-v`)

Salamander is published by [FreePats](https://freepats.zenvoid.org/Piano/acoustic-grand-piano.html) in several packages. The builders
find the sample files through the paths written in the SFZ, and convert everything to 44.1 kHz / 16 bit, so the
resulting banks have the same size whichever package you take; the source only changes how faithful the conversion is
and how much you download.

```bash
scripts/make-soundfonts.sh -p standard -v 48k24 -o build     # take the 48 kHz / 24 bit WAV package
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
| `standard` | ~390 MB | 8 velocity layers, stereo, 11 s tails | 1.0 | yes, works |
| `soft` | ~390 MB | same, but soft notes are noticeably quieter (wider dynamics) | 1.0 | no, only on the Mac |
| `lite` | ~250 MB | 6 layers, stereo, 9 s tails | 1.0 | a close variant (CPU load ~30%) |
| `small` | ~135 MB | 6 layers, **mono**, 10 s tails | 2.0 | no, only on the Mac |

The whole file is read into RAM, and a Pi 3 has 1 GB. I did not measure the free memory after loading `standard`
precisely; check yourself (`free -m`, the `available` column). If the Pi runs out of memory or CPU, use `lite` or `small`.

**The screen of the device asks for nothing.** With a monitor on HDMI the console shows a status screen (installation ID, name, addresses of the control page, the setup network if there is no network) instead of a `login:` prompt, so nothing has to be typed on a device that is used without a monitor. There is no local login and no root autologin; administer the device over SSH (user `root`, DietPi's password).

## SD card image for new devices

This is one reusable ZIP for any number of clean cards. It contains DietPi, the application and both required banks. At first boot every installed copy creates a
persistent random 16-character hexadecimal installation ID. The user enters neither a board serial nor another identifier. The ID gives it a unique hostname
`exxpiano-<id>` and setup network `exxpiano-Setup-<ID>`.

```bash
# once: download DietPi_RPi234-ARMv8-Trixie.img.xz from dietpi.com (or add --download to let the script fetch it)
printf 'DietPi password: '; read -r -s SD_PASSWORD; printf '\n'
printf 'Setup network password: '; read -r -s SD_AP_PASSWORD; printf '\n'
export SD_PASSWORD SD_AP_PASSWORD
python3 scripts/build-sd-image.py --dietpi ~/Downloads/DietPi_RPi234-ARMv8-Trixie.img.xz \
    --country RU --timezone Europe/Moscow
unset SD_PASSWORD SD_AP_PASSWORD
```

It makes `dist/sd/`: `exxpiano-rpi234-v<VERSION>-dietpi-<DIETPI_VERSION>-sd.zip`, `manifest.json`, and `README.txt`. Write the zip with **Raspberry Pi
Imager** ("Choose OS" -> "Use custom") or **balenaEtcher** (both take the zip as it is); there is nothing to unzip onto the card by hand, because a Raspberry Pi card needs
its partitions, which only an image carries. Switch the Pi on without preparing a network: the product image skips DietPi's online first-run update. The device is
`http://exxpiano-<id>.local:8080/`; with no Ethernet or saved Wi-Fi it starts its unique setup network (see "Connecting to a new Wi-Fi").

- **Installation identity.** The ID is generated once from 64 cryptographically random bits and stored in `/var/lib/exxpiano/installation-id`. The full ID is present in the setup SSID and QR codes, making collisions extremely unlikely. Step by step: **[docs/BUILD-IMAGE.md](docs/BUILD-IMAGE.md)**.
- **How it is made.** In a Linux container (Docker Desktop must be running; on Linux with root use `--no-docker`): the image is opened, the package and its dependencies are
  installed into it (`apt`, no recommended packages), the banks are copied, and DietPi is configured as a pre-installed offline system. DietPi does not own `wlan0`, wait for
  a network, or run update checks at first boot; `exxpiano-wifi` owns the radio and starts the setup AP. The finished image is read back and its web service is started in a
  container as a test. The passwords go in through the environment, never through arguments.
- **Honest status.** The whole build is tested on a stand-in image with DietPi's layout, and the programs inside the finished image run; **it has not been started on a
  Raspberry Pi**: write the first card, watch it start, and read `/var/log/exxpiano-firstboot.log`. `--download` has not been tried against dietpi.com either: if the address
  has changed, download the image yourself and give it with `--dietpi`. The stand-in image (`scripts/sd-fake-dietpi.sh`) and `scripts/sd-smoke-test.sh` are there to
  test changes to the builder without a Pi.

**The screen of the device asks for nothing.** With a monitor on HDMI the console shows a status screen (installation ID, name, addresses of the control page, the setup network if there is no network) instead of a `login:` prompt, so nothing has to be typed on a device that is used without a monitor. There is no local login and no root autologin; administer the device over SSH (user `root`, DietPi's password).

## Upgrading a running Pi

```bash
scripts/deploy-to-pi.sh 192.168.1.95        # or user@host; logs in by SSH key: no password, no questions
```

It builds the package, sends it to the Pi and upgrades it there: it keeps `/etc/exxpiano.conf`, backs the old version up first, checks that
the synthesizer and the bridge come back, and rolls back by itself if they do not (`deploy/upgrade-on-pi.sh`, by hand: `deploy/rollback-on-pi.sh`).
The sound banks are not touched.

**No passwords during an upgrade.** The script logs in with your SSH key and never asks for a password or a host-key question. On a device that does not have your key yet (a new card, a reinstalled image) it installs it with `ssh-copy-id`: the password is asked **that one time**, never again. You need a key on your computer (`ls ~/.ssh/*.pub`; if there is none: `ssh-keygen -t ed25519`). If the key cannot be installed, the script falls back to a plain password login (asked once) rather than stopping: nothing is ever asked on the device itself, which has no monitor. DietPi's password is `dietpi` unless the image was built with `--password`. A host key that has CHANGED still stops the script: that is a reason to check, not to ignore.

## Where things live

Everything is in this one folder. Git ignores the large generated parts, so they can never be committed (the pre-commit hook also refuses any
staged file over 5 MB):

| Folder | What | In git |
|---|---|---|
| `pi/`, `tools/`, `scripts/`, `deploy/`, `dietpi/`, `packaging/`, `tests/`, `docs/` | the program, image builder, internal packaging, tests | yes |
| `build/` | built banks (`mech.sf2`, `piano-<preset>.sf2`), the downloaded Salamander in `build/salamander-44k16/`, older banks in `build/archive/` | no |
| `dist/sd/` | the reusable complete SD image and its metadata | no |

## Power supply (read this)

The Pi 3 is very sensitive to its power. Check:

```bash
vcgencmd get_throttled     # must be 0x0
```

Any other value (for example `0x50005`) means the voltage sagged. Effects we saw: clicks in the sound, Wi-Fi dropping
and SSH breaking while copying large files, reboots when a USB device was unplugged. The cure is a **5.1 V / 2.5 A supply
and a short, thick cable** (up to 1 m, 20 AWG wires or thicker, no switch). A long thin cord is the most common cause.

## Audio output and latency

- **Onboard 3.5 mm:** enable `dtparam=audio=on` in `/boot/config.txt` (DietPi: `dietpi-config` -> Audio Options), device
  `plughw:Headphones`. The bcm2835 driver enlarges the buffer period to 444 samples by itself, so the latency is about
  30 ms (with `PERIODS=3`); `PERIODS=2` gives ~20 ms but may click. The default `PERIOD_SIZE=64` x `PERIODS=3` (the smallest value FluidSynth accepts, the driver rounds it up) played without clicks on a Pi 3; raise `PERIOD_SIZE` on the web page if you hear any. The output is somewhat noisy.
- **USB sound card / audio interface:** the best option. Just plug it in: `ALSA_DEVICE=auto` (the default) prefers a USB card and
  uses the smaller buffer `USB_PERIOD_SIZE=64` x `USB_PERIODS=3` (64 frames = 1.45 ms per period, ~4.4 ms of buffering; the interface's own USB and converter delay come on top). The device is opened through ALSA's `plughw` layer, which
  matters: many interfaces accept only 24/32-bit audio or 4 channels, and opening them directly (`hw:`) fails with
  `Failed to find an audio format supported by alsa`.
  Tested: a Zoom U-24 (it reports `S32_LE`, 4 channels, 44.1-96 kHz) on a Pi 3 with `64 x 3`: no clicks by ear, latency not
  measured. The card is picked when the service starts (it waits `USB_WAIT` seconds for a USB card at boot). Re-picking when you
  plug or unplug a card is done by a udev rule that was written from the documentation and **not tested on hardware**;
  if it does not work, run `sudo systemctl restart exxpiano` after changing the interface.

## Configuration

Everything is in `/etc/exxpiano.conf`. After editing: `sudo systemctl restart exxpiano exxpiano-fx`.

| Setting | What it does |
|---|---|
| `GAIN` | overall volume (0.2 is FluidSynth's own default; these banks want 1.0, the mono preset `small` wants 2.0) |
| `POLYPHONY` | number of voices; stereo uses two voices per note, and the mechanics add more |
| `ALSA_DEVICE` | `auto` (USB card if present, otherwise onboard) or an explicit device such as `plughw:U24` |
| `PERIOD_SIZE`, `PERIODS` / `USB_PERIOD_SIZE`, `USB_PERIODS`, `USB_WAIT` | buffer for the onboard output / for a USB card, and how long to wait for it at boot |
| `REVERB` | 1 enables FluidSynth's reverb (higher CPU load) |
| `FX_ARGS` | mechanics options, see below |

### Tuning the mechanics (`FX_ARGS`)

| Option | Default | Meaning |
|---|---|---|
| `--hammer-boost DB` | 0 | hammer-noise level; 0 is as in the SFZ (very quiet), +6 is clearly louder |
| `--hammer-exp N` | 0.8 | dependence of the knock on touch: amplitude ~ (velocity/127)^N. Larger = soft playing is more silent. 0.6-1.0 is gentle, 2.0 is steep |
| `--random R` | 1.0 | randomness of knock and pedal: 0 off, 1 normal, 2 strong. Level varies, and for the hammer the sample of a neighbouring key is picked |
| `--res-boost DB` | 0 | string-resonance level |
| `--pedal-boost DB` | 0 | pedal-noise level |
| `--velocity-curve G` | 1.0 | touch curve for the whole piano: out = 127 * (in/127)^G. 1 = unchanged, below 1 soft touches sound louder, above 1 you must play harder |

## Choosing the piano bank

The page has a **Piano bank** card that lists the sound banks (`*.sf2`, `*.sf3`) found in `BANK_DIR` (default `/opt/exxpiano/soundfonts`; the
mechanics bank is never offered). **Use** switches the piano: the synthesizer reloads (about a minute of silence). If the new bank does not come
up, the previous one is put back automatically and the page says so. A bank is refused, with the reason shown, when it is damaged or too large
for the device's memory (on a Pi 3: about 500 MB). If the bank has several presets the card also offers a **Preset** drop-down (applied at once);
bank 0 programs 1-6 are not offered because the mechanics bank answers there.

If the chosen bank is later missing or damaged (a deleted file), the module starts with `PIANO_SF2_LAST_GOOD`, the last bank that worked,
instead of staying silent. Only file names that the library folder lists can be chosen: the page never sends a path.

**Settings follow the instrument.** The touch curve, master volume, mechanics levels, randomness and the preset are remembered **per bank** (in
`/var/lib/exxpiano/profiles.json`, keyed by the bank's file name, so renaming a file starts it afresh). Change a slider and it is stored for the bank
that is playing; choose another bank and that bank's own settings come back, then back again when you return. A bank that was never played
starts from the settings in force at that moment. The audio output, buffers, polyphony and reverb switch belong to the device and are the same
for every bank. If a switch fails, the old bank and its settings are restored.

To add a bank for now, copy a `.sf2` into `BANK_DIR` (for example `scp -O my.sf2 root@<Pi>:/opt/exxpiano/soundfonts/`); importing from a USB
flash drive is planned (`docs/plans/usb-banks.md`).

## Look at the page without a Raspberry Pi

```bash
python3 scripts/demo-web.py --open            # http://127.0.0.1:8080/
```

The real page and the real server code run on your computer against a **simulated device** (services, CPU and temperature that move, Wi-Fi networks, three banks, the audio
interface). Change the sliders, switch the bank, rename the device, add or forget a Wi-Fi network (a name with "fail" does not connect, "Cafe" is open), scan the QR codes. Options:
`--mode ap` (setup mode: the device with no network), `--number 0007`, `--other-board` (the warning of a card made for another board), `--throttled`,
`--bind 0.0.0.0` (to look from a phone on the same network), `--token SECRET`. Nothing is written outside a temporary folder.

## Web interface

Open `http://<device>:8080/` from a phone or a computer on the same network (the address is printed by `install.sh`; the default
host name on DietPi is shown in your router). The **menu** (the ☰ button) opens the pages: **Home** (the status of the services, what is playing, the volume, and a
glance at the device: CPU, temperature, power, network), **Piano bank**, **Touch and mechanics**, **Audio output**, **Monitor** and **Wi-Fi**. An address like
`http://<device>:8080/#/wifi` opens a page directly (Back works, bookmarks work). The pages have:

- **Touch**: the touch curve with a live graph. It matters because keyboards differ: one reaches velocity 127 with a moderate press,
  another hardly ever passes 90. Move the slider while you play: the change is immediate.
- **Volume** (changes at once, through a root-only pipe: there is no network control port) and the **mechanics** levels (applied within a second).
- **Monitor page**: live, once a second. Throttling and power (under-voltage, frequency cap, temperature limit, now and since the boot), CPU load in total and per core,
  temperature, frequency, memory, the share of a core used by the synthesizer, the mechanics bridge and the web page, and charts of the last 2.5 minutes. Below it, the
  **real parameters of the audio interface** read from the driver while the synthesizer plays: format, channels, sample rate, the period and buffer the driver actually
  chose (the onboard jack rounds 64 up to 444) with the latency in milliseconds, how much is queued, and for a USB interface its clock mode and supported rates.
- **Wi-Fi page**: the connection (network, address, signal), the saved networks (with *Forget*), and **Connect to a network**: pick one from the scan (or type a
  hidden network's name), type the password, press *Connect*. The Pi tries the network for up to a minute and keeps the previous settings if it does not work.
  See *Connecting to a new Wi-Fi* below.
- **Audio output**: the output card, the buffer (size and number) of the selected interface, reverb and polyphony. The buffers are remembered **per audio interface**
  (`/var/lib/exxpiano/devices.json`, keyed by the `ALSA_DEVICE` value): choose another interface and the page fills in the buffers it was
  last used with; an interface never used before keeps the current ones. These need a restart of the synthesizer (~45 s), so they are
  applied with a separate button.
- the status of the services and of the chosen output.

Everything is stored in `/etc/exxpiano.conf`, so the page and the file always agree. The page changes only the whitelisted
settings, each within a fixed range. **There is no password by default**: anybody on your network can change these settings
(nothing else, there is no shell access). Set `WEB_TOKEN=<secret>` in the config to require it, or `WEB_BIND=127.0.0.1` to
reach the page only from the device itself. The service runs as root because it writes `/etc/exxpiano.conf` and restarts
the synthesizer; hardening that is a task for a real product.

## Several devices

At first boot every installation gets a persistent random ID such as `0123456789ABCDEF`. Cards flashed from the same ZIP therefore get different setup networks
`exxpiano-Setup-0123456789ABCDEF` and hostnames `exxpiano-0123456789abcdef`. The ID appears in the menu and page QR code; the page warns if a QR code belongs to another installation. The **Device name** box may set a friendlier hostname.

## Connecting to a new Wi-Fi

When the Pi has **no network** (no Ethernet cable and no Wi-Fi it knows: a new device, a new router, you moved) it starts **its own Wi-Fi network** and waits:

1. On a phone join **`exxpiano-Setup-<16-character ID>`**, password **`exxpianosetup`**, or scan the first QR code shown by the web UI.
2. The phone shows its "sign in to network" window with the page; if not, scan the second QR code or open `http://10.42.0.1:8080/#/wifi`. The **Wi-Fi** page opens by itself.
3. Pick your home network, type its password, press **Connect**. The setup network disappears; join your own Wi-Fi again and open
   `http://<host name>.local:8080/` (the host name is shown on the page; `avahi-daemon` provides it) or find the address in the router's client list.

If the network or the password is wrong, the Pi puts the previous settings back; in setup mode it starts its setup network again and the page says why it failed.
Good to know:

- **The password of the setup network.** It is `exxpianosetup` unless you set another one, and it is the same for every device (only the network name is unique: it ends with the installation ID). Set your own with `WIFI_AP_PASSWORD=...` in `/etc/exxpiano.conf` (8 to 63 ordinary characters; an unusable value is replaced by the default and logged), or for a whole image with `--ap-password` / `SD_AP_PASSWORD` when it is built (see `docs/BUILD-IMAGE.md`). The Wi-Fi page shows the current name and password with the QR code that joins it, so a sticker printed from the page is always right.
- The Pi waits before starting its network: 10 s if it has no saved network, **150 s** if it has some (after a power cut it boots faster than the router).
  With saved networks, an unused setup network is left every 2 minutes to look for them again; a phone connected to it is never thrown out.
- Plugging in an Ethernet cable closes the setup network. Change the name, the password and the times with `WIFI_AP_SSID`, `WIFI_AP_PASSWORD`, `WIFI_GRACE_*`
  in `/etc/exxpiano.conf`; `WIFI_SETUP=0` turns the feature off. **Change the default password before you give a device to somebody**: it protects the
  Wi-Fi password typed during the setup.
- The password of the home network is turned into a key before it is saved (as `wpa_passphrase` does) and is never logged.
- This works where `ifupdown` + `wpa_supplicant` manage the network (DietPi). On **Raspberry Pi OS** with NetworkManager the tab says so; use `sudo nmtui` there.
- **QR codes.** The Wi-Fi page shows a code for the unique setup network, one for the setup page, and one for the current page, with a **Print** button and **Download PNG / SVG** links for each (file names contain the installation ID). **The codes exist only there**: the ID is created at the first start, so the image build makes no QR codes or stickers; take them from the page of the device they belong to and print a sticker. The join code works whenever the setup network is on, so a sticker printed in advance stays valid. The built-in `pi/qrlib.py` creates them without internet or extra packages.
- The setup network starts **10 s** after the Pi finds itself without a network (immediately after you remove the last saved network and no cable is plugged
  in). The **Start the setup network for 5 minutes** button on the Wi-Fi page does it by hand and the Pi comes back to its network by itself. The only network in use
  cannot be forgotten while it is the only way to reach the Pi (no cable, no other saved network).
- Test without risk: `sudo python3 /opt/exxpiano/exxpiano-wifi.py ap-test 120` starts the setup network for 2 minutes and then returns to the normal one by itself.
  (An SSH session over Wi-Fi is cut for that time.)

## How it works

In the SFZ original the mechanics are triggered by `trigger=release` (a key is released) and `on_locc64` (the pedal) rules.
SF2 has no such mechanism, so:

- `tools/build_piano_sf2.py` builds the **piano** without mechanics. Of the 16 velocity layers it keeps a few (chosen by hand,
  spread over the velocity scale), shortens the tails with a smooth fade-out, matches the layer levels and adds a mild
  dependence of volume on velocity. The sensitivity curve (`--gamma`) shifts the ranges so that a soft touch lands in a louder layer;
- `tools/build_mech_sf2.py` builds the **mechanics** as six separate presets (hammer, resonance L/S/V3, pedal down/up).
  A preset's volume is set through the note velocity: attenuation in dB = 60 * (1 - velocity/127);
- `pi/exxpiano-fx.py` reads the keyboards, forwards everything to FluidSynth on channel 0 and, when a key is released or the
  pedal moves, sends the mechanics "notes" on channels 1-6. Levels follow the SFZ rules (the group's base volume,
  `amp_veltrack`, `rt_decay` - attenuation per second the key was held). With the pedal down, the string resonance is
  deferred until the pedal is released.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

They check the mechanics logic (`FXCore`), the structure of the `.deb` packages, and that the SF2 builders accept the
different sample packages (44.1 kHz/16 bit and 48 kHz/24 bit, WAV and FLAC, different folder layouts; this part needs
`ffmpeg` and is skipped without it). No hardware is needed. The ALSA exchange is not covered by tests (checked on a real Pi),
and the audio quality of a built bank was checked by hand through FluidSynth.

## If something does not work

See [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md).

## Licenses

The repository's scripts are MIT ([LICENSE](LICENSE)). The Salamander Grand Piano V3 sounds are CC-BY, by Alexander Holm
([NOTICE.md](NOTICE.md)). The `.sf2` files built from them are derivative works, and distributing them requires
attribution.
