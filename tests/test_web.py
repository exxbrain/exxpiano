"""Tests for pi/exxpiano-web.py: the HTTP API with the system actions replaced by fakes (no Pi, no root needed)."""
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
spec = importlib.util.spec_from_file_location('piano_web', os.path.join(PI, 'exxpiano-web.py'))
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
        self.monitor_calls = []
        self.host = 'exxpiano'
        self.renamed = []
        self.networks = [dict(ssid='Home', signal=-50, secure=True, freq=2437), dict(ssid='Cafe', signal=-70, secure=False, freq=2412)]
        self.wifi_st = dict(mode='client', busy=None, link=dict(ssid='Home', ip='192.168.1.9', signal=-50), hostname='exxpiano', ap=None,
                            scan=self.networks, scanned_at=5, last_result=None, updated=100)
        self.wifi_saved_list = [dict(ssid='Home', secure=True, hidden=False, priority=0)]
        self.wifi_requests = []
        self.scan_answer = [dict(ssid='Fresh', signal=-40, secure=True, freq=2412)]
        self.nm = False
        self.monitor_answer = dict(now=dict(t=5, cpu=12.5, cores=[10.0, 15.0], temp=55.0, throttle=None), points=[dict(t=5, cpu=12.5, temp=55.0, freq=1200)])

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

    def monitor(self, since=0):
        self.monitor_calls.append(since)
        return self.monitor_answer

    def wifi_status(self):
        return self.wifi_st

    def wifi_saved(self):
        return self.wifi_saved_list

    def wifi_scan(self):
        return self.scan_answer

    def wifi_request(self, request):
        self.wifi_requests.append(request)
        return '123'

    def network_manager_active(self):
        return self.nm

    def installation_id(self):
        return '0123456789ABCDEF'

    def hostname(self):
        return self.host

    def set_hostname(self, name):
        self.renamed.append(name)
        self.host = name
        return name


class ServerCase(unittest.TestCase):
    def start(self, conf=CONF):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.conf = os.path.join(self.tmp.name, 'exxpiano.conf')
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

    def test_the_page_opens_with_a_query_and_a_fragment_as_in_the_qr_codes(self):
        self.start()
        for path in ('/?d=81DE', '/index.html?d=81DE', '/?d=81DE&x=1'):
            with self.subTest(path):
                code, body = self.call(path)
                self.assertEqual(code, 200)
                self.assertIn(b'<title>', body)
        self.assertEqual(self.call('/nothing?d=1')[0], 404)

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

    def test_buffer_of_the_built_in_output_is_stored_and_needs_a_restart(self):
        self.start()
        code, r = self.jcall('/api/settings', {'period_size': 128, 'periods': 4})
        self.assertEqual(code, 200)
        self.assertTrue(r['restart_required'])
        self.assertEqual((r['settings']['period_size'], r['settings']['periods']), (128, 4))
        self.assertIn('PERIOD_SIZE=128', self.conf_text())
        self.assertEqual(self.jcall('/api/state')[1]['settings']['period_size'], 128)
        for bad in ({'period_size': 32}, {'period_size': 9000}, {'periods': 1}):      # outside what FluidSynth accepts
            self.assertEqual(self.jcall('/api/settings', bad)[0], 400)
        self.assertIn('PERIOD_SIZE=128', self.conf_text())

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


