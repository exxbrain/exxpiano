"""System monitor for the web page: CPU, frequency, temperature, memory, throttling and the load of the piano services.

Standard library only. Everything is read from /proc and /sys (and `vcgencmd get_throttled` when it exists), so it works on a Pi and gives
partial answers elsewhere: a value that cannot be read is None, never an error. The roots are parameters so the tests can use fake trees.
"""

import glob
import os
import re
import subprocess
import threading
import time
from collections import deque

# Bits of `vcgencmd get_throttled`: the first four are the state right now, the next four say it happened since the boot.
THROTTLE_BITS = {0: 'undervoltage', 1: 'freq_capped', 2: 'throttled', 3: 'soft_limit'}
HISTORY = 150  # points kept for the charts (one per second while the page is open)
MIN_INTERVAL = 0.5  # a second client asking within this time gets the same sample
PID_CACHE = 10.0  # seconds before the process list is scanned again


def parse_throttled(raw):
    """Integer bit field -> (names active now, names that happened since the boot)."""
    if raw is None:
        return None
    now = [name for bit, name in THROTTLE_BITS.items() if raw >> bit & 1]
    past = [name for bit, name in THROTTLE_BITS.items() if raw >> (bit + 16) & 1]
    return dict(raw=raw, now=now, past=past)


def read_throttled(sysroot='/sys'):
    """The firmware's throttling bit field: vcgencmd first, the kernel's file as a fallback; None when neither exists."""
    try:
        r = subprocess.run(['vcgencmd', 'get_throttled'], capture_output=True, text=True, timeout=2)
        m = re.search(r'throttled=(0x[0-9a-fA-F]+)', r.stdout)
        if m:
            return int(m.group(1), 16)
    except (OSError, subprocess.SubprocessError):
        pass
    # the firmware node is soc/soc:firmware on a Pi 2/3/4 and soc@107c000000/soc@107c000000:firmware on a Pi 5
    for pattern in ('devices/platform/*/*:firmware/get_throttled', 'devices/platform/*/*:firmware/raspberrypi-hwmon/get_throttled'):
        for path in sorted(glob.glob(os.path.join(sysroot, pattern))):
            try:
                with open(path) as f:
                    return int(f.read().strip(), 16)
            except (OSError, ValueError):
                continue
    return None


def _read(path):
    try:
        with open(path, encoding='utf-8', errors='replace') as f:
            return f.read()
    except OSError:
        return None


def _pairs(text):
    """'key: value' / 'key : value' lines -> dict (later duplicates win)."""
    out = {}
    for line in (text or '').splitlines():
        m = re.match(r'^\s*([A-Za-z_][\w ]*?)\s*[:=]\s*(.*?)\s*$', line)
        if m:
            out[m.group(1)] = m.group(2)
    return out


def _int(text):
    m = re.match(r'\s*(\d+)', text or '')
    return int(m.group(1)) if m else None


def parse_usb_stream(text):
    """The playback half of /proc/asound/cardN/stream0 (what the USB interface offers and how it is clocked)."""
    if not text:
        return None
    play = text.split('Capture:')[0]
    if 'Playback:' not in play:
        return None
    play = play.split('Playback:', 1)[1]
    pairs = _pairs(play)
    sync = re.search(r'Endpoint:.*\((ASYNC|SYNC|ADAPTIVE|NONE)\)', play)
    rates = pairs.get('Rates', '')
    return dict(
        sync=sync.group(1) if sync else None,
        rates=[int(r) for r in re.findall(r'\d+', rates)][:12],
        bits=_int(pairs.get('Bits')),
        momentary=_int(pairs.get('Momentary freq')),
        status=pairs.get('Status'),
    )


