"""Read the header and the preset list of a SoundFont (SF2 / SF3) without loading its samples.

A bank file can be hundreds of megabytes and may come from a stranger, so this reader never reads more than it needs and checks every size
against the real file size. Only the INFO chunk and the preset headers are read; the sample data is skipped with a seek.

    python3 sf2info.py FILE.sf2           print the name and the presets (JSON); exit status 1 if the file is not a usable bank
    python3 sf2info.py --check FILE.sf2   silent check for scripts: exit status 0 when usable
"""

import json
import os
import struct
import sys

MAX_INFO = 1 << 20  # the INFO chunk is a few hundred bytes in real banks
MAX_PDTA = 16 << 20  # preset/instrument/sample headers: 322 samples in ours, thousands in big banks
PHDR_SIZE = 38


class Sf2Error(ValueError):
    pass


def _text(raw):
    return raw.split(b'\0', 1)[0].decode('latin-1').strip()


def _sub_chunks(data):
    """Yield (tag, payload) of the sub-chunks inside a LIST payload."""
    pos = 0
    while pos + 8 <= len(data):
        tag, size = struct.unpack_from('<4sI', data, pos)
        if pos + 8 + size > len(data):
            raise Sf2Error('a sub-chunk %r runs past its list' % tag)
        yield tag, data[pos + 8 : pos + 8 + size]
        pos += 8 + size + (size & 1)


def read_info(path):
    """Return dict(name, comment, copyright, version, size, sample_bytes, presets=[dict(bank, program, name)])."""
    try:
        file_size = os.path.getsize(path)
        f = open(path, 'rb')  # noqa: SIM115 - closed by the with below; open errors are reported apart
    except OSError as exc:
        raise Sf2Error('cannot open: %s' % exc) from exc
    with f:
        head = f.read(12)
        if len(head) < 12 or head[:4] != b'RIFF' or head[8:12] != b'sfbk':
            raise Sf2Error('not a SoundFont (no RIFF/sfbk header)')
        declared = struct.unpack_from('<I', head, 4)[0] + 8
        if declared > file_size:
            raise Sf2Error('truncated: the header says %d bytes, the file has %d' % (declared, file_size))
        info, presets, sample_bytes = {}, None, 0
        pos = 12
        while pos + 12 <= min(declared, file_size):
            f.seek(pos)
            hdr = f.read(12)
            if len(hdr) < 12:
                break
            tag, size, kind = struct.unpack('<4sI4s', hdr)
            if tag != b'LIST':
                raise Sf2Error('unexpected top-level chunk %r' % tag)
            if size < 4 or pos + 8 + size > file_size:
                raise Sf2Error('a LIST chunk runs past the end of the file')
            body = size - 4
            if kind == b'INFO':
                if body > MAX_INFO:
                    raise Sf2Error('the INFO chunk is implausibly large')
                for sub_tag, payload in _sub_chunks(f.read(body)):
                    if sub_tag == b'ifil' and len(payload) >= 4:
                        info['version'] = '%d.%d' % struct.unpack('<HH', payload[:4])
                    elif sub_tag in (b'INAM', b'ICMT', b'ICOP'):
                        info[sub_tag.decode()] = _text(payload)
            elif kind == b'sdta':
                sub = f.read(8)
                if len(sub) == 8 and sub[:4] == b'smpl':
                    sample_bytes = struct.unpack('<I', sub[4:])[0]
            elif kind == b'pdta':
                if body > MAX_PDTA:
                    raise Sf2Error('the preset headers are implausibly large (%d bytes)' % body)
                presets = []
                for sub_tag, payload in _sub_chunks(f.read(body)):
                    if sub_tag == b'phdr':
                        if len(payload) % PHDR_SIZE:
                            raise Sf2Error('the preset header list has a bad size')
                        for i in range(0, len(payload) - PHDR_SIZE, PHDR_SIZE):  # the last entry is the terminal record
                            name, preset, bank = struct.unpack_from('<20sHH', payload, i)
                            presets.append(dict(bank=bank, program=preset, name=_text(name)))
            pos += 8 + size + (size & 1)
        if presets is None:
            raise Sf2Error('no preset headers (pdta) found')
        if not presets:
            raise Sf2Error('the bank has no presets')
        return dict(
            name=info.get('INAM', ''),
            comment=info.get('ICMT', ''),
            copyright=info.get('ICOP', ''),
            version=info.get('version', ''),
            size=file_size,
            sample_bytes=sample_bytes,
            presets=presets,
        )


def main(argv):
    check = '--check' in argv
    files = [a for a in argv if not a.startswith('--')]
    if len(files) != 1:
        print(__doc__, file=sys.stderr)
        return 2
    try:
        data = read_info(files[0])
    except Sf2Error as exc:
        if not check:
            print(json.dumps(dict(error=str(exc))))
        return 1
    if not check:
        print(json.dumps(data, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
