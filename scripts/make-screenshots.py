#!/usr/bin/env python3
"""Takes the screenshots of docs/images (English and Russian) from the demo `scripts/demo-web.py` with a headless Chrome.

  python3 scripts/make-screenshots.py [--chrome PATH] [--out docs/images]

It starts the demo, plays the real piece of its library (the MIDI player and Home show it), and takes every section at the width of a phone
(390 px, twice the pixels). Needs Google Chrome or Chromium; nothing is written outside the output folder and a temporary one.
"""
import argparse
import base64
import json
import os
import socket
import struct
import subprocess
import sys
import tempfile
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHROMES = ('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', '/usr/bin/google-chrome', '/usr/bin/chromium', '/usr/bin/chromium-browser')
SHOTS = (('home', 'ui-home', 825), ('bank', 'ui-bank', 900), ('sound', 'ui-sound', 825), ('mon', 'ui-mon', 825), ('wifi', 'ui-wifi', 825), ('midi', 'ui-midi', 825), ('about', 'ui-about', 825))
PORT, DEBUG_PORT = 8097, 9337


def call(path, body=None):
    req = urllib.request.Request('http://127.0.0.1:%d%s' % (PORT, path), json.dumps(body).encode() if body is not None else None, {'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=10) as f:
        return json.load(f)


class Devtools:
    """A minimal WebSocket client of Chrome's DevTools protocol (standard library only)."""

    def __init__(self, port):
        for _ in range(100):
            try:
                with urllib.request.urlopen('http://127.0.0.1:%d/json' % port, timeout=2) as f:
                    pages = [t for t in json.load(f) if t.get('type') == 'page']
                if pages:
                    break
            except OSError:
                pass
            time.sleep(0.2)
        else:
            raise RuntimeError('Chrome did not start')
        path = pages[0]['webSocketDebuggerUrl'].split('%d' % port, 1)[1]
        self.sock = socket.create_connection(('127.0.0.1', port), timeout=60)
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall(('GET %s HTTP/1.1\r\nHost: 127.0.0.1:%d\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: %s\r\n'
                           'Sec-WebSocket-Version: 13\r\n\r\n' % (path, port, key)).encode())
        response = b''
        while b'\r\n\r\n' not in response:
            response += self.sock.recv(1)
        if b' 101 ' not in response.split(b'\r\n')[0]:
            raise RuntimeError('WebSocket refused: %r' % response[:80])
        self.n = 0

    def _read(self, size):
        data = b''
        while len(data) < size:
            chunk = self.sock.recv(size - len(data))
            if not chunk:
                raise RuntimeError('connection closed')
            data += chunk
        return data

    def _message(self):
        payload = b''
        while True:
            b1, b2 = self._read(2)
            length = b2 & 127
            if length == 126:
                length = struct.unpack('>H', self._read(2))[0]
            elif length == 127:
                length = struct.unpack('>Q', self._read(8))[0]
            payload += self._read(length)
            if b1 & 0x80:                                         # the last fragment
                return payload

    def send(self, method, **params):
        self.n += 1
        data = json.dumps(dict(id=self.n, method=method, params=params)).encode()
        mask = os.urandom(4)
        head = bytes([0x81]) + (bytes([0x80 | len(data)]) if len(data) < 126 else bytes([0x80 | 126]) + struct.pack('>H', len(data))
                                if len(data) < 65536 else bytes([0x80 | 127]) + struct.pack('>Q', len(data)))
        self.sock.sendall(head + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))
        while True:
            reply = json.loads(self._message())
            if reply.get('id') == self.n:
                if 'error' in reply:
                    raise RuntimeError('%s: %s' % (method, reply['error']))
                return reply.get('result', {})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--chrome', default=next((c for c in CHROMES if os.path.exists(c)), None))
    ap.add_argument('--out', default=os.path.join(ROOT, 'docs', 'images'))
    args = ap.parse_args()
    if not args.chrome:
        sys.exit('Chrome or Chromium was not found: give it with --chrome')
    demo = subprocess.Popen([sys.executable, os.path.join(ROOT, 'scripts', 'demo-web.py'), '--port', str(PORT), '--installation-id', '012ABC'],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    chrome = None
    try:
        for _ in range(50):
            try:
                call('/api/state')
                break
            except OSError:
                time.sleep(0.2)
        piece = call('/api/midi')['library'][0]
        with tempfile.TemporaryDirectory() as profile:
            chrome = subprocess.Popen([args.chrome, '--headless=new', '--disable-gpu', '--hide-scrollbars', '--no-first-run', '--user-data-dir=' + profile,
                                       '--remote-debugging-port=%d' % DEBUG_PORT, 'about:blank'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                dt = Devtools(DEBUG_PORT)
                for lang, suffix in (('en-US', ''), ('ru-RU', '-ru')):
                    for page, name, height in SHOTS:
                        call('/api/midi', dict(action='play', id=piece['id'], loop=False))
                        time.sleep(2.5)
                        call('/api/midi', dict(action='seek', id=piece['id'], position=107))      # 1:47 of 5:54, the same in every picture
                        time.sleep(1.2)
                        dt.send('Emulation.setDeviceMetricsOverride', width=390, height=height, deviceScaleFactor=2, mobile=True)
                        dt.send('Emulation.setUserAgentOverride', userAgent='Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1',
                                acceptLanguage=lang)
                        dt.send('Page.navigate', url='about:blank')
                        dt.send('Page.navigate', url='http://127.0.0.1:%d/#/%s' % (PORT, page))
                        time.sleep(5)                                  # the page asks for its data and draws it; the Monitor needs a few seconds for its charts
                        shot = dt.send('Page.captureScreenshot', format='png')
                        out = os.path.join(args.out, name + suffix + '.png')
                        with open(out, 'wb') as f:
                            f.write(base64.b64decode(shot['data']))
                        print('saved', out)
            finally:                                           # Chrome must be gone before its profile folder is removed
                chrome.terminate()
                chrome.wait(timeout=20)
    finally:
        demo.terminate()


if __name__ == '__main__':
    main()
