"""scripts/demo-web.py: the page on a computer without a Raspberry Pi. Starts the real script and asks its API."""
import json
import os
import socket
import subprocess
import sys
import time
import unittest
import urllib.request

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
SCRIPT = os.path.join(ROOT, 'scripts', 'demo-web.py')


def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


class Demo:
    def __init__(self, *args):
        self.port = free_port()
        self.proc = subprocess.Popen([sys.executable, SCRIPT, '--port', str(self.port)] + list(args), stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        for _ in range(60):
            try:
                self.get('/api/ping')
                return
            except Exception:                                  # noqa: BLE001 - not up yet
                time.sleep(0.1)
        self.stop()
        raise RuntimeError('the demo did not start')

    def get(self, path):
        with urllib.request.urlopen('http://127.0.0.1:%d%s' % (self.port, path), timeout=5) as r:
            return r.read()

    def json(self, path, body=None):
        req = urllib.request.Request('http://127.0.0.1:%d%s' % (self.port, path), data=None if body is None else json.dumps(body).encode(),
                                     headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())

    def stop(self):
        self.proc.terminate()
        try:
            self.proc.wait(5)
        except subprocess.TimeoutExpired:
            self.proc.kill()


class DemoTests(unittest.TestCase):
    def test_the_page_and_every_part_of_the_api_answer_without_a_pi(self):
        demo = Demo('--installation-id', '1111222233334444')
        self.addCleanup(demo.stop)
        self.assertIn(b'id="drawer"', demo.get('/'))
        state = demo.json('/api/state')
        self.assertEqual((state['hostname'], state['device']['installation_id']), ('exxpiano', '1111222233334444'))
        self.assertEqual([b['id'] for b in state['banks']['banks']], ['Salamander Grand.sf2', 'Upright.sf2'])
        self.assertEqual(state['status']['synth'], 'active')
        mon = demo.json('/api/monitor')
        self.assertGreater(mon['now']['cpu'], 0)
        self.assertEqual(mon['now']['audio'][0]['buffer'], 192)
        self.assertEqual(demo.json('/api/wifi')['mode'], 'client')
        self.assertEqual(demo.json('/api/qr?kind=join')['text'], 'WIFI:T:WPA;S:exxpiano-Setup-1111222233334444;P:exxpianosetup;;')

    def test_settings_the_bank_the_name_and_wifi_work_on_the_simulation(self):
        demo = Demo()
        self.addCleanup(demo.stop)
        self.assertEqual(demo.json('/api/settings', {'velocity_curve': 1.3})['settings']['velocity_curve'], 1.3)
        self.assertEqual(demo.json('/api/device', {'name': 'piano-demo'}), {'hostname': 'piano-demo'})
        self.assertEqual(demo.json('/api/state')['hostname'], 'piano-demo')
        demo.json('/api/bank', {'id': 'Upright.sf2'})
        for _ in range(40):
            if demo.json('/api/state')['banks']['switch']['state'] == 'done':
                break
            time.sleep(0.25)
        self.assertEqual(demo.json('/api/state')['banks']['current'], 'Upright.sf2')
        demo.json('/api/wifi', {'action': 'connect', 'ssid': 'Cafe'})
        for _ in range(40):
            w = demo.json('/api/wifi')
            if not w['busy'] and w['last_result']:
                break
            time.sleep(0.25)
        self.assertEqual((w['last_result']['ok'], w['link']['ssid']), (True, 'Cafe'))
        demo.json('/api/wifi', {'action': 'connect', 'ssid': 'will fail', 'password': '12345678'})
        for _ in range(40):
            w = demo.json('/api/wifi')
            if not w['busy'] and w['last_result']['ssid'] == 'will fail':
                break
            time.sleep(0.25)
        self.assertFalse(w['last_result']['ok'])

    def test_the_setup_mode_uses_the_installation_id(self):
        demo = Demo('--mode', 'ap')
        self.addCleanup(demo.stop)
        wifi = demo.json('/api/wifi')
        self.assertEqual((wifi['mode'], wifi['ap']['ssid']), ('ap', 'exxpiano-Setup-0123456789ABCDEF'))
        device = demo.json('/api/state')['device']
        self.assertEqual(device, {'installation_id': '0123456789ABCDEF'})


if __name__ == '__main__':
    unittest.main()
