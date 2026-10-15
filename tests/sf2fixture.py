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
