#!/usr/bin/env python3
"""The synthesizer of the piano when a preview bank exists: FluidSynth through libfluidsynth (ctypes), started by exxpiano-run.

It sounds a few seconds after the start: the small preview bank and the mechanics are loaded first, the audio and the MIDI port come up,
and the big piano bank loads meanwhile. FluidSynth reads a SoundFont under the lock of the synthesizer it loads into, and every note-on
waits for that lock (31.7 s for the full bank on a Pi 3 B). So the big bank is read by a second synthesizer object that has no audio and
no MIDI, and the loaded SoundFont is then moved over (remove_sfont + add_sfont, 0.1 ms): notes wait at most 0.5 ms meanwhile.

When the bank is loaded and no key of the piano channel and no sustain pedal is down, the piano channel moves to it and the preview is
unloaded; its sounding notes ring on until their voices end. A new bank is loaded the same way when the web interface writes
`exxpiano-load BANK PROG PATH` into the command pipe (the path last: a file name may hold spaces); every other line goes to FluidSynth's own commands (`set synth.gain 0.8`).

The state is written to /run/exxpiano-bank.json for the web interface: state (preview, ready, failed), playing, loading, error.

  exxpiano-synth.py [-o NAME=VALUE ...] --preview PREVIEW.sf2 --mech MECH.sf2 [--bank B --prog P] [--fallback LAST_GOOD.sf2] PIANO.sf2
"""

import argparse
import ctypes
import json
import os
import sys
import threading
import time

PIANO_CHANNEL = 0
MECH_PRESETS = tuple((0, p) for p in range(1, 7))  # the mechanics answer on bank 0, programs 1..6 (exxpiano-fx.py: PROGRAMS)
TICK = 0.02  # how often the transition looks at the keys and the pedal
PATIENCE = 60.0  # seconds without a moment of no key and no pedal; then the next release of the pedal is enough
MEMORY_WAIT = 20.0  # the most seconds to wait for the memory of the previous bank to come back before the next one loads
STATE_FILE = os.environ.get('EXXPIANO_BANK_STATE', '/run/exxpiano-bank.json')


def log(text):
    print('exxpiano-synth: ' + text, file=sys.stderr, flush=True)


def write_state(path, **state):
    """Atomic: the web interface never reads half a file."""
    state['t'] = time.time()
    state['pid'] = os.getpid()
    tmp = '%s.%d.%d.tmp' % (path, os.getpid(), threading.get_ident())
    try:
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(state, f)
        os.replace(tmp, path)
    except OSError as exc:
        log('could not write %s: %s' % (path, exc))


