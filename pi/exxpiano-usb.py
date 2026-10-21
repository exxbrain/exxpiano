#!/usr/bin/env python3
"""exxpiano-usb: mounts a USB partition read only (or forgets it), for udev.   exxpiano-usb.py add|remove /dev/sda1

Runs as root, once per event (90-exxpiano.rules starts it in a session of its own); it publishes /run/exxpiano-usb/status.json and does
nothing else: no copying (that needs the user's request). Started by udev it has no log of its own, so what it says also goes to the system log
(tag exxpiano-usb).
"""

import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import usblib  # noqa: E402


def run(cmd):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return r.returncode, r.stdout + r.stderr
    except (OSError, subprocess.SubprocessError) as exc:
        return 127, str(exc)


def log(text):
    print(text, file=sys.stderr)
    try:
        import syslog

        syslog.openlog('exxpiano-usb', 0, syslog.LOG_DAEMON)
        syslog.syslog(syslog.LOG_INFO, text)
    except (ImportError, OSError):
        pass


def main(argv):
    if len(argv) != 3 or argv[1] not in ('add', 'remove'):
        print(__doc__, file=sys.stderr)
        return 2
    try:
        if argv[1] == 'add':
            entry = usblib.add(argv[2], run)
            log('exxpiano-usb: %s %s' % (argv[2], 'mounted' if entry['mounted'] else 'not mounted: ' + str(entry['reason'])))
        else:
            usblib.remove(argv[2], run)
            log('exxpiano-usb: %s removed' % argv[2])
    except usblib.UsbError as exc:
        log('exxpiano-usb: %s' % exc)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
