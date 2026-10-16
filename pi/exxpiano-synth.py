#!/usr/bin/env python3
"""exxpiano-synth: FluidSynth with a fast start. A small starter bank plays within a second of the start, the main bank loads beside it, and
the piano moves to the main bank in the first pause of the playing.

    exxpiano-synth.py --starter STARTER.sf2 -- /usr/bin/fluidsynth -a alsa -m alsa_seq -o name=value ... PIANO.sf2 MECH.sf2

The part after `--` is the fluidsynth command that exxpiano-run would run otherwise: its options are read from it, and when anything goes wrong
before the first sound (no libfluidsynth, an option this program does not know, a starter bank that does not load) that command is run instead.
Like fluidsynth, the program reads shell commands, one per line, on stdin (the root-only pipe of exxpiano-run), for example `set synth.gain 0.8`.

Why not the fluidsynth program itself: a bank loaded into a running synthesizer (the shell's `load`) holds the synthesizer's lock for the whole
load, and every note waits for it (about 33 s for the 729 MB bank on a Pi 3: the keys would be dead and then play all at once). So the main bank
goes into a second synthesizer of the same process, which has its own lock, and the switch only moves the sound card and the MIDI input over:
- the MIDI input is one ALSA sequencer port for the whole run (the bridge sees nothing change); its events go to the synthesizer that plays;
- the switch waits until nothing sounds (no voice on the starter), so no held note is cut and nothing clicks; after QUIET_WAIT seconds of
  continuous playing it fades the starter out over FADE_MS instead;
- what the bridge selected (bank and program of every channel, the pedal and the other controllers) is copied to the main bank at the switch;
- the starter bank has one piano: whatever preset the bridge selects on the piano channel, the starter plays its own piano.

The phase is written to STATE_FILE (JSON {phase, pid, bank}) for the web interface: starter (the small bank plays, the main one is loading),
loaded (the main bank is in memory, waiting for a pause), full (the main bank plays) or failed (the main bank did not load; the starter keeps playing).
"""

import ctypes
import ctypes.util
import json
import os
import queue
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

STATE_FILE = os.environ.get('SYNTH_STATE', '/run/exxpiano-synth.state')
QUIET_WAIT = float(os.environ.get('SYNTH_QUIET_WAIT', '30'))  # seconds of continuous playing after which the switch fades the starter out
FADE_MS = 60
PIANO_CH = 0  # the bridge plays the piano on channel 0 (exxpiano-fx.py)

# fluidsynth options -> settings (the ones exxpiano-run uses; any other option means "run fluidsynth itself")
FLAG_SETTINGS = {
    '-a': 'audio.driver',
    '-m': 'midi.driver',
    '-r': 'synth.sample-rate',
    '-z': 'audio.period-size',
    '-c': 'audio.periods',
    '-R': 'synth.reverb.active',
    '-C': 'synth.chorus.active',
    '-g': 'synth.gain',
}
FLUID_NUM_TYPE, FLUID_INT_TYPE, FLUID_STR_TYPE = 0, 1, 2

NOTE_ON, CONTROL_CHANGE, PROGRAM_CHANGE, PITCH_BEND = 0x90, 0xB0, 0xC0, 0xE0
# controllers not copied to the main bank: bank select (sent again with the program), data entry and (N)RPN numbers (they act on the
# parameter selected before), and the channel mode messages 120..127 (all sound off, reset all controllers, ...)
NOT_COPIED = {0, 6, 32, 38, 96, 97, 98, 99, 100, 101, *range(120, 128)}


def log(msg):
    print('exxpiano: ' + msg, file=sys.stderr, flush=True)


class Unsupported(Exception):
    """The fluidsynth command has an option this program does not handle."""


def parse_fluidsynth_command(cmd):
    """The fluidsynth command of exxpiano-run -> (settings as [(name, value)], soundfont paths). Raises Unsupported."""
    settings, fonts, args = [], [], list(cmd[1:])
    while args:
        a = args.pop(0)
        if a in FLAG_SETTINGS or a == '-o':
            if not args:
                raise Unsupported('%s needs a value' % a)
            value = args.pop(0)
            if a == '-o':
                name, sep, value = value.partition('=')
                if not sep:
                    raise Unsupported('-o %s' % name)
            else:
                name = FLAG_SETTINGS[a]
            settings.append((name, value))
        elif a.startswith('-'):
            raise Unsupported(a)
        else:
            fonts.append(a)
    if len(fonts) != 2:
        raise Unsupported('expected the piano and the mechanics bank, got %d banks' % len(fonts))
    return settings, fonts


