#!/usr/bin/env python3
"""exxpiano-web: a small web interface for exxpiano (standard library only).

  http://<pi>:8080/        the control page
  GET  /api/state          settings, their ranges, service status, sound cards
  POST /api/settings       {"velocity_curve": 0.8, ...}  - validated, stored in /etc/exxpiano.conf, applied
  GET  /api/levels         the level of the left and the right channel at the output (peak, held peak, clipped samples), when the meter runs
  POST /api/output-volume  {"db": -12}  - the hardware volume of the sound card that plays (dB, stored for the next boot); the state tells it as output_volume
  POST /api/restart        restart exxpiano (needed after changing audio output / buffer / reverb / polyphony)
  POST /api/bank           {"id": "<file name in the bank library>"}  - switch the piano bank (rolls back if it does not come up)
                           {"action": "delete", "id": "<bank>"}  - remove a bank that is not in use (not the current one, the last good one or the mechanics bank)
  GET  /api/usb            the USB drive (label, state, the SoundFont files of piano-banks/) and the state of the import
  POST /api/usb            {"action": "import", "name": "<file listed on the drive>"} | {"action": "delete", "id": "i-<16 hex>"}  - copy a bank from the drive / remove an imported one
  GET  /api/midi           MIDI files: the ones on the drive (piano-midi/), the library, the player (state, position), the import
  POST /api/midi           {"action": "import", "name"} | {"action": "delete", "id"} | {"action": "play", "id", "loop"} | {"action": "seek", "id", "position"} | {"action": "stop"}
  GET  /api/wifi           Wi-Fi: link, saved networks, scan results, access point (the setup mode), result of the last change
  POST /api/device         {"name": "piano-kitchen"}  - rename the device (its network name, http://<name>.local:8080/); useful with several devices
  POST /api/wifi           {"action": "scan"} | {"action": "connect", "ssid", "password", "hidden"} | {"action": "forget", "ssid"}
  GET  /api/qr             ?kind=join|setup|page  - {text, svg}: a QR code to join the setup network, to open its page, to open this page (the
                           page codes carry ?d=<installation ID>: the page warns when it is a different installation)
  GET  /api/errors                 - warnings and errors of the piano services since the boot (newest last)
  GET  /api/monitor        ?since=<ms>  - CPU, temperature, frequency, memory, throttling, load of the piano services, chart points since <ms>

Settings that can be changed are whitelisted with ranges in exxpianoconf.py. Optional WEB_TOKEN, WEB_PORT and WEB_BIND
come from /etc/exxpiano.conf. Without WEB_TOKEN anybody on your network can change the settings (nothing else).
"""
import hmac
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import bankimport
import banklib
import exxpianoconf
import midilib
import midiplayer
import oslib
import profiles
import base64
import qrlib
import sysmon
import usblib
import wifilib

WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'web')
MAX_BODY = 4096
HOSTNAME_RE = wifilib.HOSTNAME_RE
ERROR_UNITS = ('exxpiano', 'exxpiano-fx', 'exxpiano-web', 'exxpiano-wifi', 'exxpiano-usb')
OTHER_MANAGERS = ('networkmanager', 'iwd', 'connman')        # OpenRC services of network managers that own Wi-Fi themselves
ERROR_LIMIT = 40
OOM_RE = re.compile(r'Out of memory: Killed process \d+ \(([^)]*)\).*?anon-rss:(\d+)kB')
OUTPUT_RE = re.compile(r'audio output (\S+) \((\w+)\), period (\d+) x (\d+)')

MIXER_CONTROLS = ('PCM', 'Speaker', 'Headphone', 'Master')           # the playback volume controls of a sound card, in the order they are tried
MIXER_RE = re.compile(r'Playback\s+(-?\d+)\s+\[(\d+)%\]\s+\[(-?[\d.]+)dB\]')
MIXER_LIMITS_RE = re.compile(r'Limits:\s*Playback\s+(-?\d+)\s*-\s*(-?\d+)')
CARD_RE = re.compile(r'CARD=([A-Za-z0-9_.-]+)')
VOLUME_FLOOR_DB = -60.0                                                # the slider does not go down to -102 dB: there is nothing to hear below this


def parse_amixer(text):
    """`amixer sget <control>` -> dict(db, lo, hi) of a playback control that has a dB scale, else None. lo/hi are in dB (hi is 0 when the unit of the
    limits is not hundredths of a dB)."""
    m = MIXER_RE.search(text)
    if not m:
        return None
    raw, db = int(m.group(1)), float(m.group(3))
    lim = MIXER_LIMITS_RE.search(text)
    hi = 0.0
    if lim and abs(raw / 100.0 - db) < 0.06:                          # the raw unit is 0.01 dB (the Raspberry Pi's own jack)
        hi = int(lim.group(2)) / 100.0
    return dict(db=db, lo=VOLUME_FLOOR_DB, hi=hi)


