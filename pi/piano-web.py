#!/usr/bin/env python3
"""piano-web: a small web interface for the piano module (standard library only).

  http://<pi>:8080/        the control page
  GET  /api/state          settings, their ranges, service status, sound cards
  POST /api/settings       {"velocity_curve": 0.8, ...}  - validated, stored in /etc/piano-synth.conf, applied
  POST /api/restart        restart piano-synth (needed after changing audio output / buffer / reverb / polyphony)
  POST /api/bank           {"id": "<file name in the bank library>"}  - switch the piano bank (rolls back if it does not come up)

Settings that can be changed are whitelisted with ranges in pianoconf.py. Optional WEB_TOKEN, WEB_PORT and WEB_BIND
come from /etc/piano-synth.conf. Without WEB_TOKEN anybody on your network can change the settings (nothing else).
"""
import hmac
import json
import os
import re
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import banklib
import pianoconf

WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'web')
MAX_BODY = 4096
OUTPUT_RE = re.compile(r'audio output (\S+) \((\w+)\), period (\d+) x (\d+)')


class Actions:
    """Everything that touches the system; tests replace this."""

    def __init__(self, asound=None):
        self.asound = asound or os.environ.get('ASOUND_DIR', '/proc/asound')

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
        try:
            r = subprocess.run(['fluidsynth', '--version'], capture_output=True, text=True, timeout=3)
        except (OSError, subprocess.SubprocessError):
            return None
        m = re.search(r'version (\S+)', r.stdout)
        return m.group(1) if m else None

    def mem_total(self):
        return banklib.mem_total_bytes()

    def synth_pid(self):
        try:
            r = subprocess.run(['systemctl', 'show', '-p', 'MainPID', '--value', 'piano-synth'], capture_output=True, text=True,
                               timeout=3)
            return int(r.stdout.strip() or 0)
        except (OSError, subprocess.SubprocessError, ValueError):
            return 0

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
            if pid and pid != old_pid and self.service_state('piano-synth') == 'active' and self._seq_has_synth():
                stable += 1
                if stable >= 2:
                    return True
            else:
                stable = 0
        return False

    def service_state(self, unit):
        try:
            r = subprocess.run(['systemctl', 'is-active', unit], capture_output=True, text=True, timeout=3)
            return r.stdout.strip() or 'unknown'
        except (OSError, subprocess.SubprocessError):
            return 'unknown'

    def restart_synth(self):
        subprocess.Popen(['systemctl', '--no-block', 'restart', 'piano-synth.service'])

    def set_gain(self, value):
        """Live master gain: write a shell command into the pipe FluidSynth reads (root-only, no network port).
        True on success. (`set synth.gain` is the command that really changes it; plain `gain` does not.)"""
        fifo = os.environ.get('CMD_FIFO', '/run/piano-synth.cmd')
        try:
            fd = os.open(fifo, os.O_WRONLY | os.O_NONBLOCK)      # fails with ENXIO when nobody reads (synth is down)
        except OSError:
            return False
        try:
            os.write(fd, ('set synth.gain %s\n' % pianoconf._fmt(float(value))).encode('ascii'))
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
        try:
            r = subprocess.run(['journalctl', '-u', 'piano-synth', '-n', '300', '--no-pager', '-o', 'cat'],
                               capture_output=True, text=True, timeout=5)
        except (OSError, subprocess.SubprocessError):
            return None
        last = None
        for m in OUTPUT_RE.finditer(r.stdout):
            last = m
        if not last:
            return None
        return dict(device=last.group(1), kind=last.group(2), period=int(last.group(3)), periods=int(last.group(4)))


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

    def _run(self, path, bank_id, preset):
        conf, actions = self.app.conf_path, self.app.actions
        raw = pianoconf.parse_conf(conf)
        old_path = raw.get('PIANO_SF2')
        old = pianoconf.get_settings(conf)
        ready = False
        try:
            pianoconf.apply_updates({'piano_bank': preset[0], 'piano_prog': preset[1]}, conf, raw={'PIANO_SF2': path})
            old_pid = actions.synth_pid()
            actions.restart_synth()
            ready = actions.wait_synth_ready(old_pid)
        except Exception as exc:                                # whatever went wrong, the old bank must come back
            print('bank switch failed: %s' % exc, file=sys.stderr)
        if ready:
            try:
                pianoconf.apply_updates(None, conf, raw={'PIANO_SF2_LAST_GOOD': path})
            except (OSError, ValueError) as exc:
                print('could not store the last good bank: %s' % exc, file=sys.stderr)
            self._set('done', 'Now playing %s' % bank_id)
            return
        if not old_path:
            self._set('failed', 'The new bank did not load and there is no previous bank to go back to')
            return
        try:
            pianoconf.apply_updates({'piano_bank': old['piano_bank'], 'piano_prog': old['piano_prog']}, conf,
                                    raw={'PIANO_SF2': old_path})
            old_pid = actions.synth_pid()
            actions.restart_synth()
            back = actions.wait_synth_ready(old_pid)
        except Exception as exc:
            print('rollback failed: %s' % exc, file=sys.stderr)
            back = False
        self._set('rolled_back' if back else 'failed',
                  '%s did not load; %s' % (bank_id, 'the previous bank is playing again' if back
                                           else 'the previous bank could not be restored either, check the device'))


