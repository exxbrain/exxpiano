"""Tiny SoundFont files for tests. They have the structure the reader looks at (INFO, sdta/smpl, pdta/phdr); they are not playable."""

import struct


def _chunk(tag, data):
    if len(data) % 2:
        data += b'\0'
    return tag + struct.pack('<I', len(data)) + data


def _lst(kind, data):
    return _chunk(b'LIST', kind + data)


def make_sf2(path, presets=(('Piano', 0, 0),), name='Test bank', sample_bytes=2000, version=(2, 1), extra_pdta=b''):
    """presets: (name, bank, program) tuples. Returns the path."""
    info = _chunk(b'ifil', struct.pack('<HH', *version)) + _chunk(b'isng', b'EMU8000\0') + _chunk(b'INAM', name.encode('latin-1') + b'\0')
    sdta = _lst(b'sdta', _chunk(b'smpl', b'\0' * sample_bytes))
    phdr = b''
    for i, (pname, bank, prog) in enumerate(presets):
        phdr += pname.encode('latin-1')[:19].ljust(20, b'\0') + struct.pack('<HHHIII', prog, bank, i, 0, 0, 0)
    phdr += b'EOP'.ljust(20, b'\0') + struct.pack('<HHHIII', 255, 255, len(presets), 0, 0, 0)
    pdta = _lst(b'pdta', _chunk(b'phdr', phdr) + extra_pdta)
    body = b'sfbk' + _lst(b'INFO', info) + sdta + pdta
    with open(path, 'wb') as f:
        f.write(b'RIFF' + struct.pack('<I', len(body)) + body)
    return path


def make_playable_sf2(path, presets=(('Piano', 0, 0),), seconds=0.5, rate=22050, extra_bytes=0):
    """A SoundFont FluidSynth really plays: one quiet sine sample (no loop: a note ends with the sample) under every preset.
    extra_bytes of silence make the file as big as a test needs."""
    import math

    n = int(seconds * rate)
    pcm = b''.join(struct.pack('<h', int(4000 * math.sin(2 * math.pi * 440 * i / rate))) for i in range(n)) + b'\0' * (92 + extra_bytes // 2 * 2)
    info = _lst(b'INFO', _chunk(b'ifil', struct.pack('<HH', 2, 1)) + _chunk(b'isng', b'EMU8000\0') + _chunk(b'INAM', b'Playable\0'))
    phdr, pbag, pgen = b'', b'', b''
    for i, (pname, bank, prog) in enumerate(presets):
        phdr += pname.encode('latin-1')[:19].ljust(20, b'\0') + struct.pack('<HHHIII', prog, bank, i, 0, 0, 0)
        pbag += struct.pack('<HH', i, 0)
        pgen += struct.pack('<HH', 41, 0)  # every preset plays instrument 0
    phdr += b'EOP'.ljust(20, b'\0') + struct.pack('<HHHIII', 0, 0, len(presets), 0, 0, 0)
    pbag += struct.pack('<HH', len(presets), 0)
    pgen += struct.pack('<HH', 0, 0)
    inst = b'Sine'.ljust(20, b'\0') + struct.pack('<H', 0) + b'EOI'.ljust(20, b'\0') + struct.pack('<H', 1)
    ibag = struct.pack('<HHHH', 0, 0, 1, 0)
    igen = struct.pack('<HHHH', 53, 0, 0, 0)  # sampleID 0
    shdr = b'sine'.ljust(20, b'\0') + struct.pack('<IIIIIBbHH', 0, n, 0, n - 1, rate, 69, 0, 0, 1)
    shdr += b'EOS'.ljust(20, b'\0') + struct.pack('<IIIIIBbHH', 0, 0, 0, 0, 0, 0, 0, 0, 0)
    pdta = _lst(
        b'pdta',
        _chunk(b'phdr', phdr)
        + _chunk(b'pbag', pbag)
        + _chunk(b'pmod', b'\0' * 10)
        + _chunk(b'pgen', pgen)
        + _chunk(b'inst', inst)
        + _chunk(b'ibag', ibag)
        + _chunk(b'imod', b'\0' * 10)
        + _chunk(b'igen', igen)
        + _chunk(b'shdr', shdr),
    )
    body = b'sfbk' + info + _lst(b'sdta', _chunk(b'smpl', pcm)) + pdta
    with open(path, 'wb') as f:
        f.write(b'RIFF' + struct.pack('<I', len(body)) + body)
    return path