class ActionsCacheTests(unittest.TestCase):
    """The page asks for the state every few seconds; the programs behind it are started at most once per cache time."""

    def setUp(self):
        self.calls = []
        self.real_run = web.subprocess.run

        def fake_run(cmd, *a, **kw):
            self.calls.append(cmd[0])

            class R:
                stdout = {'fluidsynth': 'FluidSynth runtime version 2.4.4\n', 'systemctl': 'active\n',
                          'journalctl': 'exxpiano: audio output plughw:CARD=U24 (usb), period 64 x 3\n'}.get(cmd[0], '')
                returncode = 0
            return R()
        web.subprocess.run = fake_run
        self.addCleanup(setattr, web.subprocess, 'run', self.real_run)
        self.actions = web.Actions(asound=tempfile.mkdtemp())

    def test_repeated_questions_start_each_program_once(self):
        a = self.actions
        for _ in range(5):
            self.assertEqual(a.fluidsynth_version(), '2.4.4')
            self.assertEqual(a.service_state('exxpiano'), 'active')
            self.assertEqual(a.service_state('exxpiano-fx'), 'active')
            self.assertEqual(a.audio_output()['period'], 64)
        self.assertEqual(sorted(self.calls), ['fluidsynth', 'journalctl', 'systemctl', 'systemctl'])      # one per unit

    def test_entries_expire(self):
        a = self.actions
        a.service_state('exxpiano')
        with a._cache_lock:
            key = ('service', 'exxpiano')
            a._cache[key] = (a._cache[key][0] - 3, a._cache[key][1])        # older than its 2 s
        a.service_state('exxpiano')
        self.assertEqual(self.calls, ['systemctl', 'systemctl'])

    def test_a_restart_forgets_the_service_state_and_the_output(self):
        a = self.actions
        a.service_state('exxpiano'); a.audio_output(); a.fluidsynth_version()
        popen = web.subprocess.Popen
        web.subprocess.Popen = lambda *x, **kw: None
        self.addCleanup(setattr, web.subprocess, 'Popen', popen)
        a.restart_synth()
        self.calls.clear()
        a.service_state('exxpiano'); a.audio_output(); a.fluidsynth_version()
        self.assertEqual(sorted(self.calls), ['journalctl', 'systemctl'])    # the version of the program does not change with a restart

    def test_readiness_never_uses_the_cached_state(self):
        a = self.actions
        a.service_state('exxpiano')
        self.calls.clear()
        a.synth_pid = lambda: 7
        a._seq_has_synth = lambda: True
        web.time.sleep, orig = (lambda s: None), web.time.sleep
        self.addCleanup(setattr, web.time, 'sleep', orig)
        self.assertTrue(a.wait_synth_ready(1, timeout=30))
        self.assertGreaterEqual(self.calls.count('systemctl'), 2)


