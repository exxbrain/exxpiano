# How exxpiano works

English | [Русский](ABOUT.ru.md)

How the piano and its mechanics are made, why the banks are reduced, what it was tested on in full, and how it compares with other ways to make a Raspberry Pi piano.

## How it works

A digital piano appliance for Raspberry Pi 3/4/5 (64-bit Alpine Linux): SoundFont banks through FluidSynth (the ready images carry **Salamander Grand Piano V3**, Yamaha C5; the mechanics are tuned on it, so with another bank the levels may need adjusting in the web interface), plus
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

**The keyboard is chosen automatically, no pairing or setup.** The bridge looks at the MIDI ports every 2 seconds and takes every hardware MIDI input except service ports (Through, Timer, Announce, FLUID and the like), so any class-compliant USB keyboard works as soon as it is plugged in, several keyboards can play at once, and the bridge reconnects by itself when the synthesizer restarts. Connecting at boot was tested on a Pi with an Arturia KeyLab Essential 49 mk3; plugging in while the Pi runs is built in but not tested.

**The sound output is chosen automatically, to keep the delay low.** If an external USB sound card or audio interface is plugged in, exxpiano plays through it with a very small buffer (64 frames x 3, about 4.4 ms of buffering plus the delay of the interface itself); without one it falls back to the 3.5 mm jack of the board, where the driver raises the buffer to 444 frames (about 30 ms by calculation). The choice is made when the service starts, the buffers are remembered per interface. The delay was not measured end to end; a Zoom U-24 played without clicks on a Pi 3, and on a Pi 5 it was chosen by itself and ran without underruns.

### How the mechanics are made

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

## The Salamander limit and how exxpiano works around it

Salamander Grand Piano V3 is recorded for a computer: 16 velocity layers on every note, long tails, separate mechanics recordings. The compressed download is 412 MB, the unpacked recordings are noticeably larger, and FluidSynth reads a bank into RAM whole. The full set does not suit a cheap board with 512 MB or 1 GB.

So exxpiano does not try to load the original. It builds **reduced SF2 banks** from it: it keeps some of the layers (14 of 16 in `full`, 6 in `lite`), trims the tails with a smooth fade, levels the layers and adds a gentle dependence of volume on touch. The mechanics (hammers, dampers, resonance, pedal) live in a separate small bank, because SF2 cannot express the SFZ rules. The result:

- on a board with **1 GB** `full` (about 700 MB) plays; by ear it sounds better than the earlier 390 MB bank and significantly better than the ready-made light Salamander SoundFont of the official library;
- on the **cheapest Pi 3 A+ with 512 MB** `lite` (about 237 MB) plays, tested on a real board.

The price: fewer velocity layers and shorter tails than the original; `lite` has louder soft notes and a slightly narrower dynamic range. How audible this is has been judged by ear, not measured. `full` was compared by ear with the ready-made light Salamander SoundFont of the official library and sounds significantly better; `full` has not been compared with the original, and our `lite` has not been compared with `full` or with that SoundFont. We have no comparison of the original against the exxpiano banks.

## Tested on