class Engine:
    """What to load and when to move over. `fs` does the work (FluidSynth below, a fake in the tests)."""

    def __init__(self, fs, state_file=STATE_FILE, clock=time.monotonic, sleep=time.sleep, choose=None, remember=None):
        self.fs, self.state_file, self.clock, self.sleep = fs, state_file, clock, sleep
        self.choose = choose or (lambda piano, mech, preview: piano)  # the bank that fits the memory (see fitting_bank)
        self.remember = remember or (lambda path: None)  # the bank that really plays goes into the settings (see store_piano)
        self.lock = threading.Lock()
        self.job = None
        self.preview = None  # (path, sfont id) while the preview is loaded
        self.main = None  # (path, sfont id, bytes) of the bank the piano channel plays when ready
        self.mech = None  # (path, sfont id)
        self.state = dict(state='preview', playing=None, loading=None, error=None)

    def _publish(self, **changes):
        self.state.update(changes)
        write_state(self.state_file, **self.state)

    # ---- the start
    def start(self, preview, mech, piano, bank, prog, fallback=None):
        """The preview, then the sound at once; the mechanics and the piano bank load in the background. False when the preview does not load."""
        pid = self._load_preview(preview)
        if pid is None:
            return False
        self.fs.start_io()
        log('playing the preview %s; loading %s' % (os.path.basename(preview), piano))
        self._publish(state='preview', playing=preview, loading=piano, error=None)
        self._spawn(self._first_load, preview, mech, piano, bank, prog, fallback)
        return True

    def _first_load(self, preview, mech, piano, bank, prog, fallback):
        """The mechanics first (1.7 s on a Pi 3 B; they come above the preview in the stack, where the bridge finds their presets), then the
        piano bank, or a smaller one when it does not fit the memory next to the mechanics and the preview."""
        handle = self.fs.load_aside(mech)
        if handle is None:
            log('the mechanics bank %s did not load' % mech)
        else:
            self.mech = (mech, self.fs.add(handle))
            self._hold_preview()
        chosen = self.choose(piano, mech, preview)
        if chosen != piano:
            self._publish(state='preview', loading=chosen)
        self._bring_in(chosen, bank, prog, fallback)

    def _load_preview(self, path):
        handle = self.fs.load_aside(path)
        if handle is None:
            log('the preview bank %s did not load' % path)
            return None
        sfid = self.fs.add(handle)
        self.preview = (path, sfid)
        self.fs.select(PIANO_CHANNEL, sfid, *self.fs.first_preset(sfid))
        return sfid

    def _spawn(self, target, *args):
        self.job = threading.Thread(target=self._guarded, args=(target,) + args, daemon=True)
        self.job.start()

    def _guarded(self, target, *args):
        try:
            target(*args)
        except Exception as exc:  # noqa: BLE001 - the preview keeps playing, the journal and the state say why
            log('loading failed: %s' % exc)
            self._publish(state='failed', loading=None, error=str(exc))

    def busy(self):
        return self.job is not None and self.job.is_alive()

    # ---- loading a big bank and moving over to it
    def _bring_in(self, piano, bank, prog, fallback=None):
        t0 = self.clock()
        handle = self.fs.load_aside(piano)
        if handle is None:
            log('%s did not load' % piano)
            if fallback and fallback != piano:
                self._publish(state='preview', loading=fallback, error='%s did not load' % os.path.basename(piano))
                log('loading the last good bank %s instead' % fallback)
                self._bring_in(fallback, bank, prog)
                if self.state.get('state') == 'ready' and self.state.get('playing') == fallback:
                    self.remember(fallback)  # the page shows it, and a later switch rolls back to it, not to the damaged one
            else:
                self._publish(state='failed', loading=None, error='%s did not load' % os.path.basename(piano))
            return
        sfid = self.fs.add(handle)  # FluidSynth gives every channel its preset again by number: the newest bank answers first now
        self._hold_preview()
        self._mechanics_on_top(sfid, os.path.basename(piano))
        log('%s loaded in %.1f s' % (os.path.basename(piano), self.clock() - t0))
        self._move_over(piano, sfid, bank, prog)

    def _mechanics_on_top(self, sfid, name):
        """A bank with presets where the mechanics answer (a General MIDI bank has 0:1 .. 0:6) would take their channels: the bank added last
        answers first. The mechanics are then added again above it (the plain fluidsynth loaded them after the piano for the same reason)."""
        if not self.mech or not set(MECH_PRESETS) & set(self.fs.presets(sfid)):
            return
        handle = self.fs.load_aside(self.mech[0])
        if handle is None:
            log('the mechanics bank %s did not load again: %s keeps their presets' % (self.mech[0], name))
            return
        old = self.mech[1]
        self.mech = (self.mech[0], self.fs.add(handle))
        self._hold_preview()
        self.fs.unload(old)
        log('the mechanics answer above %s again' % name)

    def _move_over(self, piano, sfid, bank, prog):
        """Wait for a moment with no key of the piano and no pedal down, then switch. After PATIENCE a release of the pedal is enough, or
        silence on the piano channel: a pedal can stay down for ever (a player stopped in the middle, a keyboard pulled out)."""
        waited = self.clock()
        while True:
            pedal = self.fs.pedal_down(PIANO_CHANNEL)
            keys = self.fs.keys_down(PIANO_CHANNEL)
            if not pedal and not keys:
                break
            if self.clock() - waited > PATIENCE and (not pedal or not self.fs.sounding(PIANO_CHANNEL)):
                break
            self._hold_preview()
            self.sleep(TICK)
        if not self.fs.select(PIANO_CHANNEL, sfid, bank, prog):
            alt = self.fs.first_preset(sfid)
            log('%s has no preset %d:%d, playing its preset %d:%d' % (os.path.basename(piano), bank, prog, alt[0], alt[1]))
            self.fs.select(PIANO_CHANNEL, sfid, *alt)
        self.main = (piano, sfid, self.fs.size(piano))
        if self.preview:
            self.fs.unload(self.preview[1])  # its sounding notes ring on: FluidSynth frees it after their voices end
            self.preview = None
        log('now playing %s' % os.path.basename(piano))
        self._publish(state='ready', playing=piano, loading=None, error=None)

    def _hold_preview(self):
        """A program change of the bridge (its start, a change of the settings) picks presets by number: on the preview it may land on the
        big bank that is already loaded but not chosen yet, or on nothing. The piano channel stays on the preview until the move."""
        if self.preview and self.fs.channel_sfont(PIANO_CHANNEL) != self.preview[1]:
            self.fs.select(PIANO_CHANNEL, self.preview[1], *self.fs.first_preset(self.preview[1]))

    # ---- a new bank chosen in the web interface
    def load(self, preview, piano, bank, prog):
        """exxpiano-load: preview first, the previous big bank out (and its memory back), then the new one. False when busy."""
        with self.lock:
            if self.busy():
                log('a bank is loading already: %s ignored' % piano)
                return False
            self._publish(state='preview', loading=piano, error=None)
            self._spawn(self._switch, preview, piano, bank, prog)
            return True

    def _switch(self, preview, piano, bank, prog):
        if not self.preview and self._load_preview(preview) is None:
            raise RuntimeError('the preview bank %s did not load' % os.path.basename(preview))
        self._publish(state='preview', playing=preview)
        if self.main:
            old_path, old_id, old_bytes = self.main
            before = self.fs.rss()
            self.fs.unload(old_id)
            self.main = None
            self._wait_memory(before, old_bytes)
            log('%s unloaded' % os.path.basename(old_path))
        self._bring_in(piano, bank, prog)

    def _wait_memory(self, before, freed):
        """The two big banks must not be in the memory together: FluidSynth frees the old one after its last voices end."""
        end = self.clock() + MEMORY_WAIT
        while self.clock() < end:
            if self.fs.rss() <= before - 0.8 * freed:
                return
            self.sleep(0.2)
        log('the previous bank still holds its memory after %d s: silencing the piano channel' % MEMORY_WAIT)
        self.fs.silence(PIANO_CHANNEL)
        self.sleep(1.0)

    # ---- the command pipe
    def command(self, line, preview):
        """True when the line was ours (exxpiano-load); every other line is for FluidSynth."""
        parts = line.strip().split(None, 3)
        if not parts or parts[0] != 'exxpiano-load':
            return False
        try:
            bank, prog, path = int(parts[1]), int(parts[2]), parts[3]
        except (IndexError, ValueError):
            log('usage: exxpiano-load BANK PROG PATH (got %r)' % line.strip())
            return True
        self.load(preview, path, bank, prog)
        return True


