#!/usr/bin/env python3
"""The register of the devices (SD card images) that were made: who has which number, which version and banks, and a note for each.

  python3 scripts/sd-registry.py list
  python3 scripts/sd-registry.py next                      # the next free number
  python3 scripts/sd-registry.py show 0007
  python3 scripts/sd-registry.py note 0007 "sold to Ivan, 12 Oct"
  python3 scripts/sd-registry.py find ivan                 # in any column, any case

The file is `registry/devices.csv` (override: --registry or EXXPIANO_REGISTRY). It is a plain CSV that a spreadsheet opens: back it up, it is the
only list of what was handed out (it is kept out of git because the notes may name customers). build-sd-image.py reads and writes it: it
refuses a number that is already used and takes the next free one for `--number auto`.
"""
import argparse
import csv
import datetime
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_PATH = os.environ.get('EXXPIANO_REGISTRY') or os.path.join(ROOT, 'registry', 'devices.csv')
COLUMNS = ['number', 'hostname', 'ssid', 'version', 'built_at', 'image', 'image_sha256', 'banks', 'note', 'serial', 'locked']
NUMBER_RE = re.compile(r'^[0-9A-Za-z]{1,8}$')


class RegistryError(Exception):
    pass


def normalize(number):
    """A number as typed ('7', '0007', 'ab12') in the form that is stored: letters upper case, bare digits padded to four."""
    number = str(number).strip()
    if not NUMBER_RE.match(number):
        raise RegistryError('a number is 1 to 8 letters or digits: %r' % number)
    return number.zfill(4) if number.isdigit() else number.upper()


def load(path=DEFAULT_PATH):
    try:
        with open(path, newline='', encoding='utf-8') as f:
            return [{c: row.get(c, '') for c in COLUMNS} for row in csv.DictReader(f)]
    except FileNotFoundError:
        return []


def save(rows, path=DEFAULT_PATH):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    os.replace(tmp, path)


def get(number, path=DEFAULT_PATH):
    number = normalize(number)
    for row in load(path):
        if row['number'] == number:
            return row
    return None


def find_serial(serial, path=DEFAULT_PATH):
    """The current registered card for a board (compared by the low 32 bits, the way the device does), or None."""
    tail = str(serial).strip().lower().zfill(16)[-8:]
    for row in load(path):
        if row['serial'] and row['serial'].lower().zfill(16)[-8:] == tail and not row['number'].endswith('~'):
            return row
    return None


def next_number(path=DEFAULT_PATH):
    """The number after the largest numeric one that exists (0001 for an empty register); numbers with letters are not counted."""
    used = [int(r['number']) for r in load(path) if r['number'].isdigit()]
    return str(max(used) + 1 if used else 1).zfill(4)


def add(record, path=DEFAULT_PATH, replace=False):
    """Write a new record. A number that exists is refused, unless replace=True (then the old line is kept as `<number>~` with a note)."""
    number = normalize(record['number'])
    rows = load(path)
    clash = [r for r in rows if r['number'] == number]
    if clash and not replace:
        raise RegistryError('the number %s is already registered (version %s, built %s). Choose another or use --force' %
                            (number, clash[0]['version'], clash[0]['built_at']))
    for r in clash:
        r['number'] = number + '~'
        r['note'] = (r['note'] + ' ' if r['note'] else '') + 'replaced by a newer image'
    row = {c: str(record.get(c, '')) for c in COLUMNS}
    row['number'] = number
    row['built_at'] = row['built_at'] or datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    rows.append(row)
    save(rows, path)
    return row


def set_note(number, text, path=DEFAULT_PATH):
    number = normalize(number)
    rows = load(path)
    for r in rows:
        if r['number'] == number:
            r['note'] = text
            save(rows, path)
            return r
    raise RegistryError('no such number: %s' % number)


def find(text, path=DEFAULT_PATH):
    text = text.lower()
    return [r for r in load(path) if any(text in v.lower() for v in r.values())]


def format_table(rows):
    if not rows:
        return '(nothing registered yet)'
    cols = ['number', 'hostname', 'serial', 'version', 'built_at', 'banks', 'note']
    width = {c: max(len(c), *(len(r[c]) for r in rows)) for c in cols}
    line = lambda r: '  '.join(r[c].ljust(width[c]) for c in cols).rstrip()
    return '\n'.join([line({c: c for c in cols})] + [line(r) for r in rows])


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--registry', default=DEFAULT_PATH)
    sub = ap.add_subparsers(dest='cmd', required=True)
    sub.add_parser('list')
    sub.add_parser('next')
    p = sub.add_parser('show')
    p.add_argument('number')
    p = sub.add_parser('note')
    p.add_argument('number')
    p.add_argument('text')
    p = sub.add_parser('find')
    p.add_argument('text')
    args = ap.parse_args(argv)
    try:
        if args.cmd == 'list':
            print(format_table(load(args.registry)))
        elif args.cmd == 'next':
            print(next_number(args.registry))
        elif args.cmd == 'show':
            row = get(args.number, args.registry)
            if not row:
                raise RegistryError('no such number: %s' % normalize(args.number))
            for c in COLUMNS:
                print('%-13s %s' % (c, row[c]))
        elif args.cmd == 'note':
            set_note(args.number, args.text, args.registry)
            print('noted')
        elif args.cmd == 'find':
            print(format_table(find(args.text, args.registry)))
    except RegistryError as exc:
        print('sd-registry:', exc, file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
