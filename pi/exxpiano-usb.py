#!/usr/bin/env python3
"""exxpiano-usb: mounts a USB partition read only (or forgets it), for udev/systemd.   exxpiano-usb.py add|remove /dev/sda1

Runs as root, once per event; it publishes /run/exxpiano-usb/status.json and does nothing else: no copying (that needs the user's request).
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


def main(argv):
    if len(argv) != 3 or argv[1] not in ('add', 'remove'):
        print(__doc__, file=sys.stderr)
        return 2
    try:
        if argv[1] == 'add':
            entry = usblib.add(argv[2], run)
            print('exxpiano-usb: %s %s' % (argv[2], 'mounted' if entry['mounted'] else 'not mounted: ' + str(entry['reason'])), file=sys.stderr)
        else:
            usblib.remove(argv[2], run)
            print('exxpiano-usb: %s removed' % argv[2], file=sys.stderr)
    except usblib.UsbError as exc:
        print('exxpiano-usb:', exc, file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
