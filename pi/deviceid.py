#!/usr/bin/env python3
"""Reads the Raspberry Pi board serial for diagnostics.

  deviceid.py            print the serial of this board (16 hex digits, as /proc/cpuinfo shows it)

The reusable image does not ask for this serial and never uses it to control synthesizer startup.
"""
import os
import re
import sys

SERIAL_RE = re.compile(r'^[0-9a-fA-F]{8,16}$')


def normalize_serial(text):
    """16 lower-case hex digits. Accepts what /proc/cpuinfo shows (16 digits) or the last 8 digits."""
    text = (text or '').strip()
    if not SERIAL_RE.match(text):
        raise ValueError('a serial is 8 to 16 hex digits (cat /proc/cpuinfo shows it as "Serial")')
    return text.lower().zfill(16)


def read_serial(cpuinfo=None, devicetree='/sys/firmware/devicetree/base/serial-number'):
    """The serial of this board, or None."""
    cpuinfo = cpuinfo or os.environ.get('EXXPIANO_CPUINFO', '/proc/cpuinfo')           # the variable is for tests
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


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    serial = read_serial()
    print(serial or 'unknown')
    return 0 if serial else 1


if __name__ == '__main__':
    sys.exit(main())