class WifiApiTests(ServerCase):
    def post(self, body):
        return self.jcall('/api/wifi', body)

    def test_the_state_describes_the_link_the_saved_networks_and_the_scan(self):
        self.start()
        code, r = self.jcall('/api/wifi')
        self.assertEqual(code, 200)
        self.assertTrue(r['supported'])
        self.assertEqual((r['mode'], r['link']['ssid'], r['hostname']), ('client', 'Home', 'exxpiano'))
        self.assertEqual(r['saved'], [dict(ssid='Home', secure=True, hidden=False)])           # no priority, no key
        self.assertEqual([n['ssid'] for n in r['networks']], ['Home', 'Cafe'])

    def test_without_the_supervisor_the_page_is_told_why(self):
        self.start()
        self.actions.wifi_st = None
        r = self.jcall('/api/wifi')[1]
        self.assertEqual((r['supported'], r['reason']), (False, 'not_running'))
        self.assertEqual(r['saved'][0]['ssid'], 'Home')
        self.actions.nm = True
        self.assertEqual(self.jcall('/api/wifi')[1]['reason'], 'networkmanager')
        code, r = self.post(dict(action='connect', ssid='Home', password='12345678'))
        self.assertEqual(code, 400)
        self.assertEqual(self.actions.wifi_requests, [])

    def test_connect_sends_the_derived_key_never_the_password(self):
        self.start()
        code, r = self.post(dict(action='connect', ssid='IEEE', password='password'))
        self.assertEqual((code, r), (200, dict(accepted=True, id='123')))
        (req,) = self.actions.wifi_requests
        self.assertEqual(req, dict(action='connect', ssid='IEEE', psk='f42c6fc52df0ebef9ebb4b90b38a5f902e83fe1b135a70e23aed762e9710a12e', hidden=False))
        self.assertNotIn('password', json.dumps(req))

    def test_an_open_network_needs_no_password_but_a_secured_one_does(self):
        self.start()
        self.assertEqual(self.post(dict(action='connect', ssid='Cafe'))[0], 200)
        self.assertEqual(self.actions.wifi_requests[-1]['psk'], None)
        n = len(self.actions.wifi_requests)
        code, r = self.post(dict(action='connect', ssid='Home'))
        self.assertEqual(code, 400)
        self.assertIn('password', r['error'])
        self.assertEqual(len(self.actions.wifi_requests), n)
        self.assertEqual(self.post(dict(action='connect', ssid='Unlisted', password='12345678', hidden=True))[0], 200)
        self.assertTrue(self.actions.wifi_requests[-1]['hidden'])

    def test_bad_input_is_refused_and_nothing_is_sent(self):
        self.start()
        for body in (dict(action='connect', ssid='', password='12345678'), dict(action='connect', ssid='x' * 40, password='12345678'),
                     dict(action='connect', ssid='Net', password='short'), dict(action='connect', ssid=5, password='12345678'),
                     dict(action='connect', ssid='Net', password='пароль-пароль'), dict(action='forget', ssid='Unknown'),
                     dict(action='forget'), dict(action='explode'), dict(), [], 'x'):
            with self.subTest(body):
                self.assertEqual(self.post(body)[0], 400)
        self.assertEqual(self.actions.wifi_requests, [])

    def test_forget_only_works_for_a_saved_network(self):
        self.start()
        self.actions.wifi_saved_list.append(dict(ssid='Spare', secure=True, hidden=False, priority=0))
        self.assertEqual(self.post(dict(action='forget', ssid='Spare'))[0], 200)
        self.assertEqual(self.actions.wifi_requests, [dict(action='forget', ssid='Spare')])

    def test_the_setup_network_can_be_started_for_a_while(self):
        self.start()
        self.assertEqual(self.post(dict(action='setup'))[0], 200)
        self.assertEqual(self.actions.wifi_requests, [dict(action='ap', seconds=300)])
        self.actions.wifi_st['mode'] = 'ap'
        self.assertEqual(self.post(dict(action='setup'))[0], 400)
        self.actions.wifi_st.update(mode='client', busy='connect')
        self.assertEqual(self.post(dict(action='setup'))[0], 409)

    def test_the_only_way_in_is_not_forgotten_but_other_networks_are(self):
        self.start()
        code, r = self.post(dict(action='forget', ssid='Home'))                          # connected, the only saved one, no cable
        self.assertEqual(code, 400)
        self.assertIn('only way', r['error'])
        self.assertEqual(self.actions.wifi_requests, [])
        self.actions.wifi_st['cable'] = True
        self.assertEqual(self.post(dict(action='forget', ssid='Home'))[0], 200)
        self.actions.wifi_st['cable'] = False
        self.actions.wifi_saved_list.append(dict(ssid='Spare', secure=True, hidden=False, priority=0))
        self.assertEqual(self.post(dict(action='forget', ssid='Home'))[0], 200)

    def test_a_busy_supervisor_is_not_given_a_second_job(self):
        self.start()
        self.actions.wifi_st['busy'] = 'connect'
        for body in (dict(action='connect', ssid='Cafe'), dict(action='forget', ssid='Home')):
            self.assertEqual(self.post(body)[0], 409)
        self.assertEqual(self.actions.wifi_requests, [])

    def test_scan_is_fresh_in_client_mode_and_cached_in_setup_mode(self):
        self.start()
        r = self.post(dict(action='scan'))[1]
        self.assertEqual(([n['ssid'] for n in r['networks']], r['cached']), (['Fresh'], False))
        self.actions.wifi_st['mode'] = 'ap'
        r = self.post(dict(action='scan'))[1]
        self.assertEqual(([n['ssid'] for n in r['networks']], r['cached']), (['Home', 'Cafe'], True))
        self.actions.wifi_st['mode'] = 'client'
        self.actions.scan_answer = None
        self.assertEqual(self.post(dict(action='scan'))[0], 400)

    def test_the_token_protects_everything_about_wifi(self):
        self.start(CONF + 'WEB_TOKEN=s3cret\n')
        self.assertEqual(self.call('/api/wifi')[0], 401)
        self.assertEqual(self.call('/api/wifi', dict(action='forget', ssid='Home'))[0], 401)
        self.assertEqual(self.actions.wifi_requests, [])
        self.assertEqual(self.call('/api/wifi', headers={'X-Token': 's3cret'})[0], 200)

    def test_the_real_actions_survive_a_machine_without_wifi(self):
        a = web.Actions()
        a.wifi_run_dir = '/nonexistent/run'
        a.wpa_conf = '/nonexistent/wpa.conf'
        self.assertIsNone(a.wifi_status())
        self.assertEqual(a.wifi_saved(), [])


