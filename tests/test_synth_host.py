"""Tests for pi/exxpiano-synth.py: the starter bank plays at once, the main bank loads beside it without holding the keys up, and the piano
moves over in a pause with what the bridge selected. The FluidSynth tests play through the real libfluidsynth into /dev/null (no sound card
needed); they are skipped where the library is not installed.

Run from the repository root:   python3 -m unittest discover -s tests -v
"""

import ctypes
import ctypes.util
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(ROOT, 'pi'))
sys.path.insert(0, os.path.join(ROOT, 'tests'))
from sf2fixture import make_playable_sf2  # noqa: E402

SYNTH = os.path.join(ROOT, 'pi', 'exxpiano-synth.py')
RUN = os.path.join(ROOT, 'pi', 'exxpiano-run')
spec = importlib.util.spec_from_file_location('exxpiano_synth', SYNTH)
es = importlib.util.module_from_spec(spec)
spec.loader.exec_module(es)

# the command exxpiano-run builds (DRY_RUN output of the onboard jack)
CMD = [
    *('/usr/bin/fluidsynth', '-a', 'alsa', '-m', 'alsa_seq', '-o', 'audio.alsa.device=plughw:CARD=Headphones', '-o', 'audio.sample-format=16bits'),
    *('-r', '44100', '-z', '64', '-c', '3', '-R', '0', '-C', '0', '-o', 'synth.polyphony=256', '-o', 'synth.cpu-cores=3', '-g', '0.6', '/p.sf2', '/m.sf2'),
]


def have_fluidsynth():
    try:
        es.Fluid()
        return True
    except (OSError, AttributeError):
        return False


class ParseTests(unittest.TestCase):
    def test_the_command_of_exxpiano_run_becomes_settings(self):
        pairs, fonts = es.parse_fluidsynth_command(CMD)
        self.assertEqual(fonts, ['/p.sf2', '/m.sf2'])
        d = dict(pairs)
        self.assertEqual(d['audio.driver'], 'alsa')
        self.assertEqual(d['midi.driver'], 'alsa_seq')
        self.assertEqual(d['audio.alsa.device'], 'plughw:CARD=Headphones')
        self.assertEqual((d['audio.period-size'], d['audio.periods']), ('64', '3'))
        self.assertEqual((d['synth.reverb.active'], d['synth.chorus.active'], d['synth.gain']), ('0', '0', '0.6'))
        self.assertEqual((d['synth.polyphony'], d['synth.cpu-cores'], d['synth.sample-rate']), ('256', '3', '44100'))

    def test_an_unknown_option_means_fluidsynth_itself(self):
        for extra in (['-K', '32'], ['-o', 'novalue'], ['/third.sf2']):
            with self.assertRaises(es.Unsupported):
                es.parse_fluidsynth_command([*CMD[:-2], *extra, '/p.sf2', '/m.sf2'])

    def test_anything_wrong_before_the_first_sound_runs_the_fluidsynth_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake, got = os.path.join(tmp, 'fake-fluidsynth'), os.path.join(tmp, 'got')
            with open(fake, 'w') as f:
                f.write('#!/bin/bash\necho "$@" > "%s"\n' % got)
            os.chmod(fake, 0o755)
            r = subprocess.run(
                [sys.executable, SYNTH, '--starter', '/s.sf2', '--', fake, '-K', '32', '/p.sf2', '/m.sf2'], capture_output=True, text=True, timeout=20
            )
            self.assertEqual(r.returncode, 0, r.stderr)
            with open(got) as f:
                self.assertEqual(f.read().split(), ['-K', '32', '/p.sf2', '/m.sf2'])
            self.assertIn('running fluidsynth without the starter bank', r.stderr)


