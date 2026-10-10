"""Tests for pi/piano-web.py: the HTTP API with the system actions replaced by fakes (no Pi, no root needed)."""
import importlib.util
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
PI = os.path.join(HERE, '..', 'pi')
sys.path.insert(0, PI)
sys.path.insert(0, HERE)
from sf2fixture import make_sf2  # noqa: E402
import profiles  # noqa: E402
spec = importlib.util.spec_from_file_location('piano_web', os.path.join(PI, 'piano-web.py'))
web = importlib.util.module_from_spec(spec)
spec.loader.exec_module(web)

CONF = """PIANO_SF2=/p.sf2
MECH_SF2=/m.sf2
ALSA_DEVICE=auto
GAIN=1.0
POLYPHONY=128
USB_PERIOD_SIZE=64
USB_PERIODS=3
REVERB=0
FX_ARGS="--hammer-boost 0 --hammer-exp 0.8 --random 1"
"""


class FakeActions:
    def __init__(self):
        self.restarts = 0
        self.gains = []
        self.gain_ok = True
        self.mem = 954 << 20
        self.ready = [True, True, True]     # answers of wait_synth_ready, one per switch attempt
        self.waits = 0
        self.gate = None                     # an Event: wait_synth_ready blocks until it is set
        self.pid = 100

    def mem_total(self):
        return self.mem

    def synth_pid(self):
        return self.pid

    def wait_synth_ready(self, old_pid, timeout=150):
        if self.gate is not None:
            self.gate.wait(5)
        answer = self.ready[min(self.waits, len(self.ready) - 1)]
        self.waits += 1
        return answer

    def service_state(self, unit):
        return 'active'

    def restart_synth(self):
        self.restarts += 1

    def set_gain(self, value):
        self.gains.append(value)
        return self.gain_ok

    def version(self):
        return '9.9.9'

    def fluidsynth_version(self):
        return '2.4.4'

    def cards(self):
        return [dict(id='U24', name='U-24', usb=True), dict(id='Headphones', name='bcm2835 Headphones', usb=False)]

    def audio_output(self):
        return dict(device='plughw:CARD=U24', kind='usb', period=64, periods=3)


class ServerCase(unittest.TestCase):
    def start(self, conf=CONF):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.conf = os.path.join(self.tmp.name, 'piano-synth.conf')
        self.profile_file = os.path.join(self.tmp.name, 'state', 'profiles.json')    # never the real /var/lib
        with open(self.conf, 'w') as f:
            f.write(conf + 'PROFILE_FILE=%s\n' % self.profile_file)
        self.actions = FakeActions()
        self.app = web.App(self.conf, self.actions)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), web.make_handler(self.app))
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base = 'http://127.0.0.1:%d' % self.server.server_address[1]

    def call(self, path, body=None, headers=None, ctype='application/json', raw=None):
        data = raw if raw is not None else (None if body is None else json.dumps(body).encode())
        req = urllib.request.Request(self.base + path, data=data, headers=dict(headers or {}))
        if data is not None:
            req.add_header('Content-Type', ctype)
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def jcall(self, *a, **kw):
        code, body = self.call(*a, **kw)
        return code, json.loads(body.decode())

    def conf_text(self):
        with open(self.conf) as f:
            return f.read()