class Monitor:
    def __init__(self, proc='/proc', sysroot='/sys', throttled=None, clock=time.monotonic, wall=time.time, sleep=time.sleep, hz=None, asound=None):
        self.proc, self.sys = proc, sysroot
        self.asound = asound or os.path.join(proc, 'asound')
        self.throttled = throttled or (lambda: read_throttled(sysroot))
        self.clock, self.wall, self.sleep = clock, wall, sleep
        try:
            self.hz = hz or os.sysconf('SC_CLK_TCK')
        except (ValueError, OSError, AttributeError):
            self.hz = hz or 100
        self.lock = threading.Lock()
        self.history = deque(maxlen=HISTORY)
        self.prev = None  # (time, {cpu name: (idle, total)}, {label: ticks})
        self.cached = None
        self.cached_at = 0.0
        self.pids = ({}, 0.0)  # ({label: [pid]}, when scanned)

    # ---- raw readers
    def _cpu_times(self):
        text = _read(os.path.join(self.proc, 'stat'))
        out = {}
        for line in (text or '').splitlines():
            m = re.match(r'^(cpu\d*)\s+(.*)$', line)
            if not m:
                continue
            try:
                v = [int(x) for x in m.group(2).split()[:8]]
            except ValueError:
                continue
            if len(v) >= 5:
                out[m.group(1)] = (v[3] + v[4], sum(v))  # idle + iowait, everything
        return out

    def _scan_pids(self):
        found = {'synth': [], 'bridge': [], 'web': []}
        try:
            names = os.listdir(self.proc)
        except OSError:
            return found
        for name in names:
            if not name.isdigit():
                continue
            comm = (_read(os.path.join(self.proc, name, 'comm')) or '').strip()
            if comm == 'fluidsynth':
                found['synth'].append(int(name))
                continue
            if comm.startswith('python'):
                cmd = _read(os.path.join(self.proc, name, 'cmdline')) or ''
                if 'exxpiano-fx' in cmd:
                    found['bridge'].append(int(name))
                elif 'exxpiano-web' in cmd:
                    found['web'].append(int(name))
        return found

    def _ticks(self, now):
        if now - self.pids[1] > PID_CACHE:
            self.pids = (self._scan_pids(), now)
        out = {}
        for label, pids in self.pids[0].items():
            total = 0
            for pid in pids:
                text = _read(os.path.join(self.proc, str(pid), 'stat'))
                if not text or ')' not in text:
                    self.pids = (self.pids[0], 0.0)  # it went away: look again next time
                    continue
                try:
                    fields = text.rsplit(')', 1)[1].split()
                    total += int(fields[11]) + int(fields[12])  # utime + stime
                except (IndexError, ValueError):
                    continue
            out[label] = total
        return out

    def audio(self):
        """The playback streams that are open right now with what the driver really negotiated (not what was asked for)."""
        out = []
        cards = _read(os.path.join(self.asound, 'cards')) or ''
        names = {}
        for m in re.finditer(r'^\s*(\d+) \[([^\]]*?)\s*\]:\s*(\S+)\s*-\s*(.*)$', cards, re.M):
            names[int(m.group(1))] = (m.group(2), m.group(4).strip())
        try:
            entries = sorted(os.listdir(self.asound))
        except OSError:
            return out
        for entry in entries:
            m = re.match(r'^card(\d+)$', entry)
            if not m:
                continue
            card = int(m.group(1))
            base = os.path.join(self.asound, entry)
            try:
                pcms = sorted(os.listdir(base))
            except OSError:
                continue
            for pcm in pcms:
                pm = re.match(r'^pcm(\d+)p$', pcm)
                if not pm:
                    continue
                for sub in sorted(os.listdir(os.path.join(base, pcm))) if os.path.isdir(os.path.join(base, pcm)) else []:
                    if not re.match(r'^sub\d+$', sub):
                        continue
                    d = os.path.join(base, pcm, sub)
                    hw = _read(os.path.join(d, 'hw_params'))
                    if not hw or hw.strip() == 'closed':
                        continue
                    h, st = _pairs(hw), _pairs(_read(os.path.join(d, 'status')))
                    rate, period, buf = _int(h.get('rate')), _int(h.get('period_size')), _int(h.get('buffer_size'))
                    usb = parse_usb_stream(_read(os.path.join(base, 'stream%d' % int(pm.group(1)))))
                    out.append(
                        dict(
                            card=card,
                            id=(names.get(card) or (None, None))[0] or (_read(os.path.join(base, 'id')) or '').strip() or None,
                            name=(names.get(card) or (None, None))[1],
                            device=int(pm.group(1)),
                            state=st.get('state'),
                            format=h.get('format'),
                            channels=_int(h.get('channels')),
                            rate=rate,
                            period=period,
                            buffer=buf,
                            period_ms=round(period * 1000.0 / rate, 2) if rate and period else None,
                            buffer_ms=round(buf * 1000.0 / rate, 2) if rate and buf else None,
                            delay=_int(st.get('delay')),
                            avail=_int(st.get('avail')),
                            avail_max=_int(st.get('avail_max')),
                            usb=usb,
                        )
                    )
        return out

    def _temperature(self):
        text = _read(os.path.join(self.sys, 'class/thermal/thermal_zone0/temp'))
        try:
            return round(int(text) / 1000.0, 1)
        except (TypeError, ValueError):
            return None

    def _frequency(self):
        def mhz(name):
            text = _read(os.path.join(self.sys, 'devices/system/cpu/cpu0/cpufreq', name))
            try:
                return round(int(text) / 1000)
            except (TypeError, ValueError):
                return None

        return mhz('scaling_cur_freq'), mhz('cpuinfo_max_freq')

    def _memory(self):
        values = {}
        for line in (_read(os.path.join(self.proc, 'meminfo')) or '').splitlines():
            m = re.match(r'^(MemTotal|MemAvailable):\s+(\d+) kB', line)
            if m:
                values[m.group(1)] = int(m.group(2)) // 1024
        if 'MemTotal' not in values or 'MemAvailable' not in values:
            return None
        return dict(total=values['MemTotal'], used=values['MemTotal'] - values['MemAvailable'])

    def _load(self):
        try:
            values = [float(x) for x in (_read(os.path.join(self.proc, 'loadavg')) or '').split()[:3]]
        except ValueError:
            return None
        return values if len(values) == 3 else None

    # ---- one sample
    def _sample(self):
        now = self.clock()
        cpu, ticks = self._cpu_times(), self._ticks(now)
        if self.prev is None:  # the first call needs two readings to have a rate
            self.prev = (now, cpu, ticks)
            self.sleep(0.25)
            now = self.clock()
            cpu, ticks = self._cpu_times(), self._ticks(now)
        t0, cpu0, ticks0 = self.prev
        dt = max(now - t0, 1e-3)

        def pct(name):
            if name not in cpu or name not in cpu0:
                return None
            dtotal = cpu[name][1] - cpu0[name][1]
            if dtotal <= 0:
                return 0.0
            return round(max(0.0, min(100.0, 100.0 * (1 - (cpu[name][0] - cpu0[name][0]) / dtotal))), 1)

        cores = []
        i = 0
        while 'cpu%d' % i in cpu:
            cores.append(pct('cpu%d' % i))
            i += 1
        # a service's share of ONE core (a multi-threaded one can pass 100)
        procs = {
            label: round(max(0, ticks[label] - ticks0.get(label, ticks[label])) / self.hz / dt * 100, 1) if self.pids[0].get(label) else None for label in ticks
        }
        freq, freq_max = self._frequency()
        self.prev = (now, cpu, ticks)
        snap = dict(
            t=int(self.wall() * 1000),
            cpu=pct('cpu'),
            cores=cores,
            procs=procs,
            temp=self._temperature(),
            freq=freq,
            freq_max=freq_max,
            mem=self._memory(),
            load=self._load(),
            throttle=parse_throttled(self.throttled()),
            audio=self.audio(),
        )
        self.history.append(dict(t=snap['t'], cpu=snap['cpu'], temp=snap['temp'], freq=snap['freq']))
        return snap

    def get(self, since=0):
        """{now: the latest sample, points: chart points newer than `since` (ms)}. A second caller within MIN_INTERVAL shares the sample."""
        with self.lock:
            if self.cached is None or self.clock() - self.cached_at >= MIN_INTERVAL:
                self.cached = self._sample()
                self.cached_at = self.clock()
            return dict(now=self.cached, points=[p for p in self.history if p['t'] > since])