class Events:
    """MIDI events of libfluidsynth, the way its MIDI thread hands them to the host."""

    def __init__(self):
        lib = ctypes.CDLL(ctypes.util.find_library('fluidsynth') or 'libfluidsynth.so.3')
        vp, i = ctypes.c_void_p, ctypes.c_int
        for name, res, args in (
            ('new_fluid_midi_event', vp, []),
            ('fluid_midi_event_set_type', i, [vp, i]),
            ('fluid_midi_event_set_channel', i, [vp, i]),
            ('fluid_midi_event_set_key', i, [vp, i]),
            ('fluid_midi_event_set_velocity', i, [vp, i]),
            ('fluid_midi_event_set_control', i, [vp, i]),
            ('fluid_midi_event_set_value', i, [vp, i]),
            ('fluid_midi_event_set_program', i, [vp, i]),
            ('fluid_synth_get_program', i, [vp, i, ctypes.POINTER(i), ctypes.POINTER(i), ctypes.POINTER(i)]),
        ):
            fn = getattr(lib, name)
            fn.restype, fn.argtypes = res, args
        self.lib = lib

    def make(self, kind, ch, a=0, b=0):
        lib, ev = self.lib, self.lib.new_fluid_midi_event()
        lib.fluid_midi_event_set_type(ev, kind)
        lib.fluid_midi_event_set_channel(ev, ch)
        if kind == es.NOTE_ON:
            lib.fluid_midi_event_set_key(ev, a)
            lib.fluid_midi_event_set_velocity(ev, b)
        elif kind == es.CONTROL_CHANGE:
            lib.fluid_midi_event_set_control(ev, a)
            lib.fluid_midi_event_set_value(ev, b)
        elif kind == es.PROGRAM_CHANGE:
            lib.fluid_midi_event_set_program(ev, a)
        return ev

    def program(self, synth, ch):
        sf, bank, prog = ctypes.c_int(), ctypes.c_int(), ctypes.c_int()
        self.lib.fluid_synth_get_program(synth, ch, ctypes.byref(sf), ctypes.byref(bank), ctypes.byref(prog))
        return bank.value, prog.value


class SlowFluid(es.Fluid):
    """libfluidsynth whose load of the main bank waits until the test lets it go (a Pi 3 reads the 729 MB bank for about 33 s)."""

    def __init__(self, piano):
        super().__init__()
        self.gate = threading.Event()
        real = self.synth_sfload

        def sfload(synth, path, reset):
            if path == piano.encode():
                self.gate.wait(10)
            return real(synth, path, reset)

        self.synth_sfload = sfload