class FluidSynth:
    """The calls Engine needs, on libfluidsynth through ctypes."""

    NUM, INT, STR = 0, 1, 2  # fluid_settings_get_type

    def __init__(self, options, lib=None):
        L = self.L = lib or ctypes.CDLL('libfluidsynth.so.3')
        P, I, S = ctypes.c_void_p, ctypes.c_int, ctypes.c_char_p
        for name, res, args in [
            ('new_fluid_settings', P, []),
            ('fluid_settings_get_type', I, [P, S]),
            ('fluid_settings_setstr', I, [P, S, S]),
            ('fluid_settings_setint', I, [P, S, I]),
            ('fluid_settings_setnum', I, [P, S, ctypes.c_double]),
            ('new_fluid_synth', P, [P]),
            ('delete_fluid_synth', None, [P]),
            ('new_fluid_audio_driver', P, [P, P]),
            ('new_fluid_midi_driver', P, [P, P, P]),
            ('new_fluid_cmd_handler2', P, [P, P, P, P]),
            ('fluid_command', I, [P, S, I]),
            ('fluid_synth_sfload', I, [P, S, I]),
            ('fluid_synth_sfunload', I, [P, I, I]),
            ('fluid_synth_get_sfont_by_id', P, [P, I]),
            ('fluid_synth_remove_sfont', I, [P, P]),
            ('fluid_synth_add_sfont', I, [P, P]),
            ('fluid_synth_program_select', I, [P, I, I, I, I]),
            ('fluid_synth_get_program', I, [P, I, ctypes.POINTER(I), ctypes.POINTER(I), ctypes.POINTER(I)]),
            ('fluid_synth_get_cc', I, [P, I, I, ctypes.POINTER(I)]),
            ('fluid_synth_get_polyphony', I, [P]),
            ('fluid_synth_get_voicelist', None, [P, ctypes.POINTER(P), I, I]),
            ('fluid_voice_is_on', I, [P]),
            ('fluid_voice_get_channel', I, [P]),
            ('fluid_synth_all_sounds_off', I, [P, I]),
            ('fluid_sfont_iteration_start', None, [P]),
            ('fluid_sfont_iteration_next', P, [P]),
            ('fluid_preset_get_banknum', I, [P]),
            ('fluid_preset_get_num', I, [P]),
            ('fluid_set_log_function', P, [I, P, P]),
        ]:
            f = getattr(L, name)
            f.restype, f.argtypes = res, args
        self.settings = L.new_fluid_settings()
        for name, value in options:
            self.set(self.settings, name, value)
        self.synth = L.new_fluid_synth(self.settings)
        if not self.synth:
            raise RuntimeError('FluidSynth did not create the synthesizer')
        self.aside = L.new_fluid_settings()  # the loader: no threads of its own, few voices
        self.set(self.aside, 'synth.cpu-cores', '1')
        self.set(self.aside, 'synth.polyphony', '16')
        self.handler = None
        self.drivers = []
        # The loader synthesizer has no SoundFont left once the bank is taken out of it, and FluidSynth then warns for each of its 16
        # channels that it has no preset (also shown on the page as service messages). Only those, and only meanwhile, are dropped.
        self.quiet = False
        self._log = ctypes.CFUNCTYPE(None, ctypes.c_int, ctypes.c_char_p, ctypes.c_void_p)(self._warning)
        L.fluid_set_log_function(2, ctypes.cast(self._log, ctypes.c_void_p), None)  # FLUID_WARN

    def _warning(self, level, message, data):
        text = (message or b'').decode('utf-8', 'replace')
        if self.quiet and text.startswith('No preset found on channel'):
            return
        print('fluidsynth: warning: ' + text, file=sys.stderr, flush=True)

    def set(self, settings, name, value):
        key = name.encode()
        kind = self.L.fluid_settings_get_type(settings, key)
        if kind == self.INT:
            ok = self.L.fluid_settings_setint(settings, key, int(value))
        elif kind == self.NUM:
            ok = self.L.fluid_settings_setnum(settings, key, float(value))
        elif kind == self.STR:
            ok = self.L.fluid_settings_setstr(settings, key, value.encode())
        else:
            raise ValueError('FluidSynth has no setting %s' % name)
        if ok != 0:
            raise ValueError('FluidSynth refused %s=%s' % (name, value))

    def sfload(self, path):
        sfid = self.L.fluid_synth_sfload(self.synth, path.encode(), 0)
        return None if sfid < 0 else sfid

    def load_aside(self, path):
        """Read the bank in a synthesizer of its own (its lock, not the playing one's) and take the SoundFont out of it."""
        helper = self.L.new_fluid_synth(self.aside)
        if not helper:
            return None
        try:
            sfid = self.L.fluid_synth_sfload(helper, path.encode(), 0)
            if sfid < 0:
                return None
            sfont = self.L.fluid_synth_get_sfont_by_id(helper, sfid)
            self.quiet = True
            self.L.fluid_synth_remove_sfont(helper, sfont)  # removed, not freed: it is ours now
            return sfont
        finally:
            self.L.delete_fluid_synth(helper)
            self.quiet = False

    def add(self, sfont):
        return self.L.fluid_synth_add_sfont(self.synth, sfont)

    def unload(self, sfid):
        self.L.fluid_synth_sfunload(self.synth, sfid, 0)  # 0: the channels keep their presets (the mechanics are not touched)

    def select(self, chan, sfid, bank, prog):
        return self.L.fluid_synth_program_select(self.synth, chan, sfid, bank, prog) == 0

    def presets(self, sfid):
        """[(bank, program)] of a SoundFont that is in the synthesizer."""
        found = []
        sfont = self.L.fluid_synth_get_sfont_by_id(self.synth, sfid)
        if sfont:
            self.L.fluid_sfont_iteration_start(sfont)
            preset = self.L.fluid_sfont_iteration_next(sfont)
            while preset:
                found.append((self.L.fluid_preset_get_banknum(preset), self.L.fluid_preset_get_num(preset)))
                preset = self.L.fluid_sfont_iteration_next(sfont)
        return found

    def first_preset(self, sfid):
        sfont = self.L.fluid_synth_get_sfont_by_id(self.synth, sfid)
        if sfont:
            self.L.fluid_sfont_iteration_start(sfont)
            preset = self.L.fluid_sfont_iteration_next(sfont)
            if preset:
                return self.L.fluid_preset_get_banknum(preset), self.L.fluid_preset_get_num(preset)
        return 0, 0

    def channel_sfont(self, chan):
        sfid, bank, prog = ctypes.c_int(-1), ctypes.c_int(), ctypes.c_int()
        self.L.fluid_synth_get_program(self.synth, chan, ctypes.byref(sfid), ctypes.byref(bank), ctypes.byref(prog))
        return sfid.value

    def pedal_down(self, chan):
        v = ctypes.c_int(0)
        self.L.fluid_synth_get_cc(self.synth, chan, 64, ctypes.byref(v))
        return v.value >= 64

    def keys_down(self, chan):
        n = self.L.fluid_synth_get_polyphony(self.synth)
        buf = (ctypes.c_void_p * (n + 1))()
        self.L.fluid_synth_get_voicelist(self.synth, buf, n + 1, -1)
        for voice in buf:
            if not voice:
                break
            if self.L.fluid_voice_get_channel(voice) == chan and self.L.fluid_voice_is_on(voice):
                return True
        return False

    def sounding(self, chan):
        """True while a voice of the channel plays (held, sustained or ringing out)."""
        n = self.L.fluid_synth_get_polyphony(self.synth)
        buf = (ctypes.c_void_p * (n + 1))()
        self.L.fluid_synth_get_voicelist(self.synth, buf, n + 1, -1)  # the list holds the playing voices only
        return any(self.L.fluid_voice_get_channel(v) == chan for v in buf if v)

    def silence(self, chan):
        self.L.fluid_synth_all_sounds_off(self.synth, chan)

    def size(self, path):
        try:
            return os.path.getsize(path)
        except OSError:
            return 0

    def rss(self):
        with open('/proc/self/statm') as f:
            return int(f.read().split()[1]) * os.sysconf('SC_PAGE_SIZE')

    def start_io(self):
        audio = self.L.new_fluid_audio_driver(self.settings, self.synth)
        if not audio:
            raise RuntimeError('FluidSynth could not open the audio output')
        handle = ctypes.cast(self.L.fluid_synth_handle_midi_event, ctypes.c_void_p)  # the notes never pass through Python
        midi = self.L.new_fluid_midi_driver(self.settings, handle, self.synth)
        if not midi:
            raise RuntimeError('FluidSynth could not create the MIDI port')
        self.drivers = [audio, midi]
        self.handler = self.L.new_fluid_cmd_handler2(self.settings, self.synth, None, None)

    def command(self, line):
        if self.handler:
            self.L.fluid_command(self.handler, line.encode(), 1)