class WebTests(ServerCase):
    def test_index_page_is_served(self):
        self.start()
        code, body = self.call('/')
        self.assertEqual(code, 200)
        self.assertIn(b'<title>', body)

    def test_state_has_settings_spec_and_status(self):
        self.start()
        code, s = self.jcall('/api/state')
        self.assertEqual(code, 200)
        self.assertEqual(s['settings']['hammer_exp'], 0.8)
        self.assertEqual(s['settings']['velocity_curve'], 1.0)
        self.assertEqual(s['spec']['gain']['scope'], 'gain')
        self.assertEqual((s['spec']['velocity_curve']['lo'], s['spec']['velocity_curve']['hi']), (0.4, 2.5))
        self.assertEqual((s['version'], s['fluidsynth']), ('9.9.9', '2.4.4'))
        self.assertEqual(s['status']['synth'], 'active')
        self.assertEqual(s['status']['output']['device'], 'plughw:CARD=U24')
        self.assertEqual([c['id'] for c in s['status']['cards']], ['U24', 'Headphones'])
        self.assertFalse(s['auth_required'])

    def test_fx_setting_is_stored_without_restart(self):
        self.start()
        code, r = self.jcall('/api/settings', {'velocity_curve': 0.8, 'hammer_boost': 3})
        self.assertEqual(code, 200)
        self.assertTrue(r['applied']['fx'])
        self.assertFalse(r['restart_required'])
        self.assertIn('--velocity-curve 0.8', self.conf_text())
        self.assertEqual(self.actions.restarts, 0)

    def test_gain_is_applied_live_once(self):
        self.start()
        code, r = self.jcall('/api/settings', {'gain': 1.5})
        self.assertEqual((code, r['applied']['gain']), (200, True))
        self.assertEqual(self.actions.gains, [1.5])
        code, r = self.jcall('/api/settings', {'gain': 1.5})                      # unchanged -> nothing to do
        self.assertIsNone(r['applied']['gain'])
        self.assertEqual(self.actions.gains, [1.5])

    def test_gain_failure_is_reported(self):
        self.start()
        self.actions.gain_ok = False
        code, r = self.jcall('/api/settings', {'gain': 2.0})
        self.assertEqual((code, r['applied']['gain']), (200, False))
        self.assertIn('GAIN=2', self.conf_text())                                  # still stored for the next start

    def test_audio_setting_asks_for_a_restart_but_does_not_do_it(self):
        self.start()
        code, r = self.jcall('/api/settings', {'alsa_device': 'plughw:U24', 'reverb': 1})
        self.assertEqual(code, 200)
        self.assertTrue(r['restart_required'])
        self.assertEqual(self.actions.restarts, 0)
        self.assertIn('ALSA_DEVICE=plughw:U24', self.conf_text())
        code, r = self.jcall('/api/restart', {})
        self.assertEqual(code, 200)
        self.assertEqual(self.actions.restarts, 1)

    def test_bad_requests_are_rejected_and_change_nothing(self):
        self.start()
        before = self.conf_text()
        cases = [
            ('/api/settings', {'nope': 1}),
            ('/api/settings', {'gain': 99}),
            ('/api/settings', {'alsa_device': 'plughw:U24; reboot'}),
            ('/api/settings', {'hammer_exp': 'x'}),
            ('/api/settings', {}),
        ]
        for path, body in cases:
            with self.subTest(body):
                code, _ = self.jcall(path, body)
                self.assertEqual(code, 400)
        self.assertEqual(self.call('/api/settings', raw=b'{not json', ctype='application/json')[0], 400)
        self.assertEqual(self.call('/api/settings', raw=b'{"gain": 1.2}', ctype='text/plain')[0], 400)   # no simple cross-site form
        self.assertEqual(self.call('/api/settings', raw=b'x' * 5000)[0], 400)                             # too large
        self.assertEqual(self.conf_text(), before)
        self.assertEqual(self.actions.gains, [])

    def test_unknown_path(self):
        self.start()
        self.assertEqual(self.call('/nothing')[0], 404)
        self.assertEqual(self.call('/api/nothing', {'a': 1})[0], 404)

    def test_token_protects_the_api_but_not_the_page(self):
        self.start(CONF + 'WEB_TOKEN=s3cret\n')
        self.assertEqual(self.call('/')[0], 200)
        self.assertEqual(self.call('/api/state')[0], 401)
        self.assertEqual(self.call('/api/state', headers={'X-Token': 'wrong'})[0], 401)
        self.assertEqual(self.call('/api/settings', {'gain': 1.1})[0], 401)
        self.assertEqual(self.call('/api/restart', {})[0], 401)
        code, s = self.jcall('/api/state', headers={'X-Token': 's3cret'})
        self.assertEqual(code, 200)
        self.assertTrue(s['auth_required'])
        self.assertEqual(self.call('/api/settings', {'gain': 1.1}, headers={'X-Token': 's3cret'})[0], 200)
        self.assertEqual(self.actions.restarts, 0)


