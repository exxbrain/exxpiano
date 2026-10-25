# Using exxpiano: the details

English | [Русский](USING.ru.md)

The [README](../README.md) covers what a player needs to start. This page has the rest: the web interface section by section, the piano banks, the USB drive, Wi-Fi, the audio output and the settings file.

## Interface for controlling the instrument and monitoring the device

This is the web interface for controlling the instrument and monitoring the device: instrument banks, touch and mechanics, audio output, Wi-Fi, and the state of the board (processor, temperature, power, memory, errors). It opens in a browser, so no program is needed on the phone. Open `http://<device>:8080/` from a phone or a computer on the same network (the address is shown on the device's HDMI screen and in your router's list of clients; the name is `exxpiano-<ID>`). The **menu** (the ☰ button) opens the sections: **Home** (the status of the services, what is playing, the volume, and a
glance at the device: CPU, temperature, power, network), **Piano bank**, **MIDI player**, **Touch and mechanics**, **Audio output**, **Monitor**, **Wi-Fi** and **About** (the links, the version and the licenses of the code, the sounds and the demonstration recordings; a copyright line with a link to it is at the bottom of every section). An address like
`http://<device>:8080/#/wifi` opens a section directly (Back works, bookmarks work). The sections have:

The pictures of every section are in the README, under [What it looks like](../README.md#what-it-looks-like).


- **Touch**: the touch curve with a live graph. It matters because keyboards differ: one reaches velocity 127 with a moderate press,
  another hardly ever passes 90. Move the slider while you play: the change is immediate.
- **Volume** (changes at once, through a root-only pipe: there is no network control port) and the **mechanics** levels (applied within a second).
  The 3.5 mm jack of the card starts at the full volume of its driver (+4 dB): the first start of a new card sets it, the card volume
  slider turns it down and is remembered. At +4 dB the loudest passages can clip at the jack, after the level meter, so the meter does not show it.
- **Monitor section**: live, once a second. **Service messages**: warnings and errors the piano services logged since the boot (for example "no sound card found"), the notes of the piano's own scripts (grey: which audio output was chosen, that a service started; not the whole log), and the programs the kernel killed for lack of memory ("out of memory: the kernel killed fluidsynth ..."). Throttling and power (under-voltage, frequency cap, temperature limit, now and since the boot), CPU load in total and per core,
  temperature, frequency, memory, the share of a core used by the synthesizer, the mechanics bridge and the web interface, and charts of the last 2.5 minutes. Below it, the
  **real parameters of the audio interface** read from the driver while the synthesizer plays: format, channels, sample rate, the period and buffer the driver actually
  chose (the onboard jack rounds 64 up to 444) with the latency in milliseconds, how much is queued, and for a USB interface its clock mode and supported rates.
- **Wi-Fi section**: the connection (network, address, signal), the saved networks (with *Forget*), and **Connect to a network**: pick one from the scan (or type a
  hidden network's name), type the password, press *Connect*. The Pi tries the network for up to a minute and keeps the previous settings if it does not work.
  See *Connecting to a new Wi-Fi* below.
- **Effects**: the reverb of the piano and its mechanics: on or off, the amount (how much of the piano goes into the room), the room size and the damping of the
  tail. Changes apply at once, without a restart. The reverb adds a steady load to the processor, even when no key is down (on a Pi 3 B the synthesizer works about twice as hard: the
  audio thread from 18 % to 33 % of a core); if the sound clicks with it, raise the number of buffers.
- **Audio output**: the output card, the buffer (size and number) of the selected interface and polyphony. The buffers are remembered **per audio interface**
  (`/var/lib/exxpiano/devices.json`, keyed by the `ALSA_DEVICE` value): choose another interface and the web interface fills in the buffers it was
  last used with; an interface never used before keeps the current ones. These need a restart of the synthesizer (~45 s), so they are
  applied with a separate button.
- the status of the services and of the chosen output.

Everything is stored in `/etc/exxpiano.conf`, so the web interface and the file always agree. The web interface changes only the whitelisted
settings, each within a fixed range. **There is no password by default**: anybody on your network can use the whole web API:
these settings, but also importing and deleting banks and changing the Wi-Fi (there is no shell access). Set `WEB_TOKEN=<secret>` in the config to require it, or `WEB_BIND=127.0.0.1` to
reach the web interface only from the device itself. The service runs as root because it writes `/etc/exxpiano.conf` and restarts
the synthesizer; hardening that is a task for a real product.

## Choosing the piano bank

The web interface has a **Piano bank** card that lists the sound banks (`*.sf2`, `*.sf3`) found in `BANK_DIR` (default `/opt/exxpiano/soundfonts`; the
mechanics bank and the preview bank are never offered). **Use** switches the piano without restarting the synthesizer: the preview piano plays while
the new bank loads, so you can keep playing, and the piano moves over to the new bank at a moment with no key and no pedal down (after 60 s of
uninterrupted playing, at the next release of the pedal). If the new bank does not come
up, the previous one is put back automatically and the web interface says so. Without a usable preview bank (`PREVIEW_SF2` empty, missing or damaged)
the synthesizer restarts instead and the piano is silent while the bank loads (about a minute); the card says so. A bank is refused, with the reason shown, when it is damaged or too large
for the device's memory (on a Pi 3: about 500 MB). If the bank has several presets the card also offers a **Preset** drop-down (applied at once);
bank 0 programs 1-6 are not offered because the mechanics bank answers there.

The card also shows the memory: how much the device has, the largest bank that fits (the system keeps a reserve, and the mechanics bank takes its share), the free space on the card, a bar with the current bank and, for every bank, its share of that limit. **Delete** removes a bank that is not in use, an installed one as well as an imported one, to free space: the current bank, the last good one and the mechanics bank are never deleted, nothing is deleted while a bank is switching, and the question warns that it cannot be undone (an installed bank comes back only by installing it again or writing a new card). Not tried on a real Raspberry Pi: pressing **Delete** there; the rules are covered by automatic tests.

If the chosen bank is later missing or damaged (a deleted file), the module starts with `PIANO_SF2_LAST_GOOD`, the last bank that worked,
instead of staying silent. Only file names that the library folder lists can be chosen: the web interface never sends a path.

**Settings follow the instrument.** The touch curve, master volume, mechanics levels, randomness and the preset are remembered **per bank** (in
`/var/lib/exxpiano/profiles.json`, keyed by the bank's file name, so renaming a file starts it afresh). Change a slider and it is stored for the bank
that is playing; choose another bank and that bank's own settings come back, then back again when you return. A bank that was never played
starts from the settings in force at that moment. The audio output, buffers, polyphony and the effects belong to the device and are the same
for every bank. If a switch fails, the old bank and its settings are restored.

To add a bank, copy a `.sf2` into `BANK_DIR` (for example `scp my.sf2 root@<Pi>:/opt/exxpiano/soundfonts/`).

### Importing banks from a USB drive

You do not need SSH to add a bank. Put SoundFont files (`.sf2`, `.sf3`) into the folder `piano-banks/` in the root of a USB drive (FAT32, exFAT or ext4), plug the drive into the Pi and open the **Piano bank** section of the web interface: the card **Banks from a USB drive** lists the files. **Import** copies the chosen file into the device's own library (`/var/lib/exxpiano/banks/`); the copy starts only when you press the button, never by itself. The drive is not needed afterwards: the imported bank stays after unplugging and rebooting, shows up in the list marked "imported" and can be removed with **Delete** (the bank in use and the last working bank cannot be deleted).

How it is kept safe: the drive is mounted read only (`ro,nosuid,nodev,noexec`) and its files are never executed; only plain files of `piano-banks/` are offered (no links, no sub-folders, no hidden files, no `mech.sf2`); the browser never sends a path, only the name of a file the drive has just listed; the copy goes to a temporary folder, is checked (the SoundFont, the size against the memory of the board, the free space) and only then published in one step, so a drive pulled out in the middle or a full card leaves no half-copied bank. One import runs at a time.

**What is tested and what is not.** The logic (listing, safety of names, the transaction, errors, the API, the interface in the demo `scripts/demo-web.py`) is covered by automatic tests. **Not tried on a real Raspberry Pi:** mounting FAT32, exFAT and ext4 drives, the eudev rule (`90-exxpiano.rules`), pulling a drive out during the copy, and what a 400 MB copy does to playing and to the power supply (watch the Monitor section; use a powered hub if it shows under-voltage). Do not play while a big bank is being copied. If a drive is not offered, look at `grep exxpiano-usb /var/log/messages` and check that the file system is one of the three. NTFS is not supported. `bank.json` (author and licence of a bank) is not read yet.

### Playing MIDI files from a USB drive

Put MIDI files (`.mid`, `.midi`) into the folder `piano-midi/` in the root of a USB drive (the same drive as for the banks, FAT32, exFAT or ext4) and open the **MIDI player** section of the web interface (its own item in the menu): it lists them. **Import** copies the chosen file into the device's own library (`/var/lib/exxpiano/midi/`), only when you press the button; the copy is a transaction like the bank import (a temporary file, a check, one atomic publication), so a drive pulled out or a full card leaves no half-copied file. After that the drive is not needed. **Play** plays the file on **the piano you have chosen** (the current bank and preset), with the hammer, damper, resonance and pedal noises of the mechanics and with your touch curve, exactly as if you played it; **Stop** silences it at once; a slider and the **−10 s / +10 s** buttons move to any moment of the piece without stopping it (the notes that sound at that moment are let go, the pedal gets the state the file has there), and the time shows elapsed / total / left; **Repeat** plays it again and again. You can play the keyboard over it: the sounds mix. The piece goes on when you open another section (the Monitor, say): the player is part of the mechanics bridge, not of the page, and the Home section shows what is playing and where.

**Two pieces come with the device.** The package and the images carry two recordings of a real pianist, Liszt's *Un Sospiro* and *La Campanella*, played at the International Piano-e-Competition (`assets/midi/`), and put them into the library once, at the first installation; a piece you delete does not come back with an update. The recordings are from the MAESTRO dataset (Google, taken unchanged) and are licensed **CC BY-NC-SA 4.0**: non-commercial use, with attribution, shared under the same license; the rest of exxpiano stays MIT. If you use exxpiano commercially, delete the pieces (the **Delete** button of the MIDI player). Details: `assets/midi/NOTICE.md`.

What the file may do: only its notes and its sustain pedal (CC 64) are played, on the one piano, all channels together. Everything else in the file is ignored: bank and program changes, volume and pan, pitch bend and the drum channel (channel 10), so a file never changes your instrument or your settings. The tempo is the file's own (no tempo or key controls yet). Restarting the synthesizer stops the piece (so does changing the bank when there is no preview bank); start it again after the new instrument has loaded. With the preview a change of the bank does not restart the synthesizer and the piece should go on (not tried with the player on a device yet). Limits against a file that is too heavy for a Raspberry Pi 3: at most 4 MB, 30 minutes and 200 000 events; a file that is not a standard MIDI file of format 0 or 1 is refused with the reason.

**What is tested and what is not.** The reader of the files, the library, the import, the player (with a fake clock), the road through the touch curve and the mechanics, and the API are covered by automatic tests, and the interface works in the demo. **Tried on a real Raspberry Pi** with the two pieces that come with it (see [What it sounds like](../README.md#what-it-sounds-like)): they play through to the end, the load average stayed at 0.3-0.7, and the final fortissimo of *La Campanella* needed 256 voices (with 192 the synthesizer cut sounding notes, which clicked). **Not measured:** how exact the timing is (Python timers, together with the mechanics), files denser than these two, and everything about the USB drive that the bank import also leaves open. The player lives in the mechanics bridge (`exxpiano-fx`): when it is not running, the card says so.

## Connecting to a new Wi-Fi

When the Pi has **no network** (no Ethernet cable and no Wi-Fi it knows: a new device, a new router, you moved) it starts **its own Wi-Fi network** and waits:

1. On a phone join **`exxpiano-Setup-<6-character ID>`**, password **`exxpianosetup`**, or scan the first QR code shown by the web UI.
2. The phone shows its "sign in to network" window with the web interface; if not, scan the second QR code or open `http://10.42.0.1:8080/#/wifi`. The **Wi-Fi** section opens by itself.
3. Pick your home network, type its password, press **Connect**. The setup network disappears; join your own Wi-Fi again and open
   `http://<host name>.local:8080/` (the host name is shown in the web interface; `avahi-daemon` provides it) or find the address in the router's client list.

If the network or the password is wrong, the Pi puts the previous settings back; in setup mode it starts its setup network again and the web interface says why it failed.
Good to know:

- **The password of the setup network.** It is `exxpianosetup` unless you set another one, and it is the same for every device (only the network name is unique: it ends with the installation ID). Set your own with `WIFI_AP_PASSWORD=...` in `/etc/exxpiano.conf` (8 to 63 ordinary characters; an unusable value is replaced by the default and logged), or for a whole image with `--ap-password` / `SD_AP_PASSWORD` when it is built (see [BUILD-IMAGE.md](BUILD-IMAGE.md)). The Wi-Fi section shows the current name and password with the QR code that joins it, so a sticker printed from the web interface is always right.
- The Pi waits before starting its network: 10 s if it has no saved network, **150 s** if it has some (after a power cut it boots faster than the router).
  With saved networks, an unused setup network is left every 2 minutes to look for them again; a phone connected to it is never thrown out.
- A wired connection is another way in, and it closes the setup network when a cable is plugged in: the board's own network port, and also a USB network adapter or a USB hub with a network port (an interface that nothing else configures gets its address by DHCP when a cable is plugged in). **A USB adapter has not been tried on a real Pi.**
- Plugging in an Ethernet cable closes the setup network. Change the name, the password and the times with `WIFI_AP_SSID`, `WIFI_AP_PASSWORD`, `WIFI_GRACE_*`
  in `/etc/exxpiano.conf`; `WIFI_SETUP=0` turns the feature off. **Change the default password before you give a device to somebody**: it protects the
  Wi-Fi password typed during the setup.
- The password of the home network is turned into a key before it is saved (as `wpa_passphrase` does) and is never logged.
- `exxpiano-wifi` runs `wpa_supplicant` and `udhcpc` on `wlan0` itself; the system's `ifupdown-ng` only looks after the wired port. Where another network manager owns Wi-Fi (NetworkManager, iwd or connman runs) the Wi-Fi section says so and changes nothing.
- **QR codes.** The Wi-Fi section shows a code for the unique setup network, one for the web interface in the setup network, and one for the current address, with a **Print** button and **Download PNG / SVG** links for each (file names contain the installation ID). **The codes exist only there**: the ID is created at the first start, so the image build makes no QR codes or stickers; take them from the web interface of the device they belong to and print a sticker. The join code works whenever the setup network is on, so a sticker printed in advance stays valid. The built-in `pi/qrlib.py` creates them without internet or extra packages.
- The setup network starts **10 s** after the Pi finds itself without a network (immediately after you remove the last saved network and no cable is plugged
  in). The **Start the setup network for 5 minutes** button in the Wi-Fi section does it by hand and the Pi comes back to its network by itself.
- **Without a saved network the setup network is on for 5 minutes** after every start and after the last network is forgotten; while a phone is connected to it
  it stays. Then the Wi-Fi is off and the box is on no network at all, until the next start, a cable, or a network saved through the cable. Change the time
  with `WIFI_SETUP_WINDOW` (seconds, 60 to 3600).
- **Taking the box off the network.** The network in use can be forgotten even when it is the only way to reach the Pi (no cable, no other saved network):
  the page asks first and says what follows. The Pi leaves the network, keeps its setup network on for 5 minutes, and then turns the Wi-Fi off. To come back,
  restart it (5 minutes of the setup network again) or plug in a cable. The piano plays all the time.
- Test without risk (as root): `python3 /opt/exxpiano/exxpiano-wifi.py ap-test 120` starts the setup network for 2 minutes and then returns to the normal one by itself.
  (An SSH session over Wi-Fi is cut for that time.)

## Several devices

At first boot every installation gets a persistent random ID such as `012ABC`. Cards flashed from the same ZIP therefore get different setup networks
`exxpiano-Setup-012ABC` and hostnames `exxpiano-012abc`. The ID appears in the menu and the QR code of the web interface; the web interface warns if a QR code belongs to another installation. The **Device name** box may set a friendlier hostname.

## The screen of the device

**The screen of the device asks for nothing.** With a monitor on HDMI the console shows a status screen (installation ID, name, addresses of the web interface, the setup network if there is no network) instead of a `login:` prompt, so nothing has to be typed on a device that is used without a monitor. There is no local login and no root autologin; administer the device over SSH as `root`: with the password the image was built with, or, without one, with a key (put your public key into `authorized_keys` on the boot partition of the card, or build with `--ssh-key`).

## Audio output and latency

- **Onboard 3.5 mm:** the product image turns it on (`dtparam=audio=on` in `/boot/usercfg.txt`), device
  `plughw:Headphones`. The bcm2835 driver enlarges the buffer period to 444 samples by itself, so the latency is about
  30 ms (with `PERIODS=3`); `PERIODS=2` gives ~20 ms but may click. The default `PERIOD_SIZE=64` x `PERIODS=3` (the smallest value FluidSynth accepts, the driver rounds it up) played without clicks on a Pi 3; raise `PERIOD_SIZE` in the web interface if you hear any. The output is somewhat noisy.
- **USB sound card / audio interface:** the best option. Just plug it in: `ALSA_DEVICE=auto` (the default) prefers a USB card and
  uses the smaller buffer `USB_PERIOD_SIZE=64` x `USB_PERIODS=3` (64 frames = 1.45 ms per period, ~4.4 ms of buffering; the interface's own USB and converter delay come on top). The device is opened through ALSA's `plughw` layer, which
  matters: many interfaces accept only 24/32-bit audio or 4 channels, and opening them directly (`hw:`) fails with
  `Failed to find an audio format supported by alsa`.
  Tested: a Zoom U-24 (it reports `S32_LE`, 4 channels, 44.1-96 kHz) on a Pi 3 with `64 x 3`: no clicks by ear, latency not
  measured; on a Pi 5 the same interface was picked by itself and ran with `64 x 3` without underruns. The card is picked when the service starts (it waits `USB_WAIT` seconds for a USB card at boot). Re-picking when you
  plug or unplug a card is done by an eudev rule that was written from the documentation and **not tested on hardware**;
  if it does not work, run `rc-service exxpiano restart` after changing the interface.

## Configuration

Everything is in `/etc/exxpiano.conf`. After editing: `rc-service exxpiano restart; rc-service exxpiano-fx restart`.

| Setting | What it does |
|---|---|
| `GAIN` | overall volume of the synthesizer (0.2 is FluidSynth's own default; **0.6** is the loudest value at which the densest piece tried does not clip; the slider of the web interface is a dB scale around it, and only its last part can distort) |
| `POLYPHONY` | number of voices (default 256, the most); stereo uses two voices per note, and the mechanics add more |
| `METER` | 1 = the output goes through the level meter: the **Home** section shows the peak of the left and the right channel and counts the samples at full scale (clipping). It costs about 3% of one core; 0 plays to the card directly |
| `CPU_CORES` | synthesis threads of FluidSynth (1-4, default 3; one core always stays free): the voices of a dense piece are shared between the cores of the board, so a higher `POLYPHONY` (192 or 256) becomes possible (also in the **Audio output** section) |
| `ALSA_DEVICE` | `auto` (USB card if present, otherwise onboard) or an explicit device such as `plughw:U24` |
| `PERIOD_SIZE`, `PERIODS` / `USB_PERIOD_SIZE`, `USB_PERIODS`, `USB_WAIT` | buffer for the onboard output / for a USB card, and how long to wait for it at boot |
| `REVERB` | 1 enables FluidSynth's reverb (higher CPU load; also in the **Effects** section, applied without a restart) |
| `REVERB_SEND` | how much of the piano and the mechanics goes into the reverb, 0-127 = 0-50 % (default 64, a quarter; set by the bridge through the SoundFont NRPN, CC91 alone reaches only 20 %) |
| `REVERB_ROOM`, `REVERB_DAMP`, `REVERB_WIDTH`, `REVERB_LEVEL` | the room of the reverb, each 0-1: size (default 0.6), damping of the high frequencies (0.4), stereo width (0.8), output level (0.7) |
| `FX_ARGS` | mechanics options, see below |

### Tuning the mechanics (`FX_ARGS`)

| Option | Default | Meaning |
|---|---|---|
| `--hammer-boost DB` | 1.5 | hammer-noise level; 0 is as in the SFZ (very quiet), +6 is clearly louder |
| `--hammer-exp N` | 0.8 | dependence of the knock on touch: amplitude ~ (velocity/127)^N. Larger = soft playing is more silent. 0.6-1.0 is gentle, 2.0 is steep |
| `--random R` | 1.6 | randomness of knock and pedal: 0 off, 1 normal, 2 strong. Level varies, and for the hammer the sample of a neighbouring key is picked |
| `--res-boost DB` | 0 | string-resonance level |
| `--pedal-boost DB` | 4.5 | pedal-noise level |
| `--velocity-curve G` | 1.0 | touch curve for the whole piano: out = 127 * (in/127)^G. 1 = unchanged, below 1 soft touches sound louder, above 1 you must play harder |
| `--res-floor DBFS` | -64 | string-resonance voices quieter than this are not started: they are 35-70 dB under the piano and only use up the synthesizer's voices (a dense piece with the pedal needs hundreds). -200 = start all |
| `--res-burst N` | 0 | when the pedal lifts, at most N resonance voices (the loudest) start, the others give their power back as level; 0 = no limit, 10 is a strong limit |
| `--res-makeup DB` | 6 | the most that a limited burst gives back as level |
