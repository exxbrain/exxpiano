"""What the web interface and the Wi-Fi supervisor need from the system (standard library only): the state of an OpenRC service, a restart that
does not wait, the process of the synthesizer, and the messages of the piano services in the system log of this boot.

The system log is busybox syslogd writing /var/log/messages (rotated to messages.0):

  Oct 19 13:37:27 exxpiano-81de daemon.info exxpiano: exxpiano: audio output plughw:U24 (usb), period 64 x 3
  Oct 19 13:37:29 exxpiano-81de daemon.warn supervise-daemon[16]: /usr/bin/python3, pid 22, exited with return code 3
  Oct 19 13:37:30 exxpiano-81de kern.err kernel: Out of memory: Killed process 812 (fluidsynth) ... anon-rss:900000kB

The services write their output there through `logger -t <service>` (see pi/init.d). The lines carry no year, and the Raspberry Pi has no clock
of its own: the time can jump forward when the network sets it. So "this boot" is everything after the last "syslogd started" line, not a time.
"""

import os
import re
import subprocess
import time

LOG_FILES = ('/var/log/messages.0', '/var/log/messages')
LINE_RE = re.compile(r'^(\w{3}) +(\d{1,2}) (\d\d):(\d\d):(\d\d) (\S+) (\w+)\.(\w+) ([^:\[\s]+)(?:\[(\d+)\])?(?::|(?= )) ?(.*)$')
MONTHS = {m: i + 1 for i, m in enumerate(('Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'))}
LEVELS = {'emerg': 0, 'panic': 0, 'alert': 1, 'crit': 2, 'err': 3, 'error': 3, 'warn': 4, 'warning': 4, 'notice': 5, 'info': 6, 'debug': 7}
BOOT_MARK = 'started: BusyBox'  # "syslogd started: BusyBox v1.37.0": tag syslogd, then this text
CHILD_RE = re.compile(r'^Child command line: (.*)$')
EXIT_RE = re.compile(r'^(\S+), pid (\d+), (.+)$')
# the program each service runs (the command line supervise-daemon logs) -> the service
PROGRAMS = (('exxpiano-run', 'exxpiano'), ('exxpiano-fx.py', 'exxpiano-fx'), ('exxpiano-web.py', 'exxpiano-web'), ('exxpiano-wifi.py', 'exxpiano-wifi'))
STATES = {'started': 'active', 'stopped': 'inactive', 'crashed': 'failed', 'starting': 'activating', 'stopping': 'deactivating', 'inactive': 'inactive'}


def _run(cmd, timeout):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout + r.stderr
    except (OSError, subprocess.SubprocessError) as exc:
        return 127, str(exc)


# ---- services


def parse_status(rc, text):
    """`rc-service <name> status` -> active | inactive | failed | activating | deactivating | unknown (the words of systemctl is-active,
    which the page already knows)."""
    m = re.search(r'status: (\w+)', text or '')
    if m:
        return STATES.get(m.group(1), 'unknown')
    return 'unknown'


def service_state(name, run=_run):
    rc, out = run(['rc-service', name, 'status'], 3)
    return parse_status(rc, out)


def service_exists(name, run=_run):
    rc, _ = run(['rc-service', '-e', name], 3)
    return rc == 0


def restart(name, popen=None):
    """Restart a service without waiting for it (the synthesizer needs tens of seconds to load its banks)."""
    (popen or subprocess.Popen)(
        ['rc-service', name, 'restart'], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True
    )


def synth_pid(proc='/proc', comm='fluidsynth'):
    """The process ID of the running FluidSynth, 0 when there is none (the newest one if, briefly, there are two)."""
    found = 0
    try:
        names = os.listdir(proc)
    except OSError:
        return 0
    for name in names:
        if not name.isdigit():
            continue
        try:
            with open(os.path.join(proc, name, 'comm'), encoding='utf-8', errors='replace') as f:
                if f.read().strip() != comm:
                    continue
        except OSError:
            continue
        found = max(found, int(name))
    return found