def fitting_bank(piano, mech, preview, conf=None, bank_dir=None):
    """The piano bank to load: `piano`, or the largest smaller bank of the library when `piano` does not fit the memory next to the mechanics
    and the preview (a Pi 3 A+ has 512 MB). The choice is remembered in the settings file, so the page shows what really plays."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import banklib

    bank_dir = bank_dir or os.environ.get('BANK_DIR') or '/opt/exxpiano/soundfonts'
    smaller = banklib.fitting_replacement(piano, mech, bank_dir, preview=preview)
    if not smaller or smaller == piano:
        return piano
    log('%s does not fit into the memory of this board, using the smaller bank %s' % (piano, smaller))
    store_piano(smaller, conf)
    return smaller


def store_piano(path, conf=None):
    """PIANO_SF2 in the settings file becomes the bank that really plays. A failure is only logged: the sound goes on."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import exxpianoconf

    try:
        exxpianoconf.apply_updates(None, conf or os.environ.get('EXXPIANO_CONF', exxpianoconf.CONF_PATH), raw={'PIANO_SF2': path})
    except (OSError, ValueError) as exc:
        log('could not store the bank %s in the settings: %s' % (path, exc))


def parse_args(argv):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('-o', dest='options', action='append', default=[], metavar='NAME=VALUE', help='a FluidSynth setting')
    ap.add_argument('--preview', required=True)
    ap.add_argument('--mech', required=True)
    ap.add_argument('--bank', type=int, default=0)
    ap.add_argument('--prog', type=int, default=0)
    ap.add_argument('--fallback', default='')
    ap.add_argument('piano')
    args = ap.parse_args(argv)
    try:
        args.options = [tuple(o.split('=', 1)) for o in args.options]
        if any(len(o) != 2 or not o[0] for o in args.options):
            raise ValueError
    except ValueError:
        ap.error('-o takes NAME=VALUE')
    return args