def starter_preset(path):
    """(bank, program) of the starter bank's piano: its first preset."""
    import sf2info

    p = sf2info.read_info(path)['presets'][0]
    return p['bank'], p['program']


class Fluid:
    """The few calls of libfluidsynth (2.1 or newer) this program needs."""

    MIDI_HANDLER = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p)

    def __init__(self, path=None):
        lib = ctypes.CDLL(path or ctypes.util.find_library('fluidsynth') or 'libfluidsynth.so.3')
        vp, i, s, d, f = ctypes.c_void_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_double, ctypes.c_float
        ip = ctypes.POINTER(ctypes.c_int)
        for name, res, args in (
            ('new_fluid_settings', vp, []),
            ('fluid_settings_get_type', i, [vp, s]),
            ('fluid_settings_setstr', i, [vp, s, s]),
            ('fluid_settings_setint', i, [vp, s, i]),
            ('fluid_settings_setnum', i, [vp, s, d]),
            ('new_fluid_synth', vp, [vp]),
            ('delete_fluid_synth', None, [vp]),
            ('fluid_synth_sfload', i, [vp, s, i]),
            ('fluid_synth_set_gain', None, [vp, f]),
            ('fluid_synth_get_gain', f, [vp]),
            ('fluid_synth_get_active_voice_count', i, [vp]),
            ('fluid_synth_cc', i, [vp, i, i, i]),
            ('fluid_synth_get_cc', i, [vp, i, i, ip]),
            ('fluid_synth_program_change', i, [vp, i, i]),
            ('fluid_synth_program_select', i, [vp, i, i, i, i]),
            ('fluid_synth_pitch_bend', i, [vp, i, i]),
            ('fluid_synth_get_pitch_bend', i, [vp, i, ip]),
            ('fluid_synth_handle_midi_event', i, [vp, vp]),
            ('fluid_midi_event_get_type', i, [vp]),
            ('fluid_midi_event_get_channel', i, [vp]),
            ('fluid_midi_event_get_control', i, [vp]),
            ('fluid_midi_event_get_value', i, [vp]),
            ('fluid_midi_event_get_program', i, [vp]),
            ('new_fluid_audio_driver', vp, [vp, vp]),
            ('delete_fluid_audio_driver', None, [vp]),
            ('new_fluid_midi_driver', vp, [vp, self.MIDI_HANDLER, vp]),
            ('new_fluid_cmd_handler2', vp, [vp, vp, vp, vp]),
            ('fluid_command', i, [vp, s, i]),
        ):
            fn = getattr(lib, name)
            fn.restype, fn.argtypes = res, args
            setattr(self, name.replace('fluid_', '', 1) if name.startswith('fluid_') else name, fn)

    def apply_settings(self, settings, pairs):
        """-o name=value the way fluidsynth does it: the type of the setting decides how the value is read."""
        for name, value in pairs:
            key = name.encode()
            kind = self.settings_get_type(settings, key)
            if kind == FLUID_NUM_TYPE:
                ok = self.settings_setnum(settings, key, float(value))
            elif kind == FLUID_INT_TYPE:
                ok = self.settings_setint(settings, key, int(value))
            elif kind == FLUID_STR_TYPE:
                ok = self.settings_setstr(settings, key, value.encode())
            else:
                raise Unsupported('unknown setting %s' % name)
            if ok != 0:
                raise Unsupported('setting %s=%s was refused' % (name, value))