class App:
    def __init__(self, conf_path=None, actions=None):
        self.conf_path = conf_path or pianoconf.CONF_PATH
        self.actions = actions or Actions()
        conf = pianoconf.parse_conf(self.conf_path)
        self.token = conf.get('WEB_TOKEN', '')
        self.switcher = Switcher(self)

    def spec(self):
        return {n: {k: v for k, v in s.items() if k in ('kind', 'lo', 'hi', 'default', 'scope')}
                for n, s in pianoconf.SETTINGS.items()}

    def state(self):
        a = self.actions
        return dict(
            banks=self.banks_state(),
            version=a.version(),
            fluidsynth=a.fluidsynth_version(),
            settings=pianoconf.get_settings(self.conf_path),
            spec=self.spec(),
            status=dict(synth=a.service_state('piano-synth'), bridge=a.service_state('piano-fx'),
                        output=a.audio_output(), cards=a.cards()),
        )

    # ---- the piano banks
    def _bank_context(self):
        conf = pianoconf.parse_conf(self.conf_path)
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
        current_id = None
        for b in banks:
            if current and banklib._same(os.path.join(bank_dir, b['id']), current):
                current_id = b['id']
        external = None
        if current and current_id is None:
            info, _ = banklib.inspect(current)
            external = dict(path=current, name=(info or {}).get('name') or os.path.basename(current))
        s = pianoconf.get_settings(self.conf_path)
        return dict(dir=bank_dir, current=current_id, external=external, banks=banks,
                    presets=banklib.presets_of(current) if current else [],
                    preset=dict(bank=s['piano_bank'], program=s['piano_prog']), switch=self.switcher.status)

    def switch_bank(self, bank_id):
        bank_dir, current, mech, mech_bytes = self._bank_context()
        path = banklib.resolve(bank_dir, bank_id)                          # ValueError -> 400
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
        clean = pianoconf.validate({k: body[k] for k in keys})
        cur = pianoconf.get_settings(self.conf_path)
        pair = (clean.get('piano_bank', cur['piano_bank']), clean.get('piano_prog', cur['piano_prog']))
        _, current, _, _ = self._bank_context()
        usable = {(p['bank'], p['program']) for p in (banklib.presets_of(current) if current else []) if p['usable']}
        if pair not in usable:
            raise ValueError('no such preset in the current bank, or the mechanics bank shadows it')

    def update(self, body):
        if isinstance(body, dict):
            self._check_preset(body)
        settings, scopes = pianoconf.apply_updates(body, self.conf_path)     # ValueError -> 400
        done = dict(fx='fx' in scopes, gain=None)         # gain: True live, False failed, None unchanged
        if 'gain' in scopes:
            done['gain'] = bool(self.actions.set_gain(settings['gain']))
        return dict(settings=settings, applied=done, restart_required='synth' in scopes)


def make_handler(app):
    class Handler(BaseHTTPRequestHandler):
        server_version = 'piano-web'

        def log_message(self, fmt, *args):
            sys.stderr.write('%s - %s\n' % (self.address_string(), fmt % args))

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
            if self.path in ('/', '/index.html'):
                try:
                    with open(os.path.join(WEB_DIR, 'index.html'), 'rb') as f:
                        return self._send(200, f.read(), 'text/html')
                except OSError:
                    return self._send(500, dict(error='index.html is missing'))
            if self.path == '/api/state':
                if not self._authorised():
                    return self._send(401, dict(error='token required'))
                return self._send(200, dict(app.state(), auth_required=bool(app.token)))
            if self.path == '/api/ping':
                return self._send(200, dict(ok=True, auth_required=bool(app.token)))
            self._send(404, dict(error='not found'))

        def do_POST(self):
            if not self._authorised():
                return self._send(401, dict(error='token required'))
            try:
                if self.path == '/api/settings':
                    return self._send(200, app.update(self._body()))
                if self.path == '/api/restart':
                    app.actions.restart_synth()
                    return self._send(200, dict(ok=True))
                if self.path == '/api/bank':
                    body = self._body()
                    if not isinstance(body, dict):
                        raise ValueError('expected an object')
                    return self._send(200, app.switch_bank(body.get('id')))
            except Busy:
                return self._send(409, dict(error='a bank switch is in progress, wait until it finishes'))
            except (ValueError, json.JSONDecodeError) as exc:
                return self._send(400, dict(error=str(exc)))
            except OSError as exc:
                return self._send(500, dict(error='cannot write the settings: %s' % exc))
            self._send(404, dict(error='not found'))

    return Handler


def main():
    conf = pianoconf.parse_conf()
    port = int(conf.get('WEB_PORT', '8080'))
    bind = conf.get('WEB_BIND', '0.0.0.0')
    app = App()
    server = ThreadingHTTPServer((bind, port), make_handler(app))
    print('piano-web on http://%s:%d/ (token %s)' % (bind, port, 'required' if app.token else 'not set'), flush=True)
    server.serve_forever()


if __name__ == '__main__':
    main()