class Actions:
    """Everything that touches the system; tests replace this."""

    def __init__(self, asound=None, log_files=None, proc='/proc'):
        self.asound = asound or os.environ.get('ASOUND_DIR', '/proc/asound')
        self.log_files = log_files or oslib.LOG_FILES          # the system log of busybox syslogd (see oslib.py)
        self.proc = proc
        self._monitor = sysmon.Monitor(asound=self.asound)
        self.wifi_run_dir = os.environ.get('EXXPIANO_WIFI_RUN', wifilib.RUN_DIR)
        self.wifi_iface = os.environ.get('WIFI_IFACE', 'wlan0')
        self.wpa_conf = os.environ.get('WIFI_WPA_CONF', '/etc/wpa_supplicant/wpa_supplicant.conf')
        self.scan_error = None                 # why the last scan of the page failed
        self._cache = {}                       # key -> (when, value): the page asks every few seconds, programs are started at most this often
        self._cache_lock = threading.Lock()

    def _cached(self, key, ttl, produce):
        now = time.monotonic()
        with self._cache_lock:
            hit = self._cache.get(key)
            if hit and now - hit[0] < ttl:
                return hit[1]
        value = produce()                      # outside the lock: a slow program must not block the others
        with self._cache_lock:
            self._cache[key] = (time.monotonic(), value)
        return value

    def _forget(self, *keys):
        with self._cache_lock:
            for k in list(self._cache):
                if k in keys or (isinstance(k, tuple) and k[0] in keys):
                    del self._cache[k]

    def version(self):
        """Installed package version: VERSION next to the program (installed) or in the repository root."""
        here = os.path.dirname(os.path.abspath(__file__))
        for path in (os.path.join(here, 'VERSION'), os.path.join(here, '..', 'VERSION')):
            try:
                with open(path, encoding='utf-8') as f:
                    text = f.read().strip()
                    if text:
                        return text
            except OSError:
                pass
        return None

    def fluidsynth_version(self):
        return self._cached('fluidsynth', 600, self._fluidsynth_version)

    def _fluidsynth_version(self):
        try:
            r = subprocess.run(['fluidsynth', '--version'], capture_output=True, text=True, timeout=3)
        except (OSError, subprocess.SubprocessError):
            return None
        m = re.search(r'version (\S+)', r.stdout)
        return m.group(1) if m else None

    def mem_total(self):
        return banklib.mem_total_bytes()

    def mem_available(self):
        return banklib.mem_available_bytes()

    def synth_pid(self):
        return oslib.synth_pid(self.proc)

    def _seq_has_synth(self):
        try:
            with open(os.path.join(self.asound, 'seq', 'clients'), encoding='utf-8', errors='replace') as f:
                return 'FLUID Synth' in f.read()
        except OSError:
            return False

    def wait_synth_ready(self, old_pid, timeout=150):
        """True when a NEW synthesizer process runs and has finished loading its banks (its ALSA sequencer client exists) and
        stays that way for two polls. False on timeout or when it keeps being restarted (a bank it cannot load)."""
        t0, stable, seen = time.time(), 0, set()
        while time.time() - t0 < timeout:
            time.sleep(2)
            pid = self.synth_pid()
            if pid and pid != old_pid:
                seen.add(pid)
            if len(seen) >= 3:
                return False                                   # flapping: crashing and being restarted
            if pid and pid != old_pid and self._service_state('exxpiano') == 'active' and self._seq_has_synth():     # never the cached answer
                stable += 1
                if stable >= 2:
                    return True
            else:
                stable = 0
        return False

    def service_state(self, unit):
        return self._cached(('service', unit), 2, lambda: self._service_state(unit))

    def _service_state(self, unit):
        return oslib.service_state(unit)

    def log(self):
        """The system log of this boot, parsed (see oslib.read_messages); None when it cannot be read."""
        return self._cached('log', 5, lambda: oslib.read_messages(self.log_files))

    def monitor(self, since=0):
        return self._monitor.get(since)

    def levels(self):
        """What exxpiano-meter measures at the output: {t, left{peak, hold, clipped}, right{...}, clip_ago}; None when the meter is not running."""
        try:
            with open(os.environ.get('METER_STATUS', '/run/exxpiano-meter/levels.json'), encoding='ascii') as f:
                st = json.load(f)
            return st if time.time() - float(st['t']) < 2.0 else None
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def errors(self):
        """Warnings, errors and the notes of our own scripts the piano services logged since the boot: [{t (ms), unit, level, text}], newest
        last. None when the system log cannot be read."""
        return self._cached('errors', 5, self._errors)

    def _errors(self):
        entries = self.log()
        if entries is None:
            return None
        found = oslib.service_messages(entries, ERROR_UNITS, limit=ERROR_LIMIT)
        # the kernel's own lines: it says when it killed a program for lack of memory
        return sorted(found + parse_oom(oslib.kernel_messages(entries)), key=lambda e: e['t'])[-ERROR_LIMIT:]

    # ---- Wi-Fi (the supervisor exxpiano-wifi does the changes; this only reads its status and sends requests)
    def wifi_status(self):
        st = wifilib.read_status(self.wifi_run_dir)
        return st if st and time.time() - st.get('updated', 0) < 30 else None      # an old file: the supervisor is not running

    def wifi_saved(self):
        try:
            with open(self.wpa_conf, encoding='utf-8') as f:
                return wifilib.networks(f.read())
        except OSError:
            return []

    def wifi_scan(self):
        def run(cmd, timeout):
            try:
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
                return r.returncode, r.stdout + r.stderr
            except (OSError, subprocess.SubprocessError) as exc:
                return 127, str(exc)
        found, self.scan_error = wifilib.scan_networks(run, self.wifi_iface, tries=2, pause=1.0)
        return found

    def wifi_request(self, request):
        return wifilib.post_request(self.wifi_run_dir, request)

    def hostname(self):
        return socket.gethostname()

    def set_hostname(self, name):
        def run(cmd):
            try:
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
                return r.returncode, r.stdout + r.stderr
            except (OSError, subprocess.SubprocessError) as exc:
                return 127, str(exc)
        wifilib.apply_hostname(name, run)
        return socket.gethostname()

    def installation_id(self):
        return wifilib.installation_id()

    def network_manager_active(self):
        """Another network manager owns Wi-Fi (the Wi-Fi supervisor then stays out of the way)."""
        return any(self._service_state(name) in ('active', 'activating') for name in OTHER_MANAGERS)

    def restart_synth(self):
        self._forget('service', 'output', 'log')         # the answers are about to change
        oslib.restart('exxpiano')

    def output_volume(self, card):
        """The hardware volume of a sound card: dict(control, db, lo, hi), or None when the card has no playback volume that has a dB scale."""
        for control in MIXER_CONTROLS:
            try:
                r = subprocess.run(['amixer', '-c', card, 'sget', control], capture_output=True, text=True, timeout=3)
            except (OSError, subprocess.SubprocessError):
                return None
            found = parse_amixer(r.stdout) if r.returncode == 0 else None
            if found:
                return dict(found, control=control)
        return None

    def set_output_volume(self, card, control, db):
        """Set the volume of the card's control to `db` and remember it for the next boot. True on success."""
        try:
            r = subprocess.run(['amixer', '-c', card, '--', 'sset', control, '%.1fdB' % db], capture_output=True, text=True, timeout=3)
            if r.returncode != 0:
                return False
            subprocess.run(['alsactl', 'store'], capture_output=True, timeout=5)          # the stored state is what alsa-restore puts back at boot
            return True
        except (OSError, subprocess.SubprocessError):
            return False

    def set_gain(self, value):
        """Live master gain: write a shell command into the pipe FluidSynth reads (root-only, no network port).
        True on success. (`set synth.gain` is the command that really changes it; plain `gain` does not.)"""
        fifo = os.environ.get('CMD_FIFO', '/run/exxpiano.cmd')
        try:
            fd = os.open(fifo, os.O_WRONLY | os.O_NONBLOCK)      # fails with ENXIO when nobody reads (synth is down)
        except OSError:
            return False
        try:
            os.write(fd, ('set synth.gain %s\n' % exxpianoconf._fmt(float(value))).encode('ascii'))
            return True
        except OSError:
            return False
        finally:
            os.close(fd)

    def cards(self):
        """Sound cards that can play audio: [{id, name, usb}]."""
        result = []
        try:
            with open(os.path.join(self.asound, 'cards'), encoding='utf-8') as f:
                text = f.read()
        except OSError:
            return result
        for m in re.finditer(r'^\s*(\d+) \[([^\]]*)\]:\s*(.*)$', text, re.M):
            num, cid, name = m.group(1), m.group(2).strip(), m.group(3).strip()
            card = os.path.join(self.asound, 'card' + num)
            try:
                if not any(n.startswith('pcm') and n.endswith('p') for n in os.listdir(card)):
                    continue                                 # no playback (a keyboard's own USB audio, ...)
            except OSError:
                continue
            if 'hdmi' in cid.lower():
                continue
            result.append(dict(id=cid, name=name, usb=os.path.exists(os.path.join(card, 'usbid'))))
        return result

    def audio_output(self):
        return self._cached('output', 10, self._audio_output)

    def _audio_output(self):
        entries = self.log()
        if not entries:
            return None
        last = None
        for m in OUTPUT_RE.finditer(oslib.tagged_text(entries, 'exxpiano')):
            last = m
        if not last:
            return None
        return dict(device=last.group(1), kind=last.group(2), period=int(last.group(3)), periods=int(last.group(4)))