@unittest.skipUnless(have_fluidsynth(), 'libfluidsynth is not installed')
class HostTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = self.tmp.name
        self.starter = make_playable_sf2(os.path.join(d, 'starter.sf2'), presets=[('Starter', 0, 0)])
        self.piano = make_playable_sf2(os.path.join(d, 'piano.sf2'), presets=[('Grand', 0, 0), ('Other', 0, 7)], extra_bytes=4 << 20)
        self.mech = make_playable_sf2(os.path.join(d, 'mech.sf2'), presets=[('Hammer', 0, 1), ('Resonance', 0, 2)])
        self.state = os.path.join(d, 'state.json')
        self.ev = Events()

    def tearDown(self):
        self.tmp.cleanup()

    def host(self, fluid=None, piano=None, quiet_wait=5):
        # the file driver renders in real time (ALSA's null device takes the audio as fast as it is made: notes would end at once)
        pairs = [('audio.driver', 'file'), ('audio.file.name', os.devnull), ('audio.file.type', 'raw'), ('midi.driver', 'alsa_seq')]
        pairs += [('synth.gain', '0.6'), ('synth.lock-memory', '0')]
        h = es.Host(fluid or es.Fluid(), pairs, self.starter, piano or self.piano, self.mech, state_file=self.state, quiet_wait=quiet_wait)
        self.assertTrue(h.start())
        return h

    def phase(self):
        try:
            with open(self.state) as f:
                return json.load(f)['phase']
        except (OSError, ValueError):
            return None

    def wait_phase(self, want, timeout=10):
        t0 = time.time()
        while time.time() - t0 < timeout:
            if self.phase() == want:
                return True
            time.sleep(0.05)
        return False

    def start_run(self, h, commands=''):
        r, w = os.pipe()
        os.write(w, commands.encode())
        threading.Thread(target=h.run, args=(os.fdopen(r),), daemon=True).start()
        return w

    def test_the_keys_play_the_starter_while_the_main_bank_loads(self):
        fluid = SlowFluid(self.piano)
        h = self.host(fluid)
        self.assertEqual(self.phase(), 'starter')
        self.start_run(h)
        time.sleep(0.2)  # the main bank is "loading" now
        t0 = time.monotonic()
        h.on_midi(None, self.ev.make(es.NOTE_ON, 0, 60, 100))
        self.assertLess(time.monotonic() - t0, 0.2)  # not held up by the load
        self.assertGreater(fluid.synth_get_active_voice_count(h.small), 0)  # and it sounds, on the starter
        fluid.gate.set()
        self.assertTrue(self.wait_phase('full'))
        self.assertIsNone(h.small)

    def test_the_bridges_choices_move_to_the_main_bank_and_the_starter_keeps_its_own_piano(self):
        fluid = SlowFluid(self.piano)
        h = self.host(fluid)
        self.start_run(h)
        for kind, a, b in ((es.CONTROL_CHANGE, 0, 0), (es.PROGRAM_CHANGE, 7, 0), (es.CONTROL_CHANGE, 64, 127)):
            h.on_midi(None, self.ev.make(kind, 0, a, b))
        h.on_midi(None, self.ev.make(es.PROGRAM_CHANGE, 1, 2))  # a mechanics channel
        self.assertEqual(self.ev.program(h.small, 0), (0, 0))  # 0:7 is not in the starter: it plays its own piano
        self.assertEqual(self.ev.program(h.small, 1), (0, 2))
        h.on_midi(None, self.ev.make(es.NOTE_ON, 0, 60, 100))
        self.assertGreater(fluid.synth_get_active_voice_count(h.small), 0)
        fluid.gate.set()
        self.assertTrue(self.wait_phase('full'))
        self.assertEqual(self.ev.program(h.main, 0), (0, 7))
        self.assertEqual(self.ev.program(h.main, 1), (0, 2))
        v = ctypes.c_int()
        fluid.synth_get_cc(h.main, 0, 64, ctypes.byref(v))
        self.assertEqual(v.value, 127)  # the pedal that is down stays down
        h.on_midi(None, self.ev.make(es.NOTE_ON, 0, 64, 100))
        self.assertGreater(fluid.synth_get_active_voice_count(h.main), 0)  # the keys play the main bank now

    def test_the_switch_waits_for_a_pause(self):
        h = self.host(quiet_wait=5)
        long_note = make_playable_sf2(os.path.join(self.tmp.name, 'long.sf2'), presets=[('Long', 0, 0)], seconds=1.5)
        h.f.synth_sfload(h.small, long_note.encode(), 0)
        h.f.synth_program_select(h.small, 0, 3, 0, 0)
        h.on_midi(None, self.ev.make(es.NOTE_ON, 0, 60, 100))  # sounds for 1.5 s
        self.start_run(h)
        self.assertTrue(self.wait_phase('loaded'))
        time.sleep(0.5)
        self.assertEqual(self.phase(), 'loaded')  # still sounding: no switch
        self.assertTrue(self.wait_phase('full', timeout=5))

    def test_continuous_playing_fades_over_after_the_wait(self):
        h = self.host(quiet_wait=0.5)
        long_note = make_playable_sf2(os.path.join(self.tmp.name, 'long.sf2'), presets=[('Long', 0, 0)], seconds=8)
        h.f.synth_sfload(h.small, long_note.encode(), 0)
        h.f.synth_program_select(h.small, 0, 3, 0, 0)
        h.on_midi(None, self.ev.make(es.NOTE_ON, 0, 60, 100))
        self.start_run(h)
        self.assertTrue(self.wait_phase('full', timeout=4))

    def test_a_main_bank_that_does_not_load_leaves_the_starter_playing(self):
        broken = os.path.join(self.tmp.name, 'broken.sf2')
        with open(broken, 'wb') as f:
            f.write(b'RIFF half a copy')
        h = self.host(piano=broken)
        self.start_run(h, 'set synth.gain 0.3\n')
        self.assertTrue(self.wait_phase('failed'))
        time.sleep(0.3)
        self.assertAlmostEqual(h.f.synth_get_gain(h.small), 0.3, places=3)  # the commands reach the starter
        h.on_midi(None, self.ev.make(es.NOTE_ON, 0, 60, 100))
        self.assertGreater(h.f.synth_get_active_voice_count(h.small), 0)

    def test_the_volume_reaches_both_synthesizers(self):
        fluid = SlowFluid(self.piano)
        h = self.host(fluid)
        w = self.start_run(h, 'set synth.gain 0.25\n')
        time.sleep(0.3)
        self.assertAlmostEqual(fluid.synth_get_gain(h.small), 0.25, places=3)  # at once, while the main bank is loading
        fluid.gate.set()
        self.assertTrue(self.wait_phase('full'))
        time.sleep(0.3)
        self.assertAlmostEqual(fluid.synth_get_gain(h.main), 0.25, places=3)
        os.write(w, b'set synth.gain 0.4\n')  # and after the switch (the starter is gone: the main bank keeps the live settings)
        time.sleep(0.3)
        self.assertAlmostEqual(fluid.synth_get_gain(h.main), 0.4, places=3)