def set_process_name(name):
    """The name the web interface and the health checks look for (oslib.synth_pid); a plain `python3` would say nothing."""
    try:
        libc = ctypes.CDLL(None)
        libc.prctl(15, name.encode(), 0, 0, 0)  # PR_SET_NAME
    except (OSError, AttributeError):
        pass


def main(argv=None):
    args = parse_args(sys.argv[1:] if argv is None else argv)
    set_process_name('exxpiano-synth')
    fs = FluidSynth(args.options)
    engine = Engine(fs, choose=fitting_bank, remember=store_piano)
    if not engine.start(args.preview, args.mech, args.piano, args.bank, args.prog, args.fallback or None):
        try:  # exxpiano-run then starts fluidsynth without the preview, until the preview file changes
            open(os.environ.get('EXXPIANO_PREVIEW_FAILED', '/run/exxpiano-preview.failed'), 'w').close()
        except OSError:
            pass
        return 1
    stdin = sys.stdin.buffer
    while True:  # the command pipe is held open for reading and writing by exxpiano-run: it never ends
        raw = stdin.readline()
        if not raw:
            time.sleep(1)
            continue
        line = raw.decode('utf-8', 'replace').strip()
        if line and not engine.command(line, args.preview):
            fs.command(line)


if __name__ == '__main__':
    sys.exit(main())