# ---- the system log


def _timestamp(month, day, hh, mm, ss, now):
    """Milliseconds since the epoch for a syslog time without a year: this year, or the last one when that would lie in the future."""
    now_tm = time.localtime(now)
    for year in (now_tm.tm_year, now_tm.tm_year - 1):
        try:
            t = time.mktime((year, MONTHS[month], int(day), int(hh), int(mm), int(ss), 0, 0, -1))
        except (KeyError, OverflowError, ValueError):
            return None
        if t <= now + 86400:
            return int(t * 1000)
    return None


def parse_line(line, now):
    """One line of /var/log/messages -> dict(t, facility, level, tag, pid, text) or None."""
    m = LINE_RE.match(line.rstrip('\n'))
    if not m:
        return None
    t = _timestamp(m.group(1), m.group(2), m.group(3), m.group(4), m.group(5), now)
    if t is None:
        return None
    return dict(t=t, facility=m.group(7), level=LEVELS.get(m.group(8), 6), tag=m.group(9), pid=int(m.group(10)) if m.group(10) else None, text=m.group(11))


def read_messages(files=LOG_FILES, now=None):
    """The parsed lines of this boot, oldest first. None when no log can be read (the page then says the log is unavailable)."""
    now = time.time() if now is None else now
    lines, readable = [], False
    for path in files:
        try:
            with open(path, encoding='utf-8', errors='replace') as f:
                lines.extend(f.readlines())
            readable = True
        except OSError:
            continue
    if not readable:
        return None
    out = []
    for line in lines:
        e = parse_line(line, now)
        if e is None:
            continue
        if e['tag'] == 'syslogd' and e['text'].startswith(BOOT_MARK):
            out = []  # a new boot (or a restart of the log): only what follows counts
            continue
        out.append(e)
    return out


def _unit_of_command(command):
    for program, unit in PROGRAMS:
        if program in command:
            return unit
    return None


def service_messages(entries, units, notes=('exxpiano:', 'exxpiano-wifi:', 'exxpiano-usb:'), limit=40):
    """Log entries -> [{t, unit, level, text}] of the given services: warnings and errors, the deliberate notes of our own scripts (their
    text starts with "exxpiano:" and they are logged at info), and supervise-daemon's word that a service's process ended. A message repeated
    again and again is listed once with the last time; at most `limit`, newest last."""
    out, children = [], {}
    for e in entries:
        unit, text, prio = e['tag'], e['text'].strip(), e['level']
        if unit == 'supervise-daemon':
            m = CHILD_RE.match(text)
            if m:
                child_unit = _unit_of_command(m.group(1))
                if child_unit and e['pid']:
                    children[e['pid']] = child_unit
                continue
            m = EXIT_RE.match(text)
            if not m or int(m.group(2)) not in children:
                continue
            unit, text = children[int(m.group(2))], 'the process %s %s' % (m.group(2), m.group(3))
            prio = min(prio, 4)
        if unit not in units or not text:
            continue
        if prio > 4 and not text.startswith(notes):
            continue
        item = dict(t=e['t'], unit=unit, level='error' if prio <= 3 else 'warning' if prio <= 4 else 'info', text=text[:300])
        for i, old in enumerate(out):
            if old['unit'] == item['unit'] and old['text'] == item['text']:
                del out[i]
                break
        out.append(item)
    return out[-limit:]


def kernel_messages(entries):
    """The kernel's lines of this boot (klogd forwards them to the log), as dict(t, text)."""
    return [dict(t=e['t'], text=e['text']) for e in entries if e['tag'] == 'kernel']


def tagged_text(entries, tag):
    """All the text one service logged this boot, one line each (what `journalctl -u <unit> -o cat` printed)."""
    return '\n'.join(e['text'] for e in entries if e['tag'] == tag)