class RunScriptTests(unittest.TestCase):
    """exxpiano-run starts exxpiano-synth.py only when there is a starter bank and the main bank is big enough to need it."""

    def run_script(self, piano_bytes, starter=True, **env):
        with tempfile.TemporaryDirectory() as d:
            asound = os.path.join(d, 'asound')
            os.makedirs(os.path.join(asound, 'card0', 'pcm0p'))
            with open(os.path.join(asound, 'cards'), 'w') as f:
                f.write(' 0 [Headphones     ]: bcm2835_headpho - bcm2835 Headphones\n')
            piano = os.path.join(d, 'piano.sf2')
            with open(piano, 'wb') as f:
                f.truncate(piano_bytes)  # sparse: the size is what counts
            s = os.path.join(d, 'starter.sf2')
            if starter:
                with open(s, 'wb') as f:
                    f.write(b'starter')
            e = dict(
                os.environ, ASOUND_DIR=asound, DRY_RUN='1', USB_WAIT='0', PIANO_SF2=piano, MECH_SF2='/m.sf2', STARTER_SF2=s, SF2INFO='/none', BANKLIB='/none'
            )
            e.update(env)
            r = subprocess.run(['bash', RUN], env=e, capture_output=True, text=True)
            return r.stdout.strip().replace(d, '<d>')

    def test_a_big_bank_starts_with_the_starter(self):
        out = self.run_script(100 << 20)
        self.assertTrue(
            out.startswith('python3 %s --starter <d>/starter.sf2 -- /usr/bin/fluidsynth -a alsa' % os.path.join(ROOT, 'pi', 'exxpiano-synth.py')), out
        )
        self.assertTrue(out.endswith('<d>/piano.sf2 /m.sf2'), out)

    def test_a_small_bank_starts_as_before(self):
        self.assertTrue(self.run_script(10 << 20).startswith('/usr/bin/fluidsynth '))

    def test_no_starter_bank_or_an_empty_setting_starts_as_before(self):
        self.assertTrue(self.run_script(100 << 20, starter=False).startswith('/usr/bin/fluidsynth '))
        self.assertTrue(self.run_script(100 << 20, STARTER_SF2='').startswith('/usr/bin/fluidsynth '))


if __name__ == '__main__':
    unittest.main()
