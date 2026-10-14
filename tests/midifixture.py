"""Standard MIDI files for the tests: make_midi() writes a small one from a list of notes."""
import struct


def vlq(n):
    out = [n & 0x7F]
    n >>= 7
    while n:
        out.append((n & 0x7F) | 0x80)
        n >>= 7
    return bytes(reversed(out))


def track(events):
    """events: [(tick, bytes of one event)] -> an MTrk chunk with delta times."""
    body, last = b'', 0
    for tick, raw in sorted(events, key=lambda e: e[0]):
        body += vlq(tick - last) + raw
        last = tick
    body += vlq(0) + b'\xff\x2f\x00'
    return b'MTrk' + struct.pack('>I', len(body)) + body


def midi_bytes(notes, tempo=500000, division=480, pedal=(), channel=0, title=None, fmt=0, tempos=(), extra=()):
    """notes: [(start in beats, note, velocity, length in beats)]; pedal: [(beat, value)]; tempos: [(beat, microseconds per beat)];
    extra: [(beat, raw event bytes)] for anything else (program changes, volume, drums ...)."""
    ev = [(0, b'\xff\x51\x03' + tempo.to_bytes(3, 'big'))]
    if title:
        ev.append((0, b'\xff\x03' + bytes([len(title)]) + title.encode('latin-1')))
    for beat, us in tempos:
        ev.append((int(beat * division), b'\xff\x51\x03' + us.to_bytes(3, 'big')))
    for start, note, vel, length in notes:
        ev.append((int(start * division), bytes([0x90 | channel, note, vel])))
        ev.append((int((start + length) * division), bytes([0x80 | channel, note, 0])))
    for beat, value in pedal:
        ev.append((int(beat * division), bytes([0xB0 | channel, 64, value])))
    for beat, raw in extra:
        ev.append((int(beat * division), raw))
    return b'MThd' + struct.pack('>IHHH', 6, fmt, 1, division) + track(ev)


def make_midi(path, notes=((0, 60, 80, 1),), **kw):
    with open(path, 'wb') as f:
        f.write(midi_bytes(list(notes), **kw))
    return path