class DeviceNameTests(ServerCase):
    def test_the_state_tells_the_name_and_the_automatic_installation_id(self):
        self.start()
        r = self.jcall('/api/state')[1]
        self.assertEqual(r['hostname'], 'exxpiano')
        self.assertEqual(r['device'], dict(installation_id='0123456789ABCDEF'))

    def test_the_installation_id_is_used_everywhere_without_configuration(self):
        self.start()
        self.assertEqual(self.jcall('/api/qr?kind=join')[1]['ssid'], 'exxpiano-Setup-0123456789ABCDEF')
        self.assertEqual(self.jcall('/api/qr?kind=setup')[1]['text'], 'http://10.42.0.1:8080/?d=0123456789ABCDEF#/wifi')

    def test_rename_stores_a_clean_name_and_the_state_follows(self):
        self.start()
        code, r = self.jcall('/api/device', {'name': '  Piano-Kitchen '})
        self.assertEqual((code, r), (200, dict(hostname='piano-kitchen')))
        self.assertEqual(self.actions.renamed, ['piano-kitchen'])
        self.assertEqual(self.jcall('/api/state')[1]['hostname'], 'piano-kitchen')

    def test_bad_names_are_refused_and_nothing_is_changed(self):
        self.start()
        for bad in ('', ' ', '-x', 'x-', 'a b', 'a_b', 'a.b', 'пианино', 'x' * 64, 'a;reboot', '$(id)', '../x', None, 5, ['a']):
            with self.subTest(bad):
                self.assertEqual(self.jcall('/api/device', {'name': bad})[0], 400)
        for bad in ([], 'x', {}):
            self.assertEqual(self.jcall('/api/device', bad)[0], 400)
        self.assertEqual(self.actions.renamed, [])
        self.assertEqual(self.jcall('/api/device', {'name': 'a' * 63})[0], 200)               # the longest allowed

    def test_the_token_protects_renaming(self):
        self.start(CONF + 'WEB_TOKEN=s3cret\n')
        self.assertEqual(self.call('/api/device', {'name': 'x'})[0], 401)
        self.assertEqual(self.actions.renamed, [])

    def test_the_real_action_runs_hostnamectl_and_keeps_the_hosts_file_when_it_cannot_write_it(self):
        calls = []
        real = web.subprocess.run

        class R:
            returncode, stdout, stderr = 0, '', ''
        web.subprocess.run = lambda cmd, *a, **kw: (calls.append(cmd), R())[1]
        self.addCleanup(setattr, web.subprocess, 'run', real)
        web.Actions().set_hostname('piano-test')
        self.assertEqual(calls[0], ['hostnamectl', 'set-hostname', 'piano-test'])
        self.assertIn(['systemctl', 'try-restart', 'avahi-daemon.service'], calls)
        R.returncode, R.stderr = 1, 'Access denied'
        with self.assertRaises(OSError):
            web.Actions().set_hostname('piano-test')


class QrApiTests(ServerCase):
    def qr(self, query, host=None):
        headers = {'Host': host} if host else {}
        return self.jcall('/api/qr?' + query, headers=headers)

    def test_join_code_carries_the_name_and_password_of_the_setup_network(self):
        self.start()
        code, r = self.qr('kind=join')
        self.assertEqual(code, 200)
        self.assertEqual((r['ssid'], r['password']), ('exxpiano-Setup-0123456789ABCDEF', 'exxpianosetup'))
        self.assertEqual(r['text'], 'WIFI:T:WPA;S:exxpiano-Setup-0123456789ABCDEF;P:exxpianosetup;;')
        self.assertTrue(r['svg'].startswith('<svg') and 'viewBox' in r['svg'])

    def test_a_changed_name_and_password_and_a_bad_password(self):
        self.start(CONF + 'WIFI_AP_SSID=My-Piano\nWIFI_AP_PASSWORD="much better; secret"\n')
        self.assertEqual(self.qr('kind=join')[1]['text'], 'WIFI:T:WPA;S:My-Piano-0123456789ABCDEF;P:much better\\; secret;;')
        self.start(CONF + 'WIFI_AP_PASSWORD=short\n')
        self.assertEqual(self.qr('kind=join')[1]['password'], 'exxpianosetup')                  # the same fallback the supervisor makes

    def test_setup_page_code_uses_the_web_port(self):
        self.start(CONF + 'WEB_PORT=9090\n')
        self.assertEqual(self.qr('kind=setup')[1]['text'], 'http://10.42.0.1:9090/?d=0123456789ABCDEF#/wifi')
        self.start()
        self.assertEqual(self.qr('kind=setup')[1]['text'], 'http://10.42.0.1:8080/?d=0123456789ABCDEF#/wifi')

    def test_page_code_is_the_address_the_browser_used_or_the_known_ip(self):
        self.start()
        self.assertEqual(self.qr('kind=page', 'exxpiano.local:8080')[1]['text'], 'http://exxpiano.local:8080/?d=0123456789ABCDEF#/wifi')
        self.assertEqual(self.qr('kind=page', '192.168.1.95:8080')[1]['text'], 'http://192.168.1.95:8080/?d=0123456789ABCDEF#/wifi')
        r = self.qr('kind=page', 'evil.example/<script>')[1]                                  # an odd Host header is not copied into a code
        self.assertEqual(r['text'], 'http://192.168.1.9:8080/?d=0123456789ABCDEF#/wifi')

    def test_unknown_kinds_and_the_token(self):
        self.start(CONF + 'WEB_TOKEN=s3cret\n')
        self.assertEqual(self.call('/api/qr?kind=join')[0], 401)
        self.assertEqual(self.call('/api/qr?kind=join', headers={'X-Token': 's3cret'})[0], 200)
        self.assertEqual(self.call('/api/qr?kind=nonsense', headers={'X-Token': 's3cret'})[0], 400)
        self.assertEqual(self.call('/api/qr', headers={'X-Token': 's3cret'})[0], 400)


