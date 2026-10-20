"""Tests for pi/exxpiano-synth.py: the preview plays while the piano bank loads, the move to it waits for a moment with no key and no pedal,
a new bank loads the same way, and exxpiano-run starts the program only when the preview bank is usable.

FluidSynth is replaced by a fake that records what was asked (the real library was measured on a Pi 3 B: design.md of the OpenSpec change
preview-bank-while-loading). Run from the repository root:   python3 -m unittest discover -s tests -v
"""

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(ROOT, 'pi'))
import banklib  # noqa: E402
import oslib  # noqa: E402

spec = importlib.util.spec_from_file_location('exxpiano_synth', os.path.join(ROOT, 'pi', 'exxpiano-synth.py'))
synth = importlib.util.module_from_spec(spec)
spec.loader.exec_module(synth)


class FakeFS:
    """The calls of synth.FluidSynth. Banks named in `broken` do not load; `script` gives (keys, pedal) per look of the transition."""

    def __init__(self, broken=(), script=None, sizes=None):
        self.broken, self.script, self.sizes = set(broken), list(script or []), sizes or {}
        self.calls, self.stack, self.next_id = [], [], 1
        self.channel = {}  # chan -> sfont id
        self.loaded_aside = []
        self.memory = 1000
        self.release = threading.Event()  # set: a load may finish (tests that look at the state while a bank loads)
        self.release.set()

    def _id(self):
        self.next_id += 1
        return self.next_id - 1

    def load_aside(self, path):
        if 'preview' not in path:
            self.release.wait(5)
        self.loaded_aside.append(path)
        return None if path in self.broken else ('sfont', path)

    def sfload(self, path):
        if path in self.broken:
            return None
        sfid = self._id()
        self.stack.append((sfid, path))
        return sfid

    def add(self, handle):
        sfid = self._id()
        self.stack.append((sfid, handle[1]))
        self.memory += self.sizes.get(handle[1], 0)
        return sfid

    def path_of(self, sfid):
        return dict(self.stack).get(sfid)

    def unload(self, sfid):
        self.calls.append(('unload', self.path_of(sfid)))
        self.memory -= self.sizes.get(self.path_of(sfid), 0)
        self.stack = [x for x in self.stack if x[0] != sfid]

    def select(self, chan, sfid, bank, prog):
        if self.path_of(sfid) is None or (bank, prog) not in ((0, 0), (2, 5)):
            return False
        self.channel[chan] = sfid
        self.calls.append(('select', chan, self.path_of(sfid), bank, prog))
        return True

    def first_preset(self, sfid):
        return (0, 0)

    def presets(self, sfid):
        return [(0, p) for p in range(128)] if 'gm' in (self.path_of(sfid) or '') else [(0, 0)]

    def channel_sfont(self, chan):
        return self.channel.get(chan, -1)

    def _look(self):
        return self.script.pop(0) if self.script else (False, False)

    def pedal_down(self, chan):  # the engine asks for the pedal first, then for the keys of the same moment
        self.state = self._look()
        return self.state[1]

    def keys_down(self, chan):
        return self.state[0]

    def sounding(self, chan):
        return self.state[2] if len(self.state) > 2 else True

    def silence(self, chan):
        self.calls.append(('silence', chan))

    def size(self, path):
        return self.sizes.get(path, 0)

    def rss(self):
        return self.memory

    def start_io(self):
        self.calls.append(('start_io',))


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


class EngineTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.state_file = os.path.join(tmp.name, 'bank.json')
        self.clock = Clock()

    def engine(self, fs):
        return synth.Engine(fs, state_file=self.state_file, clock=self.clock, sleep=self.clock.sleep)

    def state(self):
        with open(self.state_file) as f:
            return json.load(f)

    def finish(self, e):
        e.job.join(5)
        self.assertFalse(e.job.is_alive())

    def test_the_preview_sounds_first_and_the_piano_takes_over(self):
        fs = FakeFS()
        e = self.engine(fs)
        self.assertTrue(e.start('/p/preview.sf2', '/p/mech.sf2', '/p/full.sf2', 0, 0))
        self.finish(e)
        # the sound comes up right after the preview; the mechanics follow in the background, above the preview in the stack
        self.assertEqual([p for _, p in fs.stack], ['/p/mech.sf2', '/p/full.sf2'])
        self.assertLess(fs.calls.index(('select', 0, '/p/preview.sf2', 0, 0)), fs.calls.index(('start_io',)))
        self.assertEqual(fs.loaded_aside[:2], ['/p/preview.sf2', '/p/mech.sf2'])
        self.assertEqual(fs.calls[-2:], [('select', 0, '/p/full.sf2', 0, 0), ('unload', '/p/preview.sf2')])
        st = self.state()
        self.assertEqual((st['state'], st['playing'], st['loading'], st['error']), ('ready', '/p/full.sf2', None, None))
        self.assertEqual(st['pid'], os.getpid())

    def test_while_the_bank_loads_the_state_says_the_preview_plays(self):
        fs = FakeFS()
        fs.release.clear()  # the big bank does not finish loading until the test says so
        e = self.engine(fs)
        e.start('/p/preview.sf2', '/p/mech.sf2', '/p/full.sf2', 0, 0)
        st = self.state()
        self.assertEqual((st['state'], st['playing'], st['loading']), ('preview', '/p/preview.sf2', '/p/full.sf2'))
        self.assertIn(('start_io',), fs.calls)  # the sound is on already
        fs.release.set()
        self.finish(e)
        self.assertEqual(self.state()['state'], 'ready')

    def test_the_sound_is_on_before_the_mechanics_load(self):
        fs = FakeFS()
        order = []
        real_aside, real_io = fs.load_aside, fs.start_io
        fs.load_aside = lambda path: (order.append(os.path.basename(path)), real_aside(path))[1]
        fs.start_io = lambda: (order.append('sound'), real_io())[1]
        e = self.engine(fs)
        e.start('/p/preview.sf2', '/p/mech.sf2', '/p/full.sf2', 0, 0)
        self.finish(e)
        self.assertEqual(order, ['preview.sf2', 'sound', 'mech.sf2', 'full.sf2'])

    def test_a_bank_too_big_for_the_memory_is_replaced_by_the_one_chosen(self):
        fs = FakeFS()
        asked = []
        e = synth.Engine(
            fs,
            state_file=self.state_file,
            clock=self.clock,
            sleep=self.clock.sleep,
            choose=lambda piano, mech, preview: (asked.append((piano, mech, preview)), '/p/lite.sf2')[1],
        )
        e.start('/p/preview.sf2', '/p/mech.sf2', '/p/full.sf2', 0, 0)
        self.finish(e)
        self.assertEqual(asked, [('/p/full.sf2', '/p/mech.sf2', '/p/preview.sf2')])
        self.assertNotIn('/p/full.sf2', fs.loaded_aside)
        self.assertEqual(self.state()['playing'], '/p/lite.sf2')

    def test_the_move_waits_until_no_key_and_no_pedal_are_down(self):
        fs = FakeFS(script=[(True, False), (False, True), (True, True), (False, False)])
        e = self.engine(fs)
        e.start('/p/preview.sf2', '/p/mech.sf2', '/p/full.sf2', 0, 0)
        self.finish(e)
        self.assertEqual(fs.script, [])  # it looked four times and moved at the fourth
        self.assertAlmostEqual(self.clock.t, 3 * synth.TICK)
        self.assertEqual(self.state()['playing'], '/p/full.sf2')

    def test_a_pianist_who_never_stops_gets_the_move_at_a_release_of_the_pedal_after_the_patience(self):
        ticks = int(synth.PATIENCE / synth.TICK) + 5
        fs = FakeFS(script=[(True, True)] * ticks + [(True, False)])
        e = self.engine(fs)
        e.start('/p/preview.sf2', '/p/mech.sf2', '/p/full.sf2', 0, 0)
        self.finish(e)
        self.assertGreater(self.clock.t, synth.PATIENCE)
        self.assertEqual(self.state()['playing'], '/p/full.sf2')

    def test_a_pedal_that_stays_down_does_not_hold_the_move_once_nothing_sounds(self):
        ticks = int(synth.PATIENCE / synth.TICK) + 5
        fs = FakeFS(script=[(False, True, True)] * ticks + [(False, True, False)])  # the pedal never comes up; then the last note has died away
        e = self.engine(fs)
        e.start('/p/preview.sf2', '/p/mech.sf2', '/p/full.sf2', 0, 0)
        self.finish(e)
        self.assertEqual(fs.script, [])
        self.assertEqual(self.state()['playing'], '/p/full.sf2')

    def test_the_piano_channel_stays_on_the_preview_until_the_move(self):
        fs = FakeFS(script=[(True, False), (False, False)])
        orig = fs.keys_down

        def bridge_picks_by_number(chan):
            fs.channel[0] = max(i for i, _ in fs.stack)  # a program change of the bridge lands on the newest bank with 0:0
            return orig(chan)

        fs.keys_down = bridge_picks_by_number
        e = self.engine(fs)
        e.start('/p/preview.sf2', '/p/mech.sf2', '/p/full.sf2', 0, 0)
        self.finish(e)
        selects = [c for c in fs.calls if c[0] == 'select']
        self.assertEqual(selects.count(('select', 0, '/p/preview.sf2', 0, 0)), 2)  # the start, then back once: the one look with a key down
        self.assertEqual(selects[-1], ('select', 0, '/p/full.sf2', 0, 0))

    def test_a_bank_with_presets_where_the_mechanics_answer_gets_the_mechanics_back_above_it(self):
        fs = FakeFS()
        e = self.engine(fs)
        e.start('/p/preview.sf2', '/p/mech.sf2', '/p/gm.sf2', 0, 0)
        self.finish(e)
        stack = [p for _, p in fs.stack]
        self.assertEqual(stack, ['/p/gm.sf2', '/p/mech.sf2'])  # the newest answers first: the mechanics again, above the General MIDI bank
        self.assertIn(('unload', '/p/mech.sf2'), fs.calls)  # the first copy of the mechanics
        self.assertEqual(fs.loaded_aside, ['/p/preview.sf2', '/p/mech.sf2', '/p/gm.sf2', '/p/mech.sf2'])
        fs = FakeFS()
        e = self.engine(fs)
        e.start('/p/preview.sf2', '/p/mech.sf2', '/p/full.sf2', 0, 0)
        self.finish(e)
        self.assertEqual(fs.loaded_aside, ['/p/preview.sf2', '/p/mech.sf2', '/p/full.sf2'])  # a piano bank alone: nothing to do

    def test_the_preset_of_the_settings_is_used_and_a_missing_one_falls_back_to_the_first(self):
        fs = FakeFS()
        e = self.engine(fs)
        e.start('/p/preview.sf2', '/p/mech.sf2', '/p/odd.sf2', 2, 5)
        self.finish(e)
        self.assertIn(('select', 0, '/p/odd.sf2', 2, 5), fs.calls)
        fs = FakeFS()
        e = self.engine(fs)
        e.start('/p/preview.sf2', '/p/mech.sf2', '/p/full.sf2', 9, 9)
        self.finish(e)
        self.assertIn(('select', 0, '/p/full.sf2', 0, 0), fs.calls)

    def test_a_bank_that_does_not_load_leaves_the_preview_playing_or_brings_the_last_good_one(self):
        fs = FakeFS(broken={'/p/bad.sf2'})
        e = self.engine(fs)
        e.start('/p/preview.sf2', '/p/mech.sf2', '/p/bad.sf2', 0, 0)
        self.finish(e)
        st = self.state()
        self.assertEqual((st['state'], st['loading'], st['error']), ('failed', None, 'bad.sf2 did not load'))
        self.assertNotIn(('unload', '/p/preview.sf2'), fs.calls)  # the preview keeps playing
        fs = FakeFS(broken={'/p/bad.sf2'})
        e = self.engine(fs)
        e.start('/p/preview.sf2', '/p/mech.sf2', '/p/bad.sf2', 0, 0, fallback='/p/good.sf2')
        self.finish(e)
        self.assertEqual(fs.loaded_aside, ['/p/preview.sf2', '/p/mech.sf2', '/p/bad.sf2', '/p/good.sf2'])
        self.assertEqual((self.state()['state'], self.state()['playing']), ('ready', '/p/good.sf2'))

    def test_no_preview_no_start(self):
        fs = FakeFS(broken={'/p/preview.sf2'})
        e = self.engine(fs)
        self.assertFalse(e.start('/p/preview.sf2', '/p/mech.sf2', '/p/full.sf2', 0, 0))
        self.assertNotIn(('start_io',), fs.calls)

    def test_a_new_bank_loads_after_the_old_one_gave_its_memory_back(self):
        fs = FakeFS(sizes={'/p/full.sf2': 700, '/p/lite.sf2': 250, '/p/preview.sf2': 24})
        e = self.engine(fs)
        e.start('/p/preview.sf2', '/p/mech.sf2', '/p/full.sf2', 0, 0)
        self.finish(e)
        fs.calls.clear()
        self.assertTrue(e.command('exxpiano-load 0 0 /p/lite.sf2', '/p/preview.sf2'))
        self.finish(e)
        names = [c[0] + ' ' + str(c[2] if c[0] == 'select' else c[1]) for c in fs.calls]
        self.assertEqual(names, ['select /p/preview.sf2', 'unload /p/full.sf2', 'select /p/lite.sf2', 'unload /p/preview.sf2'])
        self.assertEqual(fs.loaded_aside[-2:], ['/p/preview.sf2', '/p/lite.sf2'])
        self.assertEqual((self.state()['state'], self.state()['playing']), ('ready', '/p/lite.sf2'))

    def test_memory_that_does_not_come_back_silences_the_piano_channel_before_the_next_bank(self):
        fs = FakeFS(sizes={'/p/full.sf2': 700})
        e = self.engine(fs)
        e.start('/p/preview.sf2', '/p/mech.sf2', '/p/full.sf2', 0, 0)
        self.finish(e)
        fs.unload = lambda sfid: fs.calls.append(('unload', fs.path_of(sfid)))  # its voices hold it: nothing comes back
        e.command('exxpiano-load 0 0 /p/lite.sf2', '/p/preview.sf2')
        self.finish(e)
        self.assertIn(('silence', 0), fs.calls)
        self.assertGreaterEqual(self.clock.t, synth.MEMORY_WAIT)
        self.assertEqual(self.state()['playing'], '/p/lite.sf2')

    def test_a_second_load_while_one_runs_is_refused_and_bad_lines_are_ours_but_ignored(self):
        fs = FakeFS()
        e = self.engine(fs)
        e.start('/p/preview.sf2', '/p/mech.sf2', '/p/full.sf2', 0, 0)
        self.finish(e)
        fs.release.clear()
        self.assertTrue(e.load('/p/preview.sf2', '/p/a.sf2', 0, 0))
        self.assertFalse(e.load('/p/preview.sf2', '/p/b.sf2', 0, 0))
        fs.release.set()
        self.finish(e)
        self.assertNotIn('/p/b.sf2', fs.loaded_aside)
        self.assertTrue(e.command('exxpiano-load nonsense', '/p/preview.sf2'))
        self.assertFalse(e.command('set synth.gain 0.8', '/p/preview.sf2'))  # FluidSynth's own command
        e.command('exxpiano-load 0 0 /p/a bank with spaces.sf2', '/p/preview.sf2')
        self.finish(e)
        self.assertEqual(self.state()['playing'], '/p/a bank with spaces.sf2')