- Raspberry Pi 3 Model B (1 GB; a 512 MB Pi 3 A+ needs the smaller `lite` bank, see [the presets](BUILDING.md#build-the-sound-banks-on-a-maclinux)), the earlier DietPi image (Debian, FluidSynth 2.4.4; 1.10.22 and older), Arturia KeyLab Essential 49 mk3 keyboard,
  onboard 3.5 mm output;
- Raspberry Pi 5 (8 GB) with the `exxpiano-rpi5-…` image of v1.10.21 (DietPi 10.7.2) and a Zoom U-24 USB interface: the card was chosen by itself (`plughw:CARD=U24`, 64 x 3, about 4.4 ms of buffering, 44.1 kHz, `S32_LE`, 4 channels), the level meter runs on it, no underruns or errors since the boot, no under-voltage or throttling, the full bank loaded with 953 of 8046 MB in use. Not checked on it yet: re-picking the card when it is plugged in or out, the delay end to end;
- building the banks: macOS (Apple Silicon), FluidSynth 2.6.1, ffmpeg.

Other boards, keyboards and sound cards were not tested. I recommend a USB sound card (see [Audio output and latency](USING.md#audio-output-and-latency));
its delay was not measured.

**Start time** (measured on a Pi 3 Model B with the full bank): the preview piano plays **5 s** after the synthesizer starts and the full bank **about 37 s** after it (Alpine, a restart of the service). Measured earlier on the DietPi image, without the preview: about 55 s from power-on to the first note, about 1.5 minutes on the first start of a freshly written card. Loading the full bank is reading 729 MB from the card at 23 MB/s, the limit of the Pi 3, so a faster card does not help.

**The preview piano** (`piano-preview.sf2`, 24 MB) is built by the same tool from the same Salamander recordings (CC-BY): 3 velocity layers, stereo at 22.05 kHz, 3 s tails. It is stereo on purpose: a mono bank has the same signal on both channels and vanishes on an input that takes the left minus the right (a TRS cable into a balanced mixer input). It plays while a big bank loads. FluidSynth reads a bank under the lock of the synthesizer it loads into, and every note waits for that lock (31.7 s on a Pi 3 B), so `exxpiano-synth` reads the big bank in a second synthesizer object and moves it over in 0.1 ms: notes waited at most 0.5 ms meanwhile.

## Comparison with alternatives

This comparison comes from project descriptions and reviews, not from my own measurements: I did not measure the sound, latency or resource use of the others. Prices change, check the current ones on the vendors' sites.

| | exxpiano | Pianoteq on a Raspberry Pi | Zynthian | A hand-made FluidSynth on a Pi (FluidPi, PiPiano and the like) |
|---|---|---|---|---|
| Sound | samples of Salamander Grand Piano V3 (CC-BY) through FluidSynth plus a separate mechanics bank (hammers, dampers, resonance, pedal) | physical modelling (Modartt); little memory, the sound is computed live | many engines (piano, electric piano, synths, SoundFont and more) | any SoundFont; no piano mechanics |
| Software price | free, open source (scripts MIT, sounds CC-BY) | a paid licence | the software is open; the ready Zynthian 5 kit with a screen and knobs is about €490 plus VAT without a Raspberry Pi, about €570 with one (from a review) | free |
| Hardware | Raspberry Pi 3/4/5 (a Pi 5 through a USB sound card), a 512 MB board (Pi 3 A+) is enough, no screen or buttons needed | reports mostly name a Pi 4/5 with a 64-bit system; there are reports of home use on a Pi 3 | the official v5.1 kit on a Raspberry Pi 5 with a screen and a control board; Pi 4, 3 and 3B+ are also supported and Pi 4 or better is recommended (a Pi 3 copes worse with several engines at once); the Pi 3 A+ and a minimum amount of memory are not mentioned in the documentation, a forum post says ZynthianOS takes almost 1 GB; a Pi 2 is not supported by the image | any board that runs FluidSynth (Pi Zero and up) |
| Ready image | yes, one ZIP that works from the first power-on with no monitor | no, installed by hand | yes, the ZynthianOS image: about 7 GB compressed and about 16 GB unpacked, a card of 32 GB or more; a lighter variant with a reduced SoundFont library exists | no, usually a guide and scripts |
| Settings from a phone | yes: touch curve, mechanics, banks, audio output, Wi-Fi, monitor | no single web interface; settings are in the program | a screen and knobs on the device, a web interface exists | at best a simple interface to choose a bank |
| Piano mechanics | yes, a separate bank and a bridge | part of the physical model | depends on the chosen engine | none |
| Changing the sound | banks can be swapped (any `.sf2`), mechanics and touch are tunable | many model parameters | depends on the engine | only swapping the SoundFont |

Where exxpiano is weaker: no physical modelling (the sound is limited by recorded layers and the memory of the board, and on a cheap 512 MB board the `lite` bank is simpler than the full one), no screen or knobs on the body (everything from a phone), no built-in synthesizers besides the piano, and the latency is not measured yet. Where it is stronger: free open code, a ready image, work on the cheapest board with no monitor, a clear settings web interface and a separate piano mechanics.

Sources: [Modartt forum on Pianoteq for ARM](https://forum.modartt.com/viewtopic.php?id=7220), [Zynthian Software documentation](https://wiki.zynthian.org/index.php/Zynthian_Software), [Zynthian Hardware documentation](https://wiki.zynthian.org/index.php/Zynthian_Hardware), [forum on memory](https://discourse.zynthian.org/t/how-much-ram-4-vs-8-gb/6109), [Zynthian 5 (review)](https://www.yahoo.com/entertainment/zynthian-5-standalone-open-source-110034729.html), [FluidPi](https://github.com/schollz/FluidPi), [PiPiano](https://jpralves.net/post/2020/03/05/pipiano.html).
