"""The bank library: which piano banks are available in a folder and which of them this machine can use.

A bank is identified by its plain file name inside the library folder. The web interface only ever hands us such a name, and `resolve` accepts
it only if the folder itself lists it, so a path can never be smuggled in (no `..`, no absolute paths, no symlinks).
"""
import os
import re
import stat

import sf2info

BANK_EXT = ('.sf2', '.sf3')
NAME_RE = re.compile(r'^[^/\\\x00-\x1f][^/\\\x00-\x1f]{0,119}$')      # a plain file name
DEFAULT_DIR = '/var/lib/exxpiano/banks'
RESERVE_MB = 400                  # RAM kept for the system and the rest of the module (the Pi 3 has about 950 MB)
MECH_PROGRAMS = range(1, 7)       # the mechanics bank answers on bank 0, programs 1..6 (see exxpiano-fx.py: PROGRAMS)

_cache = {}                       # (path, size, mtime_ns) -> sf2info result or error text


def mem_total_bytes(meminfo='/proc/meminfo'):
    try:
        with open(meminfo) as f:
            for line in f:
                if line.startswith('MemTotal:'):
                    return int(line.split()[1]) * 1024
    except (OSError, ValueError):
        pass
    return None


def size_limit(mem_total, mech_bytes=0, reserve_mb=RESERVE_MB):
    """The largest piano bank that fits next to the mechanics bank, or None when the memory size is unknown."""
    if mem_total is None:
        return None
    return max(0, mem_total - reserve_mb * (1 << 20) - mech_bytes)


def inspect(path):
    """(info, error) of a bank file; parsing results are cached until the file changes."""
    try:
        st = os.stat(path)
    except OSError as exc:
        return None, 'cannot read: %s' % exc
    key = (path, st.st_size, st.st_mtime_ns)
    if key not in _cache:
        try:
            _cache[key] = (sf2info.read_info(path), None)
        except sf2info.Sf2Error as exc:
            _cache[key] = (None, str(exc))
    return _cache[key]


def _same(a, b):
    try:
        return os.path.realpath(a) == os.path.realpath(b)
    except OSError:
        return False


def list_banks(bank_dir, exclude=(), mem_total=None, mech_bytes=0, reserve_mb=RESERVE_MB):
    """[dict(id, size, name, presets, ok, fits, reason)], sorted by file name. Unreadable folders give an empty list."""
    try:
        names = sorted(os.listdir(bank_dir), key=str.lower)
    except OSError:
        return []
    limit = size_limit(mem_total, mech_bytes, reserve_mb)
    out = []
    for name in names:
        if not name.lower().endswith(BANK_EXT) or not NAME_RE.match(name) or name.startswith('.'):
            continue
        path = os.path.join(bank_dir, name)
        try:
            st = os.lstat(path)
        except OSError:
            continue
        if not stat.S_ISREG(st.st_mode):                         # no symlinks, no folders
            continue
        if any(_same(path, e) for e in exclude if e):
            continue
        info, error = inspect(path)
        fits = limit is None or st.st_size <= limit
        reason = error
        if reason is None and not fits:
            reason = 'too large for this device: %d MB, at most %d MB fit in memory' % (st.st_size >> 20, limit >> 20)
        out.append(dict(id=name, size=st.st_size, name=(info or {}).get('name') or os.path.splitext(name)[0],
                        presets=len((info or {}).get('presets', [])), ok=error is None, fits=fits, reason=reason))
    return out


def resolve(bank_dir, bank_id):
    """The path of the bank called `bank_id`; ValueError unless the library folder really lists a plain file of that name."""
    if not isinstance(bank_id, str) or not NAME_RE.match(bank_id) or bank_id.startswith('.'):
        raise ValueError('bad bank name')
    if not bank_id.lower().endswith(BANK_EXT):
        raise ValueError('not a SoundFont file name')
    try:
        listed = bank_id in os.listdir(bank_dir)
    except OSError:
        listed = False
    path = os.path.join(bank_dir, bank_id)
    if not listed or not stat.S_ISREG(os.lstat(path).st_mode):
        raise ValueError('no such bank in the library')
    return path


def presets_of(path):
    """Presets for a drop-down: dict(bank, program, name, usable, reason). Bank 0 programs 1..6 belong to the mechanics bank."""
    info, error = inspect(path)
    if error:
        return []
    out = []
    for p in info['presets']:
        shadowed = p['bank'] == 0 and p['program'] in MECH_PROGRAMS and p['bank'] < 128
        out.append(dict(p, usable=not shadowed,
                        reason='shadowed by the mechanics bank (bank 0, programs 1-6)' if shadowed else None))
    return out


def default_preset(presets):
    """(bank, program) to start with: the first preset 0:0 if there is one, else the first usable preset."""
    usable = [p for p in presets if p['usable']]
    for p in usable:
        if p['bank'] == 0 and p['program'] == 0:
            return 0, 0
    if usable:
        return usable[0]['bank'], usable[0]['program']
    return None