class Host:
    """The starter synthesizer, the main one, and the switch between them. `fluid` is a Fluid (or a stand-in in the tests)."""

    def __init__(self, fluid, settings_pairs, starter, piano, mech, state_file=STATE_FILE, quiet_wait=QUIET_WAIT):
        self.f = fluid
        self.pairs = settings_pairs
        self.starter, self.piano, self.mech = starter, piano, mech
        self.state_file = state_file
        self.quiet_wait = quiet_wait
        self.lock = threading.Lock()  # the MIDI thread and the switch: which synthesizer gets the events
        self.settings = self.main_settings = None  # the starter's (also the MIDI input's) and the main synthesizer's
        self.small = self.main = None  # synthesizers
        self.playing = None  # the one that gets the MIDI events and the sound card
        self.adriver = None
        self.programs = {}  # channel -> program the bridge selected (the starter ignores the piano's)
        self.starter_piano = (0, 0)
        self.starter_id = -1
        self.mdriver = None
        self.loaded = threading.Event()
        self.failed = False
        self.commands = queue.Queue()
        self._midi_cb = self.f.MIDI_HANDLER(self.on_midi)  # kept: ctypes must not free the callback while the driver runs

    # ---- state for the web interface
    def set_phase(self, phase):
        tmp = self.state_file + '.tmp'
        try:
            with open(tmp, 'w', encoding='ascii') as fh:
                json.dump(dict(phase=phase, pid=os.getpid(), bank=self.piano), fh)
            os.replace(tmp, self.state_file)
        except OSError as exc:
            log('cannot write %s: %s' % (self.state_file, exc))

    # ---- start
    def start(self):
        """Starter bank + mechanics, the sound card and the MIDI input. Returns False when the starter cannot play (then fluidsynth runs instead)."""
        f = self.f
        self.settings = f.new_fluid_settings()
        f.apply_settings(self.settings, self.pairs)
        self.starter_piano = starter_preset(self.starter)
        self.small = f.new_fluid_synth(self.settings)
        self.starter_id = f.synth_sfload(self.small, self.starter.encode(), 1)
        if self.starter_id < 0 or f.synth_sfload(self.small, self.mech.encode(), 1) < 0:
            log('the starter bank %s did not load' % self.starter)
            f.delete_fluid_synth(self.small)
            self.small = None
            return False
        self.playing = self.small
        self.select_starter_piano()
        self.adriver = f.new_fluid_audio_driver(self.settings, self.small)
        if not self.adriver:
            raise SystemExit('exxpiano: no audio output (the sound card is missing or busy)')
        self.mdriver = f.new_fluid_midi_driver(self.settings, self._midi_cb, None)
        if not self.mdriver:
            log('no MIDI input: the keys will not reach the synthesizer')
        self.set_phase('starter')
        log('the starter bank plays; loading the main bank %s' % self.piano)
        return True

    def select_starter_piano(self):
        self.f.synth_program_select(self.small, PIANO_CH, self.starter_id, *self.starter_piano)

    # ---- MIDI: called by FluidSynth's MIDI thread
    def on_midi(self, _data, event):
        f = self.f
        kind = f.midi_event_get_type(event)
        with self.lock:
            synth = self.playing
            if kind == PROGRAM_CHANGE:
                ch = f.midi_event_get_channel(event)
                self.programs[ch] = f.midi_event_get_program(event)
                if synth is self.small and ch == PIANO_CH:
                    self.select_starter_piano()  # the starter has one piano, whatever the bridge selects
                    return 0
            return f.synth_handle_midi_event(synth, event)

    # ---- the main bank
    def load_main(self):
        """In a thread of its own: the main bank goes into a second synthesizer (its lock is not the one the notes wait for)."""
        f = self.f
        t0 = time.monotonic()
        # settings of its own: a synthesizer takes the live settings (synth.gain of the shell's `set`) for itself, and deleting the starter
        # later takes them away from the settings it was made with
        self.main_settings = f.new_fluid_settings()
        f.apply_settings(self.main_settings, self.pairs)
        main = f.new_fluid_synth(self.main_settings)
        f.synth_set_gain(main, f.synth_get_gain(self.small))
        ok = f.synth_sfload(main, self.piano.encode(), 1) >= 0 and f.synth_sfload(main, self.mech.encode(), 1) >= 0
        if ok:
            self.main = main
            log('the main bank is loaded (%.1f s)' % (time.monotonic() - t0))
            self.set_phase('loaded')
        else:
            f.delete_fluid_synth(main)
            self.failed = True
            log('the main bank %s did not load: the starter bank keeps playing' % self.piano)
            self.set_phase('failed')
        self.loaded.set()

    def wait_for_quiet(self):
        """Until nothing sounds on the starter (two looks in a row), or quiet_wait seconds. True when it is quiet."""
        t0, quiet = time.monotonic(), 0
        while time.monotonic() - t0 < self.quiet_wait:
            quiet = quiet + 1 if self.f.synth_get_active_voice_count(self.small) == 0 else 0
            if quiet >= 2:
                return True
            time.sleep(0.1)
        return False

    def copy_state(self):
        """What the bridge set on the starter, onto the main bank: controllers (the pedal), bank + program, pitch bend. Holds self.lock."""
        f, a, b, v, w = self.f, self.small, self.main, ctypes.c_int(), ctypes.c_int()
        for ch in range(16):
            for num in range(128):
                if num in NOT_COPIED:
                    continue
                if f.synth_get_cc(a, ch, num, ctypes.byref(v)) == 0 and f.synth_get_cc(b, ch, num, ctypes.byref(w)) == 0 and v.value != w.value:
                    f.synth_cc(b, ch, num, v.value)
            if ch in self.programs:
                for num in (0, 32):
                    if f.synth_get_cc(a, ch, num, ctypes.byref(v)) == 0:
                        f.synth_cc(b, ch, num, v.value)
                f.synth_program_change(b, ch, self.programs[ch])
            if f.synth_get_pitch_bend(a, ch, ctypes.byref(v)) == 0 and v.value != 8192:
                f.synth_pitch_bend(b, ch, v.value)

    def switch(self, quiet):
        """The main bank takes the keys and the sound card; the starter goes away."""
        f = self.f
        with self.lock:
            self.copy_state()
            self.playing = self.main  # from here the keys play the main bank (it sounds once its output is open)
        if not quiet:
            gain = f.synth_get_gain(self.small)
            for step in range(9, -1, -1):
                f.synth_set_gain(self.small, gain * step / 10)
                time.sleep(FADE_MS / 10000)
        t0 = time.monotonic()
        f.delete_fluid_audio_driver(self.adriver)
        self.adriver = None
        for _ in range(20):  # the card was just closed; a USB card may take a moment
            self.adriver = f.new_fluid_audio_driver(self.main_settings, self.main)
            if self.adriver:
                break
            time.sleep(0.1)
        if not self.adriver:
            raise SystemExit('exxpiano: the sound card could not be opened again for the main bank')
        gap = (time.monotonic() - t0) * 1000
        with self.lock:
            small, self.small = self.small, None
        f.delete_fluid_synth(small)
        self.set_phase('full')
        log('the main bank plays (%s, %.0f ms without sound)' % ('in a pause' if quiet else 'faded over after %.0f s of playing' % self.quiet_wait, gap))

    # ---- shell commands from the pipe
    def read_commands(self, stream):
        for raw in stream:
            line = raw.strip()
            if line:
                self.apply_gain_to_starter(line)
                self.commands.put(line)

    def apply_gain_to_starter(self, line):
        """`set synth.gain X` reaches the main synthesizer through the settings; the starter, while it plays, gets it here at once."""
        parts = line.split()
        if len(parts) == 3 and parts[0] == 'set' and parts[1] == 'synth.gain':
            try:
                gain = float(parts[2])
            except ValueError:
                return
            with self.lock:
                if self.small is not None:
                    self.f.synth_set_gain(self.small, gain)

    def run_commands(self):
        """The fluidsynth shell commands, on the main synthesizer once it is loaded (on the starter when the main one failed)."""
        self.loaded.wait()
        if self.main is not None:
            handler = self.f.new_fluid_cmd_handler2(self.main_settings, self.main, None, None)
        else:
            handler = self.f.new_fluid_cmd_handler2(self.settings, self.small, None, None)
        while True:
            line = self.commands.get()
            self.f.command(handler, line.encode(), 1)

    def run(self, stdin=sys.stdin):
        threading.Thread(target=self.read_commands, args=(stdin,), daemon=True).start()
        threading.Thread(target=self.run_commands, daemon=True).start()
        threading.Thread(target=self.load_main, daemon=True).start()
        self.loaded.wait()
        if not self.failed:
            self.switch(self.wait_for_quiet())
        while True:
            time.sleep(3600)


