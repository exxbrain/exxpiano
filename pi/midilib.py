"""MIDI files for the piano: a bounded reader of standard MIDI files (standard library only) and the library of imported files.

A MIDI file from a USB drive is untrusted: the reader refuses what is too big, too long or too busy before it builds anything, understands
only what the piano plays (notes and the sustain pedal, no drums) and never turns anything from the file into a path or a command.

The library is /var/lib/exxpiano/midi/<id>/song.mid + meta.json, with id m-<16 hex of the SHA-256>, like the imported banks.
"""
import json
import os
import re
import stat
import struct

import bankimport

LIBRARY_DIR = '/var/lib/exxpiano/midi'
MAX_BYTES = 4 << 20                      # a piece of ten minutes is tens of kilobytes
MAX_SECONDS = 30 * 60
MAX_EVENTS = 200000                      # every event of the file, also the ones that are dropped: the memory of a Raspberry Pi 3 is the limit
MAX_TRACKS = 64
ID_RE = re.compile(r'^m-[0-9a-f]{16}$')
ID_PREFIX = 'm-'
EXT = ('.mid', '.midi')
DRUM_CHANNEL = 9                         # channel 10 for people
TITLE_MAX = 80


class MidiError(ValueError):
    """The reason is safe to show to the user."""


def _vlq(data, pos):
    value = 0
    for _ in range(4):
        if pos >= len(data):
            raise MidiError('the file ends in the middle of an event')
        byte = data[pos]
        pos += 1
        value = (value << 7) | (byte & 0x7F)
        if not byte & 0x80:
            return value, pos
    raise MidiError('a time value is too long')


GENERIC_TITLE_RE = re.compile(r'^(control track|conductor|tempo(?: track)?|track[ _-]?\d*|untitled|unnamed|midi)$', re.I)     # what sequencers write instead of a name


def _title(raw):
    text = raw.decode('latin-1', 'replace').replace('\x00', '').strip()
    return ''.join(c for c in text if c.isprintable())[:TITLE_MAX]