class BankWebTests(ServerCase):
    """The piano bank library through the API: listing, switching, rolling back and every refusal."""

    def setUp(self):
        self.bank_tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.bank_tmp.cleanup)
        self.bank_dir = self.bank_tmp.name
        self.mech = make_sf2(os.path.join(self.bank_dir, 'mech.sf2'), presets=[('Hammer', 0, 1)], name='Mechanics')
        self.old = make_sf2(os.path.join(self.bank_dir, 'old.sf2'), presets=[('Old piano', 0, 0)], name='Old piano')
        self.new = make_sf2(os.path.join(self.bank_dir, 'new.sf2'), presets=[('New piano', 0, 0), ('Pad', 0, 90)], name='New piano')
        self.gm = make_sf2(os.path.join(self.bank_dir, 'odd.sf2'), presets=[('Odd', 2, 5)], name='Odd bank')
        self.conf_text_base = ('BANK_DIR=%s\nPIANO_SF2="%s"\nMECH_SF2="%s"\nALSA_DEVICE=auto\nGAIN=1.0\nREVERB=0\n'
                               'FX_ARGS="--hammer-boost 0"\n' % (self.bank_dir, self.old, self.mech))
        self.start(self.conf_text_base)

    def finish_switch(self):
        self.app.switcher.join(10)

    def test_state_lists_the_banks_without_the_mechanics_and_marks_the_current_one(self):
        code, s = self.jcall('/api/state')
        b = s['banks']
        self.assertEqual(code, 200)
        self.assertEqual(sorted(x['id'] for x in b['banks']), ['new.sf2', 'odd.sf2', 'old.sf2'])
        self.assertEqual(b['current'], 'old.sf2')
        self.assertIsNone(b['external'])
        self.assertEqual([p['name'] for p in b['presets']], ['Old piano'])
        self.assertEqual(b['preset'], dict(bank=0, program=0))
        self.assertEqual(b['switch']['state'], 'idle')

    def test_a_current_bank_outside_the_library_is_reported_as_external(self):
        with tempfile.TemporaryDirectory() as elsewhere:
            path = make_sf2(os.path.join(elsewhere, 'far.sf2'), name='Far away')
            self.start(self.conf_text_base.replace(self.old, path))
            b = self.jcall('/api/state')[1]['banks']
            self.assertIsNone(b['current'])
            self.assertEqual((b['external']['path'], b['external']['name']), (path, 'Far away'))

    def test_switching_stores_the_new_bank_restarts_once_and_remembers_it_as_good(self):
        code, r = self.jcall('/api/bank', {'id': 'new.sf2'})
        self.assertEqual((code, r['started']), (200, True))
        self.finish_switch()
        text = self.conf_text()
        self.assertIn('PIANO_SF2="%s"' % self.new, text)
        self.assertIn('PIANO_SF2_LAST_GOOD="%s"' % self.new, text)
        self.assertEqual(self.actions.restarts, 1)
        b = self.jcall('/api/state')[1]['banks']
        self.assertEqual((b['current'], b['switch']['state']), ('new.sf2', 'done'))
        self.assertEqual(sorted(p['name'] for p in b['presets']), ['New piano', 'Pad'])

    def test_a_bank_without_preset_zero_gets_its_first_usable_preset(self):
        self.jcall('/api/bank', {'id': 'odd.sf2'})
        self.finish_switch()
        self.assertEqual(self.jcall('/api/state')[1]['banks']['preset'], dict(bank=2, program=5))

    def test_a_bank_that_does_not_come_up_is_rolled_back(self):
        self.actions.ready = [False, True]          # the new bank fails, the old one comes back
        self.jcall('/api/bank', {'id': 'new.sf2'})
        self.finish_switch()
        text = self.conf_text()
        self.assertIn('PIANO_SF2="%s"' % self.old, text)
        self.assertNotIn('LAST_GOOD', text)
        self.assertEqual(self.actions.restarts, 2)
        sw = self.jcall('/api/state')[1]['banks']['switch']
        self.assertEqual(sw['state'], 'rolled_back')
        self.assertIn('previous bank is playing again', sw['message'])

    def test_when_even_the_old_bank_does_not_come_back_the_state_says_failed(self):
        self.actions.ready = [False, False]
        self.jcall('/api/bank', {'id': 'new.sf2'})
        self.finish_switch()
        self.assertEqual(self.jcall('/api/state')[1]['banks']['switch']['state'], 'failed')

    def test_a_crash_inside_the_switch_still_rolls_back(self):
        def boom(old_pid, timeout=150):
            raise RuntimeError('systemctl exploded')
        self.actions.wait_synth_ready = boom
        self.jcall('/api/bank', {'id': 'new.sf2'})
        self.finish_switch()
        self.assertIn('PIANO_SF2="%s"' % self.old, self.conf_text())

    def test_refusals_change_nothing(self):
        junk = os.path.join(self.bank_dir, 'junk.sf2')
        with open(junk, 'wb') as f:
            f.write(b'this is not a soundfont')
        before = self.conf_text()
        for bad in ('../old.sf2', '/etc/passwd', 'missing.sf2', 'junk.sf2', '', 'notes.txt', None, 7):
            with self.subTest(bad):
                code, r = self.jcall('/api/bank', {'id': bad})
                self.assertEqual(code, 400, r)
        self.assertEqual(self.call('/api/bank', raw=b'[1]')[0], 400)
        self.assertEqual(self.conf_text(), before)
        self.assertEqual(self.actions.restarts, 0)

    def test_a_bank_too_big_for_the_memory_is_refused(self):
        self.actions.mem = (400 << 20) + (1 << 10)        # the 400 MB reserve leaves almost nothing
        code, r = self.jcall('/api/bank', {'id': 'new.sf2'})
        self.assertEqual(code, 400)
        self.assertIn('too large', r['error'])
        self.assertEqual(self.actions.restarts, 0)
        banks = {x['id']: x for x in self.jcall('/api/state')[1]['banks']['banks']}
        self.assertFalse(banks['new.sf2']['fits'])

    def test_choosing_the_bank_already_in_use_does_nothing(self):
        code, r = self.jcall('/api/bank', {'id': 'old.sf2'})
        self.assertEqual((code, r['started']), (200, False))
        self.assertEqual(self.actions.restarts, 0)

    def test_a_second_switch_or_a_preset_change_while_one_runs_is_refused(self):
        self.actions.gate = threading.Event()
        self.jcall('/api/bank', {'id': 'new.sf2'})
        try:
            self.assertEqual(self.jcall('/api/bank', {'id': 'odd.sf2'})[0], 409)
            self.assertEqual(self.jcall('/api/settings', {'piano_prog': 0, 'piano_bank': 0})[0], 409)
            self.assertEqual(self.jcall('/api/state')[1]['banks']['switch']['state'], 'switching')
        finally:
            self.actions.gate.set()
            self.finish_switch()
        self.assertEqual(self.jcall('/api/state')[1]['banks']['switch']['state'], 'done')

    def test_a_preset_change_is_checked_against_the_current_bank(self):
        self.jcall('/api/bank', {'id': 'new.sf2'})
        self.finish_switch()
        code, r = self.jcall('/api/settings', {'piano_bank': 0, 'piano_prog': 90})
        self.assertEqual((code, r['applied']['fx']), (200, True))
        self.assertIn('--piano-prog 90', self.conf_text())
        for bad in ({'piano_prog': 91}, {'piano_bank': 5}, {'piano_prog': 1}, {'piano_prog': 200}):
            with self.subTest(bad):
                self.assertEqual(self.jcall('/api/settings', bad)[0], 400)

    def test_the_token_protects_the_bank_switch_too(self):
        self.start(self.conf_text_base + 'WEB_TOKEN=s3cret\n')
        self.assertEqual(self.call('/api/bank', {'id': 'new.sf2'})[0], 401)
        self.assertEqual(self.actions.restarts, 0)


