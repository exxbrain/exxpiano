#!/usr/bin/env python3
"""The exxpiano web page on your computer, without a Raspberry Pi: a simulated device (services, CPU, temperature, Wi-Fi, banks, audio).

  python3 scripts/demo-web.py                    # http://127.0.0.1:8080/
  python3 scripts/demo-web.py --mode ap          # the device in setup mode (its own Wi-Fi), the page opens on the Wi-Fi tab
  python3 scripts/demo-web.py --installation-id 012ABC --open

Everything works as on the device (a USB drive with two banks is "plugged in": import one on the Piano bank page): move the sliders, change the bank, rename the device, add or forget a Wi-Fi network (a name with "fail" in it does not connect;
"Cafe" is open), start the setup network, scan the QR codes. Nothing is written outside a temporary folder and nothing is sent anywhere.
It runs the real code of exxpiano-web.py and the real page; only what touches the machine (systemctl, ALSA, /proc, Wi-Fi) is simulated.
"""
import argparse
import importlib.util
import math
import os
import random
import shutil
import sys
import tempfile
import threading
import time
import webbrowser
from http.server import ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PI = os.path.join(ROOT, 'pi')
sys.path.insert(0, PI)
sys.path.insert(0, os.path.join(ROOT, 'tests'))
spec = importlib.util.spec_from_file_location('piano_web', os.path.join(PI, 'exxpiano-web.py'))
web = importlib.util.module_from_spec(spec)
spec.loader.exec_module(web)
import midilib  # noqa: E402
import midiplayer  # noqa: E402
import usblib  # noqa: E402
from sf2fixture import make_sf2  # noqa: E402


spec = importlib.util.spec_from_file_location('exxpiano_demo_midi', os.path.join(PI, 'exxpiano-demo-midi.py'))
demo_midi = importlib.util.module_from_spec(spec)
spec.loader.exec_module(demo_midi)