def parse_oom(kernel):
    """The kernel's lines ([{t, text}], see oslib.kernel_messages) -> one error for every program the kernel killed for lack of memory."""
    out = []
    for e in kernel:
        m = OOM_RE.search(e['text'])
        if m:
            out.append(dict(t=e['t'], unit='kernel', level='error',
                            text='out of memory: the kernel killed %s, which held %d MB. Use a smaller piano bank (the Bank page) or a board with more memory' % (m.group(1), int(m.group(2)) // 1024)))
    return out


class WifiBusy(Exception):
    """The supervisor is in the middle of changing the network."""


class Busy(Exception):
    """A bank switch is already running."""


class Switcher:
    """Switches the piano bank in the background and puts the old one back if the synthesizer does not come up with the new one."""

    def __init__(self, app):
        self.app = app
        self.lock = threading.Lock()
        self.thread = None
        self.status = dict(state='idle', message='', bank=None)

    def busy(self):
        return self.status['state'] == 'switching'

    def start(self, path, bank_id, preset):
        with self.lock:
            if self.busy():
                raise Busy()
            self.status = dict(state='switching', message='Loading the bank, this takes about a minute', bank=bank_id)
        self.thread = threading.Thread(target=self._run, args=(path, bank_id, preset), daemon=True)
        self.thread.start()

    def join(self, timeout=None):
        if self.thread:
            self.thread.join(timeout)

    def _set(self, state, message):
        self.status = dict(state=state, message=message, bank=self.status.get('bank'))

    def _per_bank(self, settings):
        return {n: settings[n] for n in exxpianoconf.PER_BANK_NAMES}

    def _run(self, path, bank_id, preset):
        app, conf, actions = self.app, self.app.conf_path, self.app.actions
        raw = exxpianoconf.parse_conf(conf)
        old_path = raw.get('PIANO_SF2')
        old = self._per_bank(exxpianoconf.get_settings(conf))
        old_key = os.path.basename(old_path) if old_path else None
        # 1. what the old bank was last played with is remembered now (also covers edits made by hand in the file)
        app.remember(old_key, old)
        # 2. the new bank brings its own settings back; a bank never played before starts from the current ones
        mine = dict(old)
        saved = profiles.get(app.profile_path, bank_id)
        if saved:
            mine.update(saved)
        usable = {(p['bank'], p['program']) for p in banklib.presets_of(path) if p['usable']}
        if not saved or (mine['piano_bank'], mine['piano_prog']) not in usable:
            mine['piano_bank'], mine['piano_prog'] = preset
        ready = False
        try:
            exxpianoconf.apply_updates(mine, conf, raw={'PIANO_SF2': path})
            old_pid = actions.synth_pid()
            actions.restart_synth()
            ready = actions.wait_synth_ready(old_pid)
        except Exception as exc:                                # whatever went wrong, the old bank must come back
            print('bank switch failed: %s' % exc, file=sys.stderr)
        if ready:
            app.remember(bank_id, mine)
            try:
                exxpianoconf.apply_updates(None, conf, raw={'PIANO_SF2_LAST_GOOD': path})
            except (OSError, ValueError) as exc:
                print('could not store the last good bank: %s' % exc, file=sys.stderr)
            self._set('done', 'Now playing %s' % bank_id)
            return
        if not old_path:
            self._set('failed', 'The new bank did not load and there is no previous bank to go back to')
            return
        try:                                                    # the old bank AND its settings come back
            exxpianoconf.apply_updates(old, conf, raw={'PIANO_SF2': old_path})
            old_pid = actions.synth_pid()
            actions.restart_synth()
            back = actions.wait_synth_ready(old_pid)
        except Exception as exc:
            print('rollback failed: %s' % exc, file=sys.stderr)
            back = False
        self._set('rolled_back' if back else 'failed',
                  '%s did not load; %s' % (bank_id, 'the previous bank is playing again' if back
                                           else 'the previous bank could not be restored either, check the device'))


class ImportJob:
    """One import at a time, in a thread; the browser polls `snapshot()`. The states: idle, copying, verifying, done, failed."""

    def __init__(self):
        self._lock = threading.Lock()
        self._state = dict(state='idle', done=0, total=0, error=None, bank_id=None, name=None, op=0, already=False, kind='bank')

    def snapshot(self):
        with self._lock:
            return dict(self._state)

    def busy(self):
        with self._lock:
            return self._state['state'] in ('copying', 'verifying')

    def start(self, name, work, kind='bank'):
        """`work(progress)` returns (bank id, already there) or raises bankimport.ImportFailed. Busy when one is running."""
        with self._lock:
            if self._state['state'] in ('copying', 'verifying'):
                raise Busy()
            op = self._state['op'] + 1
            self._state = dict(state='copying', done=0, total=0, error=None, bank_id=None, name=name, op=op, already=False, kind=kind)

        def progress(done, total, phase):
            with self._lock:
                if self._state['op'] == op:
                    self._state.update(state=phase if phase in ('copying', 'verifying') else self._state['state'], done=max(self._state['done'], done), total=total)

        def run():
            try:
                bank_id, already = work(progress)
                result = dict(state='done', bank_id=bank_id, already=already, error=None)
            except bankimport.ImportFailed as exc:
                result = dict(state='failed', error=str(exc))
            except Exception as exc:                          # noqa: BLE001 - the user gets a safe sentence, the journal gets the cause
                print('bank import failed:', exc, file=sys.stderr)
                result = dict(state='failed', error='the import failed')
            with self._lock:
                if self._state['op'] == op:
                    self._state.update(result)
                    if result['state'] == 'done':
                        self._state['done'] = self._state['total']

        threading.Thread(target=run, daemon=True).start()
        return op


class App:
    def __init__(self, conf_path=None, actions=None):
        self.conf_path = conf_path or exxpianoconf.CONF_PATH
        self.actions = actions or Actions()
        conf = exxpianoconf.parse_conf(self.conf_path)
        self.token = conf.get('WEB_TOKEN', '')
        self.profile_path = conf.get('PROFILE_FILE') or profiles.DEFAULT_PATH
        self.devices_path = conf.get('DEVICE_PROFILE_FILE') or os.path.join(os.path.dirname(self.profile_path), 'devices.json')
        self.switcher = Switcher(self)
        self.import_dir = conf.get('IMPORT_DIR') or banklib.IMPORT_DIR
        self.usb_status_path = conf.get('USB_STATUS') or usblib.STATUS_FILE
        self.importer = ImportJob()
        self.midi_dir = conf.get('MIDI_DIR') or midilib.LIBRARY_DIR
        self.midi_run_dir = conf.get('MIDI_RUN_DIR') or midiplayer.RUN_DIR
        bankimport.stale_cleanup(self.import_dir)

    def spec(self):
        return {n: {k: v for k, v in s.items() if k in ('kind', 'lo', 'hi', 'default', 'scope')}
                for n, s in exxpianoconf.SETTINGS.items()}

    def state(self):
        a = self.actions
        return dict(
            banks=self.banks_state(),
            version=a.version(),
            hostname=a.hostname(), device=self.device_info(),
            fluidsynth=a.fluidsynth_version(),
            settings=exxpianoconf.get_settings(self.conf_path),
            device_profiles=profiles.load(self.devices_path, exxpianoconf.PER_DEVICE_NAMES),
            spec=self.spec(),
            status=dict(synth=a.service_state('exxpiano'), bridge=a.service_state('exxpiano-fx'),
                        output=a.audio_output(), cards=a.cards()),
            output_volume=self.output_volume(),
        )

    # ---- settings remembered per instrument
    def output_card(self):
        """The id of the sound card that plays now: the one the synthesizer logged, else the first card that can play."""
        out = self.actions.audio_output()
        m = CARD_RE.search(out['device']) if out else None
        if m:
            return m.group(1)
        cards = self.actions.cards()
        return cards[0]['id'] if cards else None

    def output_volume(self):
        card = self.output_card()
        vol = self.actions.output_volume(card) if card else None
        return dict(vol, card=card) if vol else None

    def set_output_volume(self, body):
        if not isinstance(body, dict):
            raise ValueError('expected an object')
        db = body.get('db')
        if isinstance(db, bool) or not isinstance(db, (int, float)) or db != db:
            raise ValueError('db is a number')
        vol = self.output_volume()
        if vol is None:
            raise ValueError('the sound card has no volume control')
        if not vol['lo'] <= db <= vol['hi']:
            raise ValueError('db must be between %s and %s' % (vol['lo'], vol['hi']))
        if not self.actions.set_output_volume(vol['card'], vol['control'], float(db)):
            raise OSError('the sound card volume could not be set')
        return dict(output_volume=self.output_volume())

    def current_key(self):
        cur = exxpianoconf.parse_conf(self.conf_path).get('PIANO_SF2')
        return os.path.basename(cur) if cur else None

    def remember(self, key, settings):
        """Store the per-instrument settings under the bank's file name (a failure is logged, never fatal)."""
        if not key:
            return
        try:
            profiles.save(self.profile_path, key, settings)
        except OSError as exc:
            print('could not remember the settings of %s: %s' % (key, exc), file=sys.stderr)

    def remember_device(self, device, settings):
        """Store the buffers of an audio interface under its ALSA_DEVICE value (a failure is logged, never fatal)."""
        try:
            profiles.save(self.devices_path, device, settings, exxpianoconf.PER_DEVICE_NAMES)
        except OSError as exc:
            print('could not remember the buffers of %s: %s' % (device, exc), file=sys.stderr)

    # ---- the piano banks
    # ---- Wi-Fi
    def wifi(self):
        a = self.actions
        st = a.wifi_status()
        saved = [dict(ssid=n['ssid'], secure=n['secure'], hidden=n['hidden']) for n in a.wifi_saved()]
        if st is None:
            return dict(supported=False, reason='networkmanager' if a.network_manager_active() else 'not_running', saved=saved, networks=[],
                        link=None, mode=None, busy=None)
        return dict(supported=True, mode=st.get('mode'), busy=st.get('busy'), link=st.get('link'), hostname=st.get('hostname'), ap=st.get('ap'),
                    saved=saved, networks=st.get('scan') or [], scan_error=st.get('scan_error'), scanned_at=st.get('scanned_at'), last_result=st.get('last_result'),
                    now=st.get('updated'), cable=bool(st.get('cable')))

    def wifi_action(self, body):
        if not isinstance(body, dict):
            raise ValueError('expected an object')
        action = body.get('action')
        state = self.wifi()
        if not state['supported']:
            raise ValueError('Wi-Fi setup is not available here (%s)' % state['reason'])
        if action == 'scan':
            if state['mode'] == 'ap' or state['busy']:
                return dict(networks=state['networks'], scanned_at=state['scanned_at'], cached=True)   # the radio is serving or busy
            found = self.actions.wifi_scan()
            if found is None:
                raise ValueError(getattr(self.actions, 'scan_error', None) or 'the scan failed, try again in a moment')
            return dict(networks=found, scanned_at=int(time.time()), cached=False)
        if state['busy']:
            raise WifiBusy()
        if action == 'setup':                                    # the setup network for a while, then back by itself (a test and a way out)
            if state['mode'] == 'ap':
                raise ValueError('the setup network is already running')
            return dict(accepted=True, id=self.actions.wifi_request(dict(action='ap', seconds=300)))
        if action == 'connect':
            ssid = body.get('ssid')
            wifilib.validate_ssid(ssid)
            password = wifilib.validate_passphrase(body.get('password'))
            if password is None and any(n['ssid'] == ssid and n['secure'] for n in state['networks']):
                raise ValueError('this network needs a password')
            psk = wifilib.derive_psk(ssid, password) if password else None
            rid = self.actions.wifi_request(dict(action='connect', ssid=ssid, psk=psk, hidden=bool(body.get('hidden'))))
            return dict(accepted=True, id=rid)
        if action == 'forget':
            ssid = body.get('ssid')
            if not isinstance(ssid, str) or not any(n['ssid'] == ssid for n in state['saved']):
                raise ValueError('that network is not saved')
            link = state.get('link')
            if link and link.get('ssid') == ssid and len(state['saved']) == 1 and not state.get('cable'):
                raise ValueError('this is the only way to reach the Pi: connect to another network or plug in a cable first')
            return dict(accepted=True, id=self.actions.wifi_request(dict(action='forget', ssid=ssid)))
        raise ValueError('unknown action')

    def qr(self, query, host=''):
        """A QR code as text and SVG. join: the setup network (phones offer to join it); setup: the page on that network; page: this page as the
        asking browser reached it (for a phone on the same network)."""
        kind = (query.get('kind') or [''])[0]
        conf = exxpianoconf.parse_conf(self.conf_path)
        install_id = self.device_info()['installation_id']           # the codes identify the installed copy they belong to
        port = conf.get('WEB_PORT') or '8080'
        if kind == 'join':
            ssid, password = wifilib.setup_credentials(conf, install_id)
            text, extra = qrlib.wifi_text(ssid, password), dict(ssid=ssid, password=password)
        elif kind == 'setup':
            text, extra = 'http://%s:%s/?d=%s#/wifi' % (wifilib.AP_ADDRESS, port, install_id), {}
        elif kind == 'page':
            if not re.fullmatch(r'[A-Za-z0-9._-]{1,100}(:\d{1,5})?', host or ''):
                host = '%s:%s' % ((self.wifi().get('link') or {}).get('ip') or wifilib.AP_ADDRESS, port)
            text, extra = 'http://%s/?d=%s#/wifi' % (host, install_id), {}
        else:
            raise ValueError('kind must be join, setup or page')
        matrix = qrlib.encode(text, 'M')
        # svg and png are what the page offers for download (the only place where a code can be taken: it belongs to this installation)
        return dict(extra, kind=kind, text=text, svg=qrlib.to_svg(matrix), png=base64.b64encode(qrlib.to_png(matrix, scale=10, border=4)).decode('ascii'),
                    filename='exxpiano-%s-%s' % ({'join': 'join-wifi', 'setup': 'setup-page', 'page': 'page'}[kind], install_id))

    def device_info(self):
        return dict(installation_id=self.actions.installation_id())

    def rename(self, body):
        name = body.get('name') if isinstance(body, dict) else None
        if not isinstance(name, str):
            raise ValueError('expected {"name": ...}')
        name = name.strip().lower()
        if not HOSTNAME_RE.match(name):
            raise ValueError('use lowercase letters, digits and hyphens (1 to 63 characters, no hyphen at the ends)')
        return dict(hostname=self.actions.set_hostname(name))

    def monitor(self, query):
        try:
            since = int((query.get('since') or ['0'])[0])
        except ValueError:
            raise ValueError('bad since')
        if since < 0:
            raise ValueError('bad since')
        return self.actions.monitor(since)

    def _bank_context(self):
        conf = exxpianoconf.parse_conf(self.conf_path)
        bank_dir = conf.get('BANK_DIR') or banklib.DEFAULT_DIR
        current, mech = conf.get('PIANO_SF2'), conf.get('MECH_SF2')
        try:
            mech_bytes = os.path.getsize(mech) if mech else 0
        except OSError:
            mech_bytes = 0
        return bank_dir, current, mech, mech_bytes

    def banks_state(self):
        bank_dir, current, mech, mech_bytes = self._bank_context()
        banks = banklib.list_banks(bank_dir, exclude=[mech], mem_total=self.actions.mem_total(), mech_bytes=mech_bytes)
        for b in banks:
            b['source'] = 'installed'
        banks += banklib.list_imports(self.import_dir, mem_total=self.actions.mem_total(), mech_bytes=mech_bytes)
        current_id = None
        for b in banks:
            if current and banklib._same(self._bank_path(bank_dir, b['id']), current):
                current_id = b['id']
        external = None
        if current and current_id is None:
            info, _ = banklib.inspect(current)
            external = dict(path=current, name=(info or {}).get('name') or os.path.basename(current))
        s = exxpianoconf.get_settings(self.conf_path)
        key = os.path.basename(current) if current else None
        return dict(dir=bank_dir, current=current_id, external=external, banks=banks,
                    memory=banklib.memory_info(bank_dir, self.actions.mem_total(), mech_bytes, current, mem_available=self.actions.mem_available()),
                    profile=dict(key=key, saved=bool(key and profiles.get(self.profile_path, key))),
                    presets=banklib.presets_of(current) if current else [],
                    preset=dict(bank=s['piano_bank'], program=s['piano_prog']), switch=self.switcher.status)

    def _bank_path(self, bank_dir, bank_id):
        try:
            return banklib.resolve_any(bank_dir, self.import_dir, bank_id)
        except (ValueError, OSError):
            return os.path.join(bank_dir, bank_id)

    # ---- USB drive: list and import, delete an imported bank
    def usb_state(self):
        drives = [usblib.drive_state(d) for d in usblib.read_status(self.usb_status_path)['drives']]
        return dict(drives=drives, importing=self.importer.snapshot())

    def usb_action(self, body):
        if not isinstance(body, dict):
            raise ValueError('expected an object')
        action = body.get('action')
        if action == 'import':
            return self._usb_import(body.get('name'), body.get('drive'))
        if action == 'delete':
            return self._usb_delete(body.get('id'))
        raise ValueError('unknown action')

    def _usb_import(self, name, drive_id):
        if self.switcher.busy():
            raise Busy()
        source = None
        for d in usblib.read_status(self.usb_status_path)['drives']:
            state = usblib.drive_state(d)
            if state['ready'] and (drive_id in (None, state['id'])):
                try:
                    source = usblib.resolve_candidate(d['mountpoint'], name)
                    break
                except ValueError:
                    continue
        if source is None:
            raise ValueError('that file is not on a connected drive')
        _, _, _, mech_bytes = self._bank_context()
        limit = banklib.size_limit(self.actions.mem_total(), mech_bytes)
        import_dir = self.import_dir
        self.importer.start(name, lambda progress: bankimport.import_bank(source, name, import_dir, mem_limit=limit, progress=progress))
        return dict(started=True)

    # ---- MIDI files: the drive's files, the library, the player of the bridge
    def midi_player(self):
        """The status the bridge's player published; 'unavailable' when it is old (the bridge is not running)."""
        try:
            with open(os.path.join(self.midi_run_dir, 'status.json'), encoding='utf-8') as f:
                st = json.load(f)
            if time.time() - float(st.get('updated', 0)) > 6:
                raise ValueError('old')
            return st
        except (OSError, ValueError, TypeError, AttributeError):
            return dict(state='unavailable')

    def midi_state(self):
        drives = []
        for d in usblib.read_status(self.usb_status_path)['drives']:
            s = usblib.drive_state(d)
            drives.append(dict(id=s['id'], label=s['label'], ready=s['ready'], reason=s['reason'], has_folder=s['has_midi_folder'], files=s['midi_files']))
        return dict(drives=drives, library=midilib.list_library(self.midi_dir), player=self.midi_player(), importing=self.importer.snapshot())

    def midi_action(self, body):
        if not isinstance(body, dict):
            raise ValueError('expected an object')
        action = body.get('action')
        if action == 'import':
            return self._midi_import(body.get('name'), body.get('drive'))
        if action == 'delete':
            return self._midi_delete(body.get('id'))
        if action == 'play':
            return self._midi_request(dict(op='play', id=self._midi_id(body.get('id')), loop=bool(body.get('loop'))))
        if action == 'seek':
            position = body.get('position')
            if isinstance(position, bool) or not isinstance(position, (int, float)) or not 0 <= position <= 24 * 3600:
                raise ValueError('position is a number of seconds')
            return self._midi_request(dict(op='seek', id=self._midi_id(body.get('id')), position=float(position)))
        if action == 'stop':
            return self._midi_request(dict(op='stop'))
        raise ValueError('unknown action')

    def _midi_id(self, midi_id):
        midilib.resolve(self.midi_dir, midi_id)                        # ValueError -> 400
        return midi_id

    def _midi_request(self, req):
        if req['op'] == 'play' and (self.importer.busy() or self.switcher.busy()):
            raise Busy()
        os.makedirs(self.midi_run_dir, exist_ok=True)
        path = os.path.join(self.midi_run_dir, 'request.json')
        tmp = path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(dict(req, nonce=time.time_ns()), f)
        os.replace(tmp, path)
        return dict(accepted=True)

    def _midi_import(self, name, drive_id):
        if self.switcher.busy():
            raise Busy()
        source = None
        for d in usblib.read_status(self.usb_status_path)['drives']:
            state = usblib.drive_state(d)
            if state['ready'] and (drive_id in (None, state['id'])):
                try:
                    source = usblib.resolve_candidate(d['mountpoint'], name, usblib.MIDI_FOLDER, usblib.MIDI_EXT)
                    break
                except ValueError:
                    continue
        if source is None:
            raise ValueError('that file is not on a connected drive')
        midi_dir = self.midi_dir
        self.importer.start(name, lambda progress: midilib.import_midi(source, name, midi_dir, progress=progress), kind='midi')
        return dict(started=True)

    def _midi_delete(self, midi_id):
        if self.importer.busy() or self.switcher.busy():
            raise Busy()
        player = self.midi_player()
        keep = [player.get('id')] if player.get('state') == 'playing' else []
        midilib.delete_file(self.midi_dir, midi_id, keep_ids=keep)
        return dict(deleted=True)

    def _usb_delete(self, bank_id):
        if self.importer.busy() or self.switcher.busy():
            raise Busy()
        conf = exxpianoconf.parse_conf(self.conf_path)
        keep = [conf.get('PIANO_SF2'), conf.get('PIANO_SF2_LAST_GOOD')]
        banklib.delete_import(self.import_dir, bank_id, keep_paths=keep)
        return dict(deleted=True)

    def delete_bank(self, bank_id):
        """Remove a bank of the library (installed or imported) that is not in use; the current bank, the last good one and the mechanics bank stay."""
        if self.importer.busy() or self.switcher.busy():
            raise Busy()
        bank_dir, current, mech, _ = self._bank_context()
        conf = exxpianoconf.parse_conf(self.conf_path)
        keep = [current, conf.get('PIANO_SF2_LAST_GOOD'), mech]
        if isinstance(bank_id, str) and banklib.IMPORT_ID_RE.match(bank_id):
            banklib.delete_import(self.import_dir, bank_id, keep_paths=keep)
            return dict(deleted=True)
        return dict(deleted=True, freed=banklib.delete_installed(bank_dir, bank_id, keep_paths=keep))

    def switch_bank(self, bank_id):
        bank_dir, current, mech, mech_bytes = self._bank_context()
        path = banklib.resolve_any(bank_dir, self.import_dir, bank_id)       # ValueError -> 400
        if current and banklib._same(path, current):
            return dict(started=False, message='This bank is already in use')
        info, error = banklib.inspect(path)
        if error:
            raise ValueError(error)
        limit = banklib.size_limit(self.actions.mem_total(), mech_bytes)
        if limit is not None and os.path.getsize(path) > limit:
            raise ValueError('too large for this device: %d MB, at most %d MB fit in memory' % (os.path.getsize(path) >> 20, limit >> 20))
        preset = banklib.default_preset(banklib.presets_of(path))
        if preset is None:
            raise ValueError('the bank has no preset that can be used for the piano')
        self.switcher.start(path, bank_id, preset)                        # Busy -> 409
        return dict(started=True)

    def _check_preset(self, body):
        """A new piano preset must exist in the current bank and must not be one the mechanics bank shadows."""
        keys = [k for k in ('piano_bank', 'piano_prog') if k in body]
        if not keys:
            return
        if self.switcher.busy():
            raise Busy()
        clean = exxpianoconf.validate({k: body[k] for k in keys})
        cur = exxpianoconf.get_settings(self.conf_path)
        pair = (clean.get('piano_bank', cur['piano_bank']), clean.get('piano_prog', cur['piano_prog']))
        _, current, _, _ = self._bank_context()
        usable = {(p['bank'], p['program']) for p in (banklib.presets_of(current) if current else []) if p['usable']}
        if pair not in usable:
            raise ValueError('no such preset in the current bank, or the mechanics bank shadows it')

    def _with_device_profile(self, body):
        """Choosing another audio interface: keep the buffers of the old one and, unless the request names buffers itself,
        bring back the ones this interface was used with."""
        if not isinstance(body, dict) or 'alsa_device' not in body:
            return body
        cur = exxpianoconf.get_settings(self.conf_path)
        try:
            new = exxpianoconf.validate({'alsa_device': body['alsa_device']})['alsa_device']
        except ValueError:
            return body                                    # apply_updates reports it
        if new == cur['alsa_device']:
            return body
        self.remember_device(cur['alsa_device'], {n: cur[n] for n in exxpianoconf.PER_DEVICE_NAMES})
        saved = profiles.get(self.devices_path, new, exxpianoconf.PER_DEVICE_NAMES) or {}
        merged = {k: v for k, v in saved.items() if k not in body}
        return dict(merged, **body)

    def update(self, body):
        per_bank = isinstance(body, dict) and any(k in exxpianoconf.PER_BANK_NAMES for k in body)
        if per_bank and self.switcher.busy():
            raise Busy()                                   # the switch is writing these very values
        if isinstance(body, dict):
            self._check_preset(body)
        body = self._with_device_profile(body)
        settings, scopes = exxpianoconf.apply_updates(body, self.conf_path)     # ValueError -> 400
        if per_bank:
            self.remember(self.current_key(), {n: settings[n] for n in exxpianoconf.PER_BANK_NAMES})
        if isinstance(body, dict) and ('alsa_device' in body or any(k in exxpianoconf.PER_DEVICE_NAMES for k in body)):
            self.remember_device(settings['alsa_device'], {n: settings[n] for n in exxpianoconf.PER_DEVICE_NAMES})
        done = dict(fx='fx' in scopes, gain=None)         # gain: True live, False failed, None unchanged
        if 'gain' in scopes:
            done['gain'] = bool(self.actions.set_gain(settings['gain']))
        return dict(settings=settings, applied=done, restart_required='synth' in scopes)


def make_handler(app):
    class Handler(BaseHTTPRequestHandler):
        server_version = 'exxpiano-web'

        def log_message(self, fmt, *args):
            sys.stderr.write('%s - %s\n' % (self.address_string(), fmt % args))

        def log_request(self, code='-', size='-'):
            # the page polls the state and the monitor all the time: successful polls would only fill the journal with noise
            if self.command == 'GET' and str(code) == '200' and urlsplit(self.path).path in ('/api/state', '/api/monitor', '/api/errors', '/api/levels', '/api/usb', '/api/midi', '/api/wifi', '/api/qr', '/api/ping'):
                return
            super().log_request(code, size)

        def _send(self, code, payload, ctype='application/json'):
            body = payload if isinstance(payload, bytes) else json.dumps(payload).encode('utf-8')
            self.send_response(code)
            self.send_header('Content-Type', ctype + '; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.end_headers()
            self.wfile.write(body)

        def _authorised(self):
            if not app.token:
                return True
            return hmac.compare_digest(self.headers.get('X-Token', ''), app.token)

        def _body(self):
            try:
                length = int(self.headers.get('Content-Length', '0'))
            except ValueError:
                length = -1
            if not 0 < length <= MAX_BODY:
                raise ValueError('bad body size')
            if 'application/json' not in self.headers.get('Content-Type', ''):
                raise ValueError('Content-Type must be application/json')
            return json.loads(self.rfile.read(length).decode('utf-8'))

        def do_GET(self):
            if urlsplit(self.path).path in ('/', '/index.html'):                 # a query (?d=81DE from a QR code) still means the page
                try:
                    with open(os.path.join(WEB_DIR, 'index.html'), 'rb') as f:
                        return self._send(200, f.read(), 'text/html')
                except OSError:
                    return self._send(500, dict(error='index.html is missing'))
            url = urlsplit(self.path)
            if url.path == '/api/state':
                if not self._authorised():
                    return self._send(401, dict(error='token required'))
                return self._send(200, dict(app.state(), auth_required=bool(app.token)))
            if url.path == '/api/qr':
                if not self._authorised():
                    return self._send(401, dict(error='token required'))
                try:
                    return self._send(200, app.qr(parse_qs(url.query), self.headers.get('Host', '')))
                except (ValueError, qrlib.QrError) as exc:
                    return self._send(400, dict(error=str(exc)))
            if url.path == '/api/midi':
                if not self._authorised():
                    return self._send(401, dict(error='token required'))
                return self._send(200, app.midi_state())
            if url.path == '/api/usb':
                if not self._authorised():
                    return self._send(401, dict(error='token required'))
                return self._send(200, app.usb_state())
            if url.path == '/api/wifi':
                if not self._authorised():
                    return self._send(401, dict(error='token required'))
                return self._send(200, app.wifi())
            if url.path == '/api/levels':
                if not self._authorised():
                    return self._send(401, dict(error='token required'))
                st = app.actions.levels()
                return self._send(200, dict(available=bool(st), **(st or {})))
            if url.path == '/api/errors':
                if not self._authorised():
                    return self._send(401, dict(error='token required'))
                found = app.actions.errors()
                return self._send(200, dict(errors=found or [], available=found is not None))
            if url.path == '/api/monitor':
                if not self._authorised():
                    return self._send(401, dict(error='token required'))
                try:
                    return self._send(200, app.monitor(parse_qs(url.query)))
                except ValueError as exc:
                    return self._send(400, dict(error=str(exc)))
            if url.path == '/api/ping':
                return self._send(200, dict(ok=True, auth_required=bool(app.token)))
            self._send(404, dict(error='not found'))

        def do_POST(self):
            if not self._authorised():
                return self._send(401, dict(error='token required'))
            try:
                if self.path == '/api/settings':
                    return self._send(200, app.update(self._body()))
                if self.path == '/api/output-volume':
                    return self._send(200, app.set_output_volume(self._body()))
                if self.path == '/api/restart':
                    app.actions.restart_synth()
                    return self._send(200, dict(ok=True))
                if self.path == '/api/midi':
                    return self._send(200, app.midi_action(self._body()))
                if self.path == '/api/usb':
                    return self._send(200, app.usb_action(self._body()))
                if self.path == '/api/wifi':
                    return self._send(200, app.wifi_action(self._body()))
                if self.path == '/api/device':
                    return self._send(200, app.rename(self._body()))
                if self.path == '/api/bank':
                    body = self._body()
                    if not isinstance(body, dict):
                        raise ValueError('expected an object')
                    if body.get('action') == 'delete':
                        return self._send(200, app.delete_bank(body.get('id')))
                    return self._send(200, app.switch_bank(body.get('id')))
            except Busy:
                return self._send(409, dict(error='a bank switch is in progress, wait until it finishes'))
            except WifiBusy:
                return self._send(409, dict(error='the Pi is changing the network right now, wait a moment'))
            except (ValueError, json.JSONDecodeError) as exc:
                return self._send(400, dict(error=str(exc)))
            except OSError as exc:
                return self._send(500, dict(error='cannot write the settings: %s' % exc))
            self._send(404, dict(error='not found'))

    return Handler


def main():
    conf = exxpianoconf.parse_conf()
    port = int(conf.get('WEB_PORT', '8080'))
    bind = conf.get('WEB_BIND', '0.0.0.0')
    app = App()
    server = ThreadingHTTPServer((bind, port), make_handler(app))
    print('exxpiano-web on http://%s:%d/ (token %s)' % (bind, port, 'required' if app.token else 'not set'), flush=True)
    server.serve_forever()


if __name__ == '__main__':
    main()