class InstrumentProfileTests(ServerCase):
    """Every bank remembers its own touch, volume, mechanics and preset; switching brings them back."""

    def setUp(self):
        self.bank_tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.bank_tmp.cleanup)
        d = self.bank_dir = self.bank_tmp.name
        self.mech = make_sf2(os.path.join(d, 'mech.sf2'), presets=[('Hammer', 0, 1)])
        self.a = make_sf2(os.path.join(d, 'a.sf2'), presets=[('A piano', 0, 0)], name='A')
        self.b = make_sf2(os.path.join(d, 'b.sf2'), presets=[('B piano', 0, 0), ('B pad', 0, 90)], name='B')
        self.base = ('BANK_DIR=%s\nPIANO_SF2="%s"\nMECH_SF2="%s"\nGAIN=1.0\nFX_ARGS="--hammer-boost 0"\n' % (d, self.a, self.mech))
        self.start(self.base)

    def settings(self):
        return self.jcall('/api/state')[1]['settings']

    def switch(self, bank_id):
        self.assertEqual(self.jcall('/api/bank', {'id': bank_id})[0], 200)
        self.app.switcher.join(10)

    def test_a_change_is_remembered_for_the_bank_that_is_playing(self):
        self.jcall('/api/settings', {'velocity_curve': 1.5, 'hammer_boost': 4})
        p = profiles.get(self.profile_file, 'a.sf2')
        self.assertEqual((p['velocity_curve'], p['hammer_boost']), (1.5, 4.0))
        self.assertIsNone(profiles.get(self.profile_file, 'b.sf2'))

    def test_device_settings_are_not_remembered_per_bank(self):
        self.jcall('/api/settings', {'polyphony': 64, 'alsa_device': 'plughw:U24'})
        self.assertIsNone(profiles.get(self.profile_file, 'a.sf2'))

    def test_a_bank_never_played_inherits_the_current_settings(self):
        self.jcall('/api/settings', {'velocity_curve': 1.5, 'gain': 1.7})
        self.switch('b.sf2')
        s = self.settings()
        self.assertEqual((s['velocity_curve'], s['gain']), (1.5, 1.7))
        self.assertEqual(profiles.get(self.profile_file, 'b.sf2')['velocity_curve'], 1.5)       # now it has a profile of its own

    def test_each_bank_gets_its_own_settings_back(self):
        self.jcall('/api/settings', {'velocity_curve': 1.5, 'gain': 1.2})                        # A
        self.switch('b.sf2')
        self.jcall('/api/settings', {'velocity_curve': 0.7, 'gain': 2.0, 'hammer_boost': 6})     # B
        self.switch('a.sf2')
        s = self.settings()
        self.assertEqual((s['velocity_curve'], s['gain'], s['hammer_boost']), (1.5, 1.2, 0.0))
        self.switch('b.sf2')
        s = self.settings()
        self.assertEqual((s['velocity_curve'], s['gain'], s['hammer_boost']), (0.7, 2.0, 6.0))
        self.assertIn('GAIN=2', self.conf_text())                                                # the synthesizer starts with it

    def test_the_preset_is_remembered_per_bank_too(self):
        self.switch('b.sf2')
        self.assertEqual(self.jcall('/api/settings', {'piano_bank': 0, 'piano_prog': 90})[0], 200)
        self.switch('a.sf2')
        self.assertEqual(self.settings()['piano_prog'], 0)
        self.switch('b.sf2')
        s = self.settings()
        self.assertEqual((s['piano_bank'], s['piano_prog']), (0, 90))

    def test_a_remembered_preset_that_no_longer_exists_falls_back_to_the_default(self):
        profiles.save(self.profile_file, 'b.sf2', {'piano_bank': 5, 'piano_prog': 77, 'velocity_curve': 0.9})
        self.switch('b.sf2')
        s = self.settings()
        self.assertEqual((s['piano_bank'], s['piano_prog']), (0, 0))
        self.assertEqual(s['velocity_curve'], 0.9)                                                # the rest is still brought back

    def test_values_edited_by_hand_in_the_file_are_not_lost_when_switching_away(self):
        with open(self.conf) as f:
            text = f.read()
        with open(self.conf, 'w') as f:
            f.write(text.replace('--hammer-boost 0', '--hammer-boost 0 --velocity-curve 1.9'))   # not through the page
        self.switch('b.sf2')
        self.assertEqual(profiles.get(self.profile_file, 'a.sf2')['velocity_curve'], 1.9)

    def test_a_failed_switch_puts_the_old_settings_back_too(self):
        self.jcall('/api/settings', {'velocity_curve': 1.5, 'gain': 1.2})
        profiles.save(self.profile_file, 'b.sf2', {'velocity_curve': 0.5, 'gain': 3.0})
        self.actions.ready = [False, True]
        self.switch('b.sf2')
        s = self.settings()
        self.assertEqual((s['velocity_curve'], s['gain']), (1.5, 1.2))
        self.assertIn('PIANO_SF2="%s"' % self.a, self.conf_text())
        self.assertEqual(profiles.get(self.profile_file, 'b.sf2')['velocity_curve'], 0.5)       # B's own profile is untouched

    def test_per_bank_changes_are_refused_while_a_switch_runs_but_device_settings_are_not(self):
        self.actions.gate = threading.Event()
        self.jcall('/api/bank', {'id': 'b.sf2'})
        try:
            self.assertEqual(self.jcall('/api/settings', {'velocity_curve': 1.1})[0], 409)
            self.assertEqual(self.jcall('/api/settings', {'gain': 1.1})[0], 409)
            self.assertEqual(self.jcall('/api/settings', {'polyphony': 64})[0], 200)
        finally:
            self.actions.gate.set()
            self.app.switcher.join(10)

    def test_the_state_says_whether_the_bank_has_remembered_settings(self):
        b = self.jcall('/api/state')[1]['banks']
        self.assertEqual(b['profile'], dict(key='a.sf2', saved=False))
        self.jcall('/api/settings', {'velocity_curve': 1.3})
        self.assertEqual(self.jcall('/api/state')[1]['banks']['profile'], dict(key='a.sf2', saved=True))

    def test_the_profiles_survive_a_restart_of_the_web_service(self):
        self.jcall('/api/settings', {'velocity_curve': 1.3})
        again = web.App(self.conf, self.actions)                 # a new process, same files
        self.assertTrue(again.banks_state()['profile']['saved'])
        self.assertEqual(profiles.get(again.profile_path, 'a.sf2')['velocity_curve'], 1.3)


