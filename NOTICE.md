# Attribution

This repository contains no sounds. The scripts `tools/build_*.py` build the sound banks from
**Salamander Grand Piano V3** (Yamaha C5) by **Alexander Holm**, licensed under **Creative Commons Attribution (CC-BY)**.
The original files are published at https://freepats.zenvoid.org/Piano/SalamanderGrandPiano/ (the license text is there too).

The `.sf2` files made by these scripts are derivative works. If you distribute them, credit:

> Salamander Grand Piano V3 by Alexander Holm, CC-BY. Converted to SF2 (reduced number of velocity layers and
> tail length, level-matched).

The following parts of the set are used: the note samples (`A0v1.wav` ...), the key-release noises (`rel*.wav`),
the string resonance (`harm*.wav`) and the pedal sounds (`pedal*.wav`). The tuning corrections come from
`SalamanderGrandPianoV3Retuned.sfz` (Markus Fiedler).

## The demonstration MIDI recordings

The package and the images carry two recordings of piano performances, `assets/midi/liszt-un-sospiro-2011.mid` and
`assets/midi/liszt-la-campanella-2013.mid` (Liszt, played at the International Piano-e-Competition on a Yamaha Disklavier), taken unchanged
from the **MAESTRO dataset v3.0.0** (Google LLC, https://magenta.tensorflow.org/datasets/maestro), licensed under **CC BY-NC-SA 4.0**:
non-commercial use only, with attribution, shared under the same license. That license covers these files, not the MIT code. See
`assets/midi/NOTICE.md`; delete the files if you use exxpiano commercially.

## The system and the libraries in the image

The SD card image is made of **Alpine Linux** packages (each under its own license, listed in `/usr/share/licenses` and by `apk info -L`) and of the
exxpiano packages. Two Python libraries that Alpine does not carry are packaged unchanged from their releases: **mido** (MIT,
https://github.com/mido/mido) and **python-rtmidi** with the bundled RtMidi (MIT, https://github.com/SpotlightKid/python-rtmidi).