class ArgsTests(unittest.TestCase):
    def test_settings_and_banks(self):
        a = synth.parse_args(
            [
                '-o',
                'audio.alsa.device=exxpiano_tap:\'plughw:CARD=U24\'',
                '-o',
                'synth.gain=0.6',
                '--preview',
                'p.sf2',
                '--mech',
                'm.sf2',
                '--bank',
                '2',
                '--prog',
                '5',
                'full.sf2',
            ]
        )
        self.assertEqual(a.options, [('audio.alsa.device', "exxpiano_tap:'plughw:CARD=U24'"), ('synth.gain', '0.6')])
        self.assertEqual((a.preview, a.mech, a.bank, a.prog, a.piano, a.fallback), ('p.sf2', 'm.sf2', 2, 5, 'full.sf2', ''))
        with self.assertRaises(SystemExit):
            synth.parse_args(['-o', 'nonsense', '--preview', 'p', '--mech', 'm', 'x'])


class LaunchTests(unittest.TestCase):
    """exxpiano-run picks exxpiano-synth only for a usable preview bank."""

    def run_dry(self, preview, failed=False, newer_preview=False):
        with tempfile.TemporaryDirectory() as tmp:
            asound = os.path.join(tmp, 'asound')
            os.makedirs(os.path.join(asound, 'card0', 'pcm0p'))
            with open(os.path.join(asound, 'cards'), 'w') as f:
                f.write(' 0 [Headphones     ]: bcm2835_headpho - bcm2835 Headphones\n')
            good = os.path.join(tmp, 'good.sf2')
            with open(good, 'wb') as f:
                f.write(b'x')
            env = dict(
                os.environ,
                ASOUND_DIR=asound,
                DRY_RUN='1',
                USB_WAIT='0',
                PIANO_SF2=good,
                MECH_SF2='/m.sf2',
                FLUIDSYNTH='/usr/bin/fluidsynth',
                SF2INFO=os.path.join(tmp, 'sf2info.py'),
                BANKLIB=os.path.join(tmp, 'none.py'),
                METER='0',
                CPU_COUNT='4',
                FX_ARGS='--velocity-curve 1 --piano-bank 2 --piano-prog 5',
                PIANO_SF2_LAST_GOOD='/last.sf2',
            )
            with open(env['SF2INFO'], 'w') as f:  # a stand-in: a bank is good when its name has "good" in it
                f.write("import sys\nsys.exit(0 if 'good' in sys.argv[-1] else 1)\n")
            env['EXXPIANO_PREVIEW_FAILED'] = os.path.join(tmp, 'preview.failed')
            if failed:
                with open(env['EXXPIANO_PREVIEW_FAILED'], 'w'):
                    pass
            if preview:
                env['PREVIEW_SF2'] = os.path.join(tmp, preview)
                if 'missing' not in preview:
                    with open(env['PREVIEW_SF2'], 'wb') as f:
                        f.write(b'RIFF preview')
                    if failed:  # the mark is newer than the file, unless the file is a new one
                        shift = 10 if newer_preview else -10
                        os.utime(env['PREVIEW_SF2'], (os.path.getmtime(env['EXXPIANO_PREVIEW_FAILED']) + shift,) * 2)
            else:
                env['PREVIEW_SF2'] = ''
            r = subprocess.run(['bash', os.path.join(ROOT, 'pi', 'exxpiano-run')], env=env, capture_output=True, text=True, check=True)
            return r.stdout.replace(tmp, 'TMP'), r.stderr

    def test_a_usable_preview_starts_exxpiano_synth_with_the_same_settings(self):
        out, _ = self.run_dry('preview-good.sf2')
        self.assertIn('exxpiano-synth.py -o audio.driver=alsa -o midi.driver=alsa_seq', out)
        for part in (
            "audio.alsa.device=plughw:CARD=Headphones",
            'audio.sample-format=16bits',
            'synth.sample-rate=44100',
            'audio.period-size=64',
            'audio.periods=3',
            'synth.reverb.active=0',
            'synth.chorus.active=0',
            'synth.polyphony=256',
            'synth.cpu-cores=3',
            'synth.gain=0.6',
            '--preview TMP/preview-good.sf2',
            '--mech /m.sf2',
            '--bank 2 --prog 5',
            '--fallback /last.sf2',
            'TMP/good.sf2',
        ):
            self.assertIn(part, out)

    def test_without_a_usable_preview_fluidsynth_starts_as_before(self):
        out, _ = self.run_dry(None)  # PREVIEW_SF2 empty: turned off
        self.assertTrue(out.startswith('/usr/bin/fluidsynth -a alsa -m alsa_seq'), out)
        out, err = self.run_dry('preview-missing.sf2')
        self.assertTrue(out.startswith('/usr/bin/fluidsynth -a alsa'), out)
        self.assertIn('preview-missing.sf2 is missing: loading the piano bank without it', err)
        out, err = self.run_dry('preview-good.sf2', failed=True)  # it did not load last time
        self.assertTrue(out.startswith('/usr/bin/fluidsynth -a alsa'), out)
        self.assertIn('did not load before', err)
        out, _ = self.run_dry('preview-good.sf2', failed=True, newer_preview=True)  # a new preview file is tried again
        self.assertIn('exxpiano-synth.py', out)

    def test_with_the_preview_no_check_runs_before_the_synthesizer(self):
        with tempfile.TemporaryDirectory() as tmp:
            calls = os.path.join(tmp, 'calls')
            stand_in = os.path.join(tmp, 'python3')
            with open(stand_in, 'w') as f:  # every python3 that exxpiano-run starts before the synthesizer is written down
                f.write('#!/bin/sh\necho "$@" >> %s\nexit 0\n' % calls)
            os.chmod(stand_in, 0o755)
            real_path = os.environ['PATH']
            try:
                os.environ['PATH'] = tmp + os.pathsep + real_path
                self.run_dry('preview-good.sf2')
            finally:
                os.environ['PATH'] = real_path
            self.assertFalse(os.path.exists(calls) and open(calls).read().strip(), 'python ran before the synthesizer')