class GainPipeTests(unittest.TestCase):
    def test_set_gain_writes_the_command_into_the_pipe(self):
        with tempfile.TemporaryDirectory() as tmp:
            fifo = os.path.join(tmp, 'cmd')
            os.mkfifo(fifo, 0o600)
            keep = os.open(fifo, os.O_RDWR)                       # plays the role of the running FluidSynth
            old = os.environ.get('CMD_FIFO')
            os.environ['CMD_FIFO'] = fifo
            try:
                self.assertTrue(web.Actions().set_gain(1.25))
                self.assertEqual(os.read(keep, 100), b'set synth.gain 1.25\n')
            finally:
                os.close(keep)
                if old is None:
                    del os.environ['CMD_FIFO']
                else:
                    os.environ['CMD_FIFO'] = old

    def test_set_gain_reports_failure_when_the_synth_is_not_running(self):
        with tempfile.TemporaryDirectory() as tmp:
            fifo = os.path.join(tmp, 'cmd')
            os.mkfifo(fifo, 0o600)                                # nobody reads it
            old = os.environ.get('CMD_FIFO')
            os.environ['CMD_FIFO'] = fifo
            try:
                self.assertFalse(web.Actions().set_gain(1.25))
            finally:
                if old is None:
                    del os.environ['CMD_FIFO']
                else:
                    os.environ['CMD_FIFO'] = old

    def test_missing_pipe_is_a_failure_not_a_crash(self):
        old = os.environ.get('CMD_FIFO')
        os.environ['CMD_FIFO'] = '/nonexistent/cmd'
        try:
            self.assertFalse(web.Actions().set_gain(1.0))
        finally:
            if old is None:
                del os.environ['CMD_FIFO']
            else:
                os.environ['CMD_FIFO'] = old


class CardsTests(unittest.TestCase):
    def test_cards_skips_hdmi_and_capture_only_cards(self):
        with tempfile.TemporaryDirectory() as a:
            rows = [(0, 'Headphones', 'pcm0p', False), (1, 'vc4hdmi', 'pcm0p', False),
                    (2, 'mk3', 'pcm0c', True), (3, 'U24', 'pcm0p', True)]
            text = ''
            for n, cid, pcm, usb in rows:
                text += ' %d [%-15s]: x - Name %s\n' % (n, cid, cid)
                os.makedirs(os.path.join(a, 'card%d' % n, pcm))
                if usb:
                    open(os.path.join(a, 'card%d' % n, 'usbid'), 'w').close()
            with open(os.path.join(a, 'cards'), 'w') as f:
                f.write(text)
            cards = web.Actions(asound=a).cards()
            self.assertEqual([(c['id'], c['usb']) for c in cards], [('Headphones', False), ('U24', True)])


if __name__ == '__main__':
    unittest.main()