class MonitorEndpointTests(ServerCase):
    def test_the_monitor_endpoint_returns_the_sample_and_the_points(self):
        self.start()
        code, r = self.jcall('/api/monitor')
        self.assertEqual(code, 200)
        self.assertEqual(r['now']['cpu'], 12.5)
        self.assertEqual(r['points'][0]['freq'], 1200)
        self.assertEqual(self.actions.monitor_calls, [0])

    def test_since_is_passed_on_and_checked(self):
        self.start()
        self.jcall('/api/monitor?since=1234')
        self.assertEqual(self.actions.monitor_calls[-1], 1234)
        for bad in ('since=abc', 'since=-5', 'since=1.5'):
            with self.subTest(bad):
                before = len(self.actions.monitor_calls)
                self.assertEqual(self.jcall('/api/monitor?' + bad)[0], 400)
                self.assertEqual(len(self.actions.monitor_calls), before)

    def test_the_token_protects_the_monitor(self):
        self.start(CONF + 'WEB_TOKEN=s3cret\n')
        self.assertEqual(self.call('/api/monitor')[0], 401)
        self.assertEqual(self.call('/api/monitor', headers={'X-Token': 's3cret'})[0], 200)
        self.assertEqual(len(self.actions.monitor_calls), 1)

    def test_the_real_actions_produce_a_sample_from_this_machine(self):
        r = web.Actions().monitor(0)                          # whatever this machine has: nothing may raise
        self.assertIn('cpu', r['now'])
        self.assertIsInstance(r['points'], list)


class DeviceProfileTests(ServerCase):
    """The buffers belong to the audio interface: each one gets its own back when it is chosen again."""
    U24 = 'plughw:CARD=U24'

    def put(self, body):
        code, r = self.jcall('/api/settings', body)
        self.assertEqual(code, 200)
        return r['settings']

    def test_each_interface_brings_its_own_buffers_back(self):
        self.start()
        self.put({'alsa_device': self.U24, 'usb_period_size': 128, 'usb_periods': 4})
        self.put({'alsa_device': 'auto', 'period_size': 64, 'periods': 2})
        s = self.put({'alsa_device': self.U24})                                  # only the interface is named
        self.assertEqual((s['usb_period_size'], s['usb_periods']), (128, 4))
        s = self.put({'alsa_device': 'auto'})
        self.assertEqual((s['period_size'], s['periods']), (64, 2))
        self.assertIn('PERIOD_SIZE=64', self.conf_text())

    def test_a_request_that_names_buffers_wins_over_the_remembered_ones(self):
        self.start()
        self.put({'alsa_device': self.U24, 'usb_period_size': 128})
        self.put({'alsa_device': 'auto'})
        s = self.put({'alsa_device': self.U24, 'usb_period_size': 256})
        self.assertEqual(s['usb_period_size'], 256)
        self.assertEqual(self.jcall('/api/state')[1]['device_profiles'][self.U24]['usb_period_size'], 256)

    def test_an_interface_never_used_keeps_the_current_buffers(self):
        self.start()
        self.put({'period_size': 128})
        s = self.put({'alsa_device': 'plughw:CARD=Other'})
        self.assertEqual(s['period_size'], 128)

    def test_the_state_lists_what_is_remembered_and_the_bank_profiles_stay_apart(self):
        self.start()
        self.assertEqual(self.jcall('/api/state')[1]['device_profiles'], {})
        self.put({'alsa_device': self.U24, 'usb_period_size': 128})
        remembered = self.jcall('/api/state')[1]['device_profiles']
        self.assertEqual(sorted(remembered), ['auto', self.U24])
        self.assertEqual(sorted(remembered[self.U24]), ['period_size', 'periods', 'usb_period_size', 'usb_periods'])
        self.assertFalse(os.path.exists(self.app.profile_path))                # nothing leaked into the per-bank file

    def test_changes_that_are_not_about_the_interface_remember_nothing(self):
        self.start()
        self.put({'gain': 1.5, 'polyphony': 64})
        self.assertEqual(self.jcall('/api/state')[1]['device_profiles'], {})

    def test_a_bad_interface_is_refused_and_nothing_is_stored(self):
        self.start()
        self.assertEqual(self.jcall('/api/settings', {'alsa_device': 'hw:1; rm -rf /'})[0], 400)
        self.assertEqual(self.jcall('/api/state')[1]['device_profiles'], {})


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