def parse(data):
    """-> dict(events=[(seconds, kind, a, b)], length, notes, title). kind: 'on' (note, velocity), 'off' (note, 0), 'pedal' (value, 0).
    Only the piano's part is kept: notes and CC 64 of every channel except the drum channel, all on one voice. MidiError for anything else."""
    if len(data) > MAX_BYTES:
        raise MidiError('the file is too big (at most %d MB)' % (MAX_BYTES >> 20))
    if len(data) < 14 or data[:4] != b'MThd':
        raise MidiError('not a standard MIDI file')
    header_len = struct.unpack('>I', data[4:8])[0]
    if header_len < 6 or 8 + header_len > len(data):
        raise MidiError('the MIDI header is damaged')
    fmt, ntracks, division = struct.unpack('>HHH', data[8:14])
    if fmt not in (0, 1):
        raise MidiError('only MIDI files of format 0 and 1 are supported')
    if division & 0x8000 or division == 0:
        raise MidiError('time in SMPTE frames is not supported')
    if ntracks > MAX_TRACKS:
        raise MidiError('too many tracks')
    pos = 8 + header_len
    note_events, tempos, title, seen = [], [], '', 0
    for _ in range(ntracks):
        if pos + 8 > len(data) or data[pos:pos + 4] != b'MTrk':
            break                                              # a short file: what was read so far is played
        size = struct.unpack('>I', data[pos + 4:pos + 8])[0]
        end = min(pos + 8 + size, len(data))
        p, tick, running = pos + 8, 0, None
        while p < end:
            delta, p = _vlq(data, p)
            tick += delta
            seen += 1
            if seen > MAX_EVENTS:
                raise MidiError('too many events in the file')
            if p >= end:
                break
            status = data[p]
            if status == 0xFF:                                # meta event
                if p + 1 >= end:
                    break
                mtype = data[p + 1]
                length, p = _vlq(data, p + 2)
                body = data[p:p + length]
                p += length
                if mtype == 0x51 and length == 3:
                    tempos.append((tick, int.from_bytes(body, 'big')))
                elif mtype == 0x03 and not title:
                    title = _title(body)
                elif mtype == 0x2F:
                    break
                continue
            if status in (0xF0, 0xF7):                         # system exclusive: skipped
                length, p = _vlq(data, p + 1)
                p += length
                continue
            if status & 0x80:
                running = status
                p += 1
            elif running is None:
                raise MidiError('the file starts with a data byte')
            status = running
            kind = status & 0xF0
            need = 1 if kind in (0xC0, 0xD0) else 2
            if p + need > end:
                break
            a = data[p]
            b = data[p + 1] if need == 2 else 0
            p += need
            if (status & 0x0F) == DRUM_CHANNEL:
                continue
            if kind == 0x90 and b > 0:
                note_events.append((tick, 2, 'on', a, b))
            elif kind == 0x80 or kind == 0x90:
                note_events.append((tick, 0, 'off', a, 0))
            elif kind == 0xB0 and a == 64:
                note_events.append((tick, 1, 'pedal', b, 0))
        pos += 8 + size
    notes = sum(1 for e in note_events if e[2] == 'on')
    if not notes:
        raise MidiError('the file has no piano notes')
    tempos = sorted(set(tempos)) or [(0, 500000)]
    if tempos[0][0] != 0:
        tempos.insert(0, (0, 500000))
    # ticks -> seconds through the tempo map
    marks, seconds, last_tick, last_us = [], 0.0, 0, tempos[0][1]
    for t, us in tempos:
        seconds += (t - last_tick) * last_us / division / 1e6
        marks.append((t, seconds, us))
        last_tick, last_us = t, us
    note_events.sort()
    events, mi = [], 0
    for tick, _, kind, a, b in note_events:
        while mi + 1 < len(marks) and marks[mi + 1][0] <= tick:
            mi += 1
        t0, s0, us = marks[mi]
        events.append((s0 + (tick - t0) * us / division / 1e6, kind, a, b))
    length = events[-1][0]
    if length > MAX_SECONDS:
        raise MidiError('the piece is too long (at most %d minutes)' % (MAX_SECONDS // 60))
    return dict(events=events, length=length, notes=notes, title=title)


def inspect(path):
    """parse() of a file; MidiError also when it cannot be read."""
    try:
        if os.path.getsize(path) > MAX_BYTES:
            raise MidiError('the file is too big (at most %d MB)' % (MAX_BYTES >> 20))
        with open(path, 'rb') as f:
            return parse(f.read())
    except OSError as exc:
        raise MidiError('the file cannot be read') from exc


# ---- the library of imported files
def _entry(library_dir, midi_id):
    """(path, meta) of a complete imported file, or (None, None)."""
    if not isinstance(midi_id, str) or not ID_RE.match(midi_id):
        return None, None
    folder = os.path.join(library_dir, midi_id)
    try:
        if os.path.islink(folder) or not os.path.isdir(folder):
            return None, None
        with open(os.path.join(folder, 'meta.json'), encoding='utf-8') as f:
            meta = json.load(f)
        path = os.path.join(folder, 'song.mid')
        st = os.lstat(path)
        if meta.get('id') != midi_id or not stat.S_ISREG(st.st_mode) or st.st_size != meta.get('size'):
            return None, None
        return path, meta
    except (OSError, ValueError, AttributeError):
        return None, None


def list_library(library_dir):
    """[dict(id, name, original_name, size, length, notes)] sorted by name."""
    try:
        names = sorted(os.listdir(library_dir))
    except OSError:
        return []
    out = []
    for name in names:
        path, meta = _entry(library_dir, name)
        if path:
            out.append(dict(id=name, name=meta.get('name') or meta.get('original_name') or name, original_name=meta.get('original_name'),
                            size=meta['size'], length=meta.get('length'), notes=meta.get('notes')))
    return sorted(out, key=lambda e: e['name'].lower())


def resolve(library_dir, midi_id):
    """The path of an imported file; ValueError when the library has no complete file of that id."""
    path, _ = _entry(library_dir, midi_id)
    if not path:
        raise ValueError('no such MIDI file in the library')
    return path


def delete_file(library_dir, midi_id, keep_ids=()):
    """Remove an imported file unless it is one of `keep_ids` (the one that is playing). ValueError with a reason."""
    path, _ = _entry(library_dir, midi_id)
    if not path:
        raise ValueError('no such MIDI file in the library')
    if midi_id in keep_ids:
        raise ValueError('this file is playing: stop it first')
    bankimport.remove_entry(library_dir, midi_id)


def import_midi(src_path, display_name, library_dir, progress=None, statvfs=os.statvfs, opener=open, title=None):
    """Copy a MIDI file into the library as one transaction. -> (id, already_there). `title` is the name to show instead of the file's own."""
    def validate(path):
        try:
            info = inspect(path)
        except MidiError as exc:
            raise bankimport.ImportFailed(str(exc)) from exc
        stem = re.sub(r'\s+', ' ', re.sub(r'[_-]+', ' ', os.path.splitext(display_name)[0])).strip()[:TITLE_MAX]
        own = info['title'] if info['title'] and not GENERIC_TITLE_RE.match(info['title'].strip()) else ''
        return dict(name=(title or '')[:TITLE_MAX] or own or stem, length=round(info['length'], 1), notes=info['notes'])
    return bankimport.import_file(src_path, display_name, library_dir, prefix=ID_PREFIX, data_name='song.mid', validate=validate,
                                  max_bytes=MAX_BYTES, progress=progress, statvfs=statvfs, opener=opener)