class DemoActions(web.Actions):
    """What touches the machine is replaced by a simulation that moves a little, so the page has something to show."""

    def __init__(self, args):
        super().__init__()
        self.args, self.t0, self.rng = args, time.time(), random.Random(7)
        self.host = args.hostname
        self.mode = args.mode
        self.busy, self.busy_until, self.ap_until = None, 0.0, 0.0
        self.link = None if args.mode == 'ap' else dict(ssid='Home', ip='192.168.1.95', signal=-52)
        self.saved = [dict(ssid='Home', secure=True, hidden=False, priority=1), dict(ssid='Dacha', secure=True, hidden=True, priority=0)]
        self.scan = [dict(ssid='Home', signal=-48, secure=True, freq=2437), dict(ssid='Cafe', signal=-63, secure=False, freq=2412),
                     dict(ssid='Neighbour', signal=-74, secure=True, freq=2462), dict(ssid='Studio 5G', signal=-80, secure=True, freq=5180)]
        self.last_result = None
        self.cable = False
        self.history = []

    # ---- the system
    def version(self):
        return super().version() or 'demo'

    def fluidsynth_version(self):
        return '2.4.4'

    def mem_total(self):
        return 954 << 20

    def mem_available(self):
        return 454 << 20

    def synth_pid(self):
        return 4242

    def wait_synth_ready(self, old_pid, timeout=150):
        time.sleep(2)                                     # the real one waits for the banks to load
        return True

    def service_state(self, unit):
        return 'active'

    def restart_synth(self):
        print('demo: the synthesizer would restart now')

    def set_gain(self, value):
        print('demo: gain %.2f' % value)
        return True

    def cards(self):
        return [dict(id='U24', name='ZOOM U-24', usb=True), dict(id='Headphones', name='bcm2835 Headphones', usb=False)]

    def audio_output(self):
        return dict(device='plughw:CARD=U24', kind='usb', period=64, periods=3)

    def hostname(self):
        return self.host

    def set_hostname(self, name):
        self.host = name
        print('demo: the device is now called %s' % name)
        return name

    def wifi_mac(self):
        return 'b8:27:eb:d2:81:de'

    def installation_id(self):
        return self.args.installation_id

    def network_manager_active(self):
        return False

    # ---- the monitor
    def monitor(self, since=0):
        now, k = time.time(), time.time() - self.t0
        cpu = max(2.0, min(97.0, 28 + 22 * math.sin(k / 9) + 9 * math.sin(k / 2.3) + self.rng.uniform(-3, 3)))
        temp = round(49 + 0.18 * min(k, 120) + 4 * math.sin(k / 40) + self.rng.uniform(-0.4, 0.4), 1)
        warm = temp > 62
        point = dict(t=int(now * 1000), cpu=round(cpu, 1), temp=temp, freq=1000 if warm else 1200)
        self.history = (self.history + [point])[-150:]
        cores = [round(max(0, min(100, cpu + d)), 1) for d in (-8, 12, -3, 22)]
        throttle = dict(raw=0x50000, now=[], past=['undervoltage', 'throttled']) if self.args.throttled else dict(raw=0, now=[], past=[])
        snap = dict(t=point['t'], cpu=point['cpu'], cores=cores, procs=dict(synth=round(cpu * 1.6, 1), bridge=round(self.rng.uniform(1, 5), 1), web=round(self.rng.uniform(0, 2), 1)),
                    temp=temp, freq=point['freq'], freq_max=1200, mem=dict(total=931, used=int(500 + 30 * math.sin(k / 30))), load=[round(cpu / 40, 2), 0.7, 0.5], throttle=throttle,
                    audio=[dict(card=1, id='U24', name='ZOOM U-24', device=0, state='RUNNING', format='S32_LE', channels=4, rate=44100, period=64, buffer=192, period_ms=1.45,
                                buffer_ms=4.35, delay=int(120 + 30 * math.sin(k)), avail=72, avail_max=100,
                                usb=dict(sync='ASYNC', rates=[44100, 48000, 88200, 96000], bits=24, momentary=44100, status='Running'))])
        return dict(now=snap, points=[p for p in self.history if p['t'] > since])

    def errors(self):
        now = int(time.time() * 1000)
        return [dict(t=now - 90000, unit='exxpiano-wifi', level='warning', text="could not connect to 'Dacha', restoring the previous settings"),
                dict(t=now - 20000, unit='exxpiano', level='error', text='no sound card found: connect a USB sound card or use the 3.5 mm jack')]

    # ---- Wi-Fi: a small state machine that answers the requests of the page
    def _tick(self):
        now = time.time()
        if self.busy and now >= self.busy_until:
            self.busy = None
        if self.mode == 'ap' and self.ap_until and now >= self.ap_until:
            self.mode, self.link = 'client', dict(ssid=(self.saved[0]['ssid'] if self.saved else 'Home'), ip='192.168.1.95', signal=-52)
        if self.mode == 'client' and not self.saved and not self.cable and self.link is None:
            self.mode = 'ap'

    def wifi_status(self):
        self._tick()
        ap = dict(ssid=web.wifilib.ap_ssid('exxpiano-Setup', self.args.installation_id))
        return dict(mode=self.mode, busy=self.busy, link=self.link, cable=self.cable, hostname=self.host, ap=ap if self.mode == 'ap' else None,
                    scan=self.scan, scanned_at=int(time.time()), last_result=self.last_result, updated=int(time.time()))

    def wifi_saved(self):
        return list(self.saved)

    def wifi_scan(self):
        time.sleep(1.2)
        return [dict(n, signal=n['signal'] + self.rng.randint(-3, 3)) for n in self.scan]

    def wifi_request(self, request):
        action, now = request.get('action'), int(time.time())
        if action == 'connect':
            self.busy, self.busy_until = 'connect', time.time() + 3.5
            ssid = request.get('ssid')
            threading.Timer(3.5, self._finish_connect, [ssid, request]).start()
        elif action == 'forget':
            self.saved = [n for n in self.saved if n['ssid'] != request.get('ssid')]
            if self.link and self.link['ssid'] == request.get('ssid'):
                self.link = None
            self.last_result = dict(ok=True, kind='forget', ssid=request.get('ssid'), error=None, at=now)
        elif action == 'ap':
            self.mode, self.link, self.ap_until = 'ap', None, time.time() + float(request.get('seconds', 300))
        return str(now)

    def _finish_connect(self, ssid, request):
        now = int(time.time())
        if 'fail' in (ssid or '').lower():
            self.last_result = dict(ok=False, kind='connect', ssid=ssid, error='wrong password, or the network is out of range', at=now)
            return
        self.saved = [n for n in self.saved if n['ssid'] != ssid] + [dict(ssid=ssid, secure=bool(request.get('psk')), hidden=bool(request.get('hidden')), priority=9)]
        self.mode, self.link, self.ap_until = 'client', dict(ssid=ssid, ip='192.168.1.%d' % self.rng.randint(20, 200), signal=-55), 0
        self.last_result = dict(ok=True, kind='connect', ssid=ssid, error=None, at=now)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--port', type=int, default=8080)
    ap.add_argument('--bind', default='127.0.0.1', help='127.0.0.1 (this computer only); 0.0.0.0 to look at it from a phone on the same network')
    ap.add_argument('--mode', choices=('client', 'ap'), default='client', help='ap: the device has no network and runs its own (setup mode)')
    ap.add_argument('--hostname', default='exxpiano')
    ap.add_argument('--installation-id', default='012ABC', help='6 hexadecimal characters, generated automatically on a real device')
    ap.add_argument('--throttled', action='store_true', help='show that the board was throttled earlier (under-voltage)')
    ap.add_argument('--token', default='', help='ask for this access token, as WEB_TOKEN does')
    ap.add_argument('--open', action='store_true', help='open the page in the browser')
    args = ap.parse_args()

    tmp = tempfile.mkdtemp(prefix='piano-demo-', dir='/tmp' if os.path.isdir('/tmp') else None)
    banks = os.path.join(tmp, 'banks')
    os.makedirs(banks)
    make_sf2(os.path.join(banks, 'mech.sf2'), presets=[('Hammer', 0, 1)], name='Mechanics', sample_bytes=2000)
    make_sf2(os.path.join(banks, 'Salamander Grand.sf2'), presets=[('Grand Piano', 0, 0), ('Bright', 0, 5), ('Felt', 3, 0)], name='Salamander Grand Piano', sample_bytes=900000)
    make_sf2(os.path.join(banks, 'Upright.sf2'), presets=[('Upright', 0, 0)], name='Upright piano', sample_bytes=300000)
    for name, megabytes in (('mech.sf2', 36), ('Salamander Grand.sf2', 695), ('Upright.sf2', 237)):          # sparse padding: the sizes of the real banks, no disk used
        os.truncate(os.path.join(banks, name), megabytes << 20)
    # a simulated USB drive with two banks in piano-banks/ (the import works for real: it copies into a temporary library)
    drive = os.path.join(tmp, 'usb-drive')
    os.makedirs(os.path.join(drive, 'piano-banks'))
    make_sf2(os.path.join(drive, 'piano-banks', 'Kawai Grand.sf2'), presets=[('Kawai Grand', 0, 0)], name='Kawai Grand', sample_bytes=600000)
    make_sf2(os.path.join(drive, 'piano-banks', 'Electric Piano.sf2'), presets=[('Electric', 0, 4)], name='Electric Piano', sample_bytes=250000)
    # the real pieces of assets/midi are on the drive; one is already in the library (as after the first installation of the package)
    os.makedirs(os.path.join(drive, 'piano-midi'))
    for name in os.listdir(os.path.join(ROOT, 'assets', 'midi')):
        if name.endswith('.mid'):
            shutil.copy(os.path.join(ROOT, 'assets', 'midi', name), os.path.join(drive, 'piano-midi', name))
    first = os.path.join(tmp, 'first-piece')
    os.makedirs(first)
    shutil.copy(os.path.join(ROOT, 'assets', 'midi', 'liszt-un-sospiro-2011.mid'), first)
    demo_midi.import_demo(first, os.path.join(tmp, 'midi'))
    usb_status = os.path.join(tmp, 'usb-status.json')
    usblib._write_json(usb_status, dict(drives=[dict(id='/dev/sda1', label='PIANO-BANKS', fs='vfat', mounted=True, mountpoint=drive, reason=None)]))
    conf = os.path.join(tmp, 'exxpiano.conf')
    lines = ['PIANO_SF2=%s' % os.path.join(banks, 'Salamander Grand.sf2'), 'MECH_SF2=%s' % os.path.join(banks, 'mech.sf2'), 'BANK_DIR=%s' % banks, 'ALSA_DEVICE=auto',
             'GAIN=1.0', 'POLYPHONY=128', 'USB_PERIOD_SIZE=64', 'USB_PERIODS=3', 'REVERB=0', 'FX_ARGS="--velocity-curve 1 --hammer-boost 1.5 --hammer-exp 0.8 --random 1.6 --res-boost 0 --pedal-boost 4.5"',
             'PROFILE_FILE=%s' % os.path.join(tmp, 'profiles.json'), 'WEB_PORT=%d' % args.port,
             'IMPORT_DIR=%s' % os.path.join(tmp, 'library'), 'USB_STATUS=%s' % usb_status,
             'MIDI_DIR=%s' % os.path.join(tmp, 'midi'), 'MIDI_RUN_DIR=%s' % os.path.join(tmp, 'midi-run')]
    if args.token:
        lines.append('WEB_TOKEN=%s' % args.token)
    with open(conf, 'w') as f:
        f.write('\n'.join(lines) + '\n')

    app = web.App(conf, DemoActions(args))
    # the real player of the bridge, with a synthesizer that is not there: it reads the requests and publishes the status as on the device
    player = midiplayer.Player(os.path.join(tmp, 'midi'), os.path.join(tmp, 'midi-run'), lambda *e: None, lambda pedal: None, lambda: 'demo')
    threading.Thread(target=player.run, daemon=True).start()
    server = ThreadingHTTPServer((args.bind, args.port), web.make_handler(app))
    url = 'http://%s:%d/%s' % ('127.0.0.1' if args.bind in ('0.0.0.0', '127.0.0.1') else args.bind, args.port, '#/wifi' if args.mode == 'ap' else '')
    print('Demo of exxpiano (a simulated device: nothing is changed on this computer)')
    print('  open', url, ' | stop with Ctrl+C')
    if args.open:
        threading.Timer(0.8, webbrowser.open, [url]).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\nstopped')


if __name__ == '__main__':
    main()
