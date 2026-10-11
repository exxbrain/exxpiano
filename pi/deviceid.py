#!/usr/bin/env python3
"""The serial number of the Raspberry Pi board, and the binding of a card (an image) to one board.

  deviceid.py            print the serial of this board (16 hex digits, as /proc/cpuinfo shows it)
  deviceid.py check      exit 0 when the card may run here, 78 when it was made for another board and DEVICE_LOCK=1 (piano-synth-run uses it)

An image can be built for one board (build-sd-image.py --serial ...). The serial is then in /etc/piano-synth.conf as DEVICE_SERIAL:
  * always: the web page warns in red when the card is in another board, the register of the images knows which board each card was made for;
  * with DEVICE_LOCK=1 (build option --lock): the synthesizer does not start on another board.
This is bookkeeping and a lock against copying a card to a board it was not made for, not copy protection: whoever has root on the device can edit
the configuration. If the serial cannot be read (not a Raspberry Pi), the check lets the card run: it never locks a card it cannot judge.
"""
import os
import re
import sys

SERIAL_RE = re.compile(r'^[0-9a-fA-F]{8,16}$')
EX_CONFIG = 78


def normalize_serial(text):
    """16 lower-case hex digits. Accepts what /proc/cpuinfo shows (16 digits) or the last 8 digits."""
    text = (text or '').strip()
    if not SERIAL_RE.match(text):
        raise ValueError('a serial is 8 to 16 hex digits (cat /proc/cpuinfo shows it as "Serial")')
    return text.lower().zfill(16)


def read_serial(cpuinfo=None, devicetree='/sys/firmware/devicetree/base/serial-number'):
    """The serial of this board, or None."""
    cpuinfo = cpuinfo or os.environ.get('PIANO_CPUINFO', '/proc/cpuinfo')           # the variable is for tests
    try:
        with open(cpuinfo, encoding='utf-8', errors='replace') as f:
            for line in f:
                m = re.match(r'^Serial\s*:\s*([0-9a-fA-F]{8,16})\s*$', line)
                if m:
                    return normalize_serial(m.group(1))
    except OSError:
        pass
    try:
        with open(devicetree, 'rb') as f:
            text = f.read().replace(b'\0', b'').decode('ascii', 'replace')
        if SERIAL_RE.match(text):
            return normalize_serial(text)
    except OSError:
        pass
    return None


def same_board(a, b):
    """The low 32 bits identify a board; comparing them tolerates a serial given as the last 8 digits."""
    return normalize_serial(a)[-8:] == normalize_serial(b)[-8:]


def status(configured, actual):
    """{bound, serial, bound_serial, ok}: ok is None when there is nothing to compare."""
    try:
        bound = normalize_serial(configured) if configured else None
    except ValueError:
        bound = None
    ok = None
    if bound and actual:
        ok = same_board(bound, actual)
    return dict(bound=bool(bound), serial=actual, bound_serial=bound, ok=ok)


def check(env=None, actual='unread'):
    """(exit code, message). 0 = may run, 78 = locked to another board."""
    env = os.environ if env is None else env
    actual = read_serial() if actual == 'unread' else actual
    st = status(env.get('DEVICE_SERIAL'), actual)
    locked = env.get('DEVICE_LOCK') == '1'
    if not locked or not st['bound']:
        return 0, 'not locked'
    if st['ok'] is None:
        return 0, 'the serial of this board cannot be read: the card is allowed to run'
    if st['ok']:
        return 0, 'this card belongs to this board'
    return EX_CONFIG, 'this card was made for the Raspberry Pi with serial %s, this one is %s: the synthesizer does not start' % (st['bound_serial'], actual)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == 'check':
        code, message = check()
        print('deviceid:', message, file=sys.stderr if code else sys.stdout)
        return code
    serial = read_serial()
    print(serial or 'unknown')
    return 0 if serial else 1


if __name__ == '__main__':
    sys.exit(main())