PI3B_MEMTOTAL = 978632 * 1024  # MemTotal of a Raspberry Pi 3 B with gpu_mem=16 (measured on Alpine 3.24)


class HelperTests(unittest.TestCase):
    def test_the_synthesizer_is_found_under_either_name(self):
        with tempfile.TemporaryDirectory() as proc:
            for pid, comm in (('300', 'fluidsynth'), ('400', 'exxpiano-synth'), ('500', 'python3')):
                os.makedirs(os.path.join(proc, pid))
                with open(os.path.join(proc, pid, 'comm'), 'w') as f:
                    f.write(comm + '\n')
            self.assertEqual(oslib.synth_pid(proc), 400)
            self.assertEqual(oslib.synth_pid(proc, comm='fluidsynth'), 300)

    def test_a_bank_that_fits_only_without_the_preview_is_replaced_while_the_preview_is_used(self):
        with tempfile.TemporaryDirectory() as tmp:
            sizes = {'mech.sf2': 37660222, 'piano.sf2': 728982358, 'piano-lite.sf2': 248325370, 'piano-preview.sf2': 23826614}  # the real banks
            for name, size in sizes.items():
                with open(os.path.join(tmp, name), 'wb') as f:
                    f.truncate(size)
            path = lambda n: os.path.join(tmp, n)  # noqa: E731
            real_inspect = banklib.inspect
            banklib.inspect = lambda p: (dict(name=os.path.basename(p), presets=[dict(bank=0, program=0)]), None)  # the files are only sizes
            self.addCleanup(setattr, banklib, 'inspect', real_inspect)
            board = (200 << 20) + sizes['mech.sf2'] + sizes['piano.sf2'] + (10 << 20)  # the full bank fits next to the mechanics, not the preview too
            self.assertIsNone(banklib.fitting_replacement(path('piano.sf2'), path('mech.sf2'), tmp, mem_total=board))
            self.assertEqual(
                banklib.fitting_replacement(path('piano.sf2'), path('mech.sf2'), tmp, mem_total=board, preview=path('piano-preview.sf2')),
                path('piano-lite.sf2'),
            )
            pi3b = PI3B_MEMTOTAL  # a Pi 3 B with gpu_mem=16: the full bank fits next to the mechanics and the preview, with about 2 MiB to spare
            self.assertIsNone(banklib.fitting_replacement(path('piano.sf2'), path('mech.sf2'), tmp, mem_total=pi3b, preview=path('piano-preview.sf2')))

    def test_the_preview_counts_in_the_memory_and_its_state_is_read_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            mech, preview = os.path.join(tmp, 'm.sf2'), os.path.join(tmp, 'p.sf2')
            for path, n in ((mech, 30), (preview, 20)):
                with open(path, 'wb') as f:
                    f.write(b'x' * n)
            self.assertEqual(banklib.resident_bytes(mech, preview), 50)
            self.assertEqual(banklib.resident_bytes(mech, None), 30)
            self.assertEqual(banklib.resident_bytes(mech, os.path.join(tmp, 'gone.sf2')), 30)
            state = os.path.join(tmp, 'bank.json')
            synth.write_state(state, state='preview', playing=preview, loading='/b/full.sf2', error=None)
            st = banklib.loading_state(state)
            self.assertEqual((st['state'], st['loading'], st['pid']), ('preview', '/b/full.sf2', os.getpid()))
            with open(state, 'w') as f:
                json.dump(dict(state='ready', pid=2**22 + 12345), f)  # a synthesizer that is gone
            self.assertIsNone(banklib.loading_state(state) if os.path.isdir('/proc/self') else None)
            with open(state, 'w') as f:
                f.write('{"state": "nonsense"}')
            self.assertIsNone(banklib.loading_state(state))
            self.assertIsNone(banklib.loading_state(os.path.join(tmp, 'none.json')))


if __name__ == '__main__':
    unittest.main()