def set_process_name(name):
    try:
        ctypes.CDLL(None).prctl(15, name.encode(), 0, 0, 0)  # PR_SET_NAME: what /proc/<pid>/comm, top and the Monitor show
    except (OSError, AttributeError):
        pass


def run_fluidsynth(cmd, why):
    log('%s: running fluidsynth without the starter bank' % why)
    sys.stderr.flush()
    os.execv(cmd[0], cmd)


def main(argv):
    if len(argv) < 4 or argv[0] != '--starter' or '--' not in argv:
        print(__doc__.split('\n\n')[1], file=sys.stderr)
        return 2
    starter = argv[1]
    cmd = argv[argv.index('--') + 1 :]
    try:
        pairs, (piano, mech) = parse_fluidsynth_command(cmd)
        fluid = Fluid()
    except (Unsupported, OSError, AttributeError) as exc:
        run_fluidsynth(cmd, str(exc))
    set_process_name('exxpiano-synth')
    host = Host(fluid, pairs, starter, piano, mech)
    try:
        started = host.start()
    except (Unsupported, OSError, ValueError, IndexError) as exc:  # IndexError: a starter bank without presets
        started, why = False, str(exc)
    else:
        why = 'the starter bank did not load'
    if not started:
        run_fluidsynth(cmd, why)
    host.run()
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
