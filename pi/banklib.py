"""The bank library: which piano banks are available in a folder and which of them this machine can use.

A bank is identified by its plain file name inside the library folder. The web interface only ever hands us such a name, and `resolve` accepts
it only if the folder itself lists it, so a path can never be smuggled in (no `..`, no absolute paths, no symlinks).
"""

import json
import os
import re
import shutil
import stat

import sf2info

BANK_EXT = ('.sf2', '.sf3')
NAME_RE = re.compile(r'^[^/\\\x00-\x1f][^/\\\x00-\x1f]{0,119}$')  # a plain file name
DEFAULT_DIR = '/var/lib/exxpiano/banks'
IMPORT_DIR = '/var/lib/exxpiano/banks'  # banks imported from a USB drive: one folder <id>/ with bank.sf2 and meta.json each
IMPORT_ID_RE = re.compile(r'^i-[0-9a-f]{16}$')
RESERVE_MB = 200  # RAM kept for the system and the rest of the module on a board with 1 GB (the Pi 3 B has about 950 MB); the system itself took about 85 MB on a 512 MB board. Lowered from 400 for the 700 MB bank; not measured on a 1 GB board
SMALL_BOARD_MB = 800  # below this (a Pi 3 A+ has 512 MB, about 414 usable) the system needs less than 400 MB: it used about 85 MB there
SMALL_BOARD_RESERVE = 0.28  # share of the memory kept on such a board
MECH_PROGRAMS = range(1, 7)  # the mechanics bank answers on bank 0, programs 1..6 (see exxpiano-fx.py: PROGRAMS)

_cache = {}  # (path, size, mtime_ns) -> sf2info result or error text


def mem_total_bytes(meminfo='/proc/meminfo'):
    try:
        with open(meminfo) as f:
            for line in f:
                if line.startswith('MemTotal:'):
                    return int(line.split()[1]) * 1024
    except (OSError, ValueError):
        pass
    return None


def mem_available_bytes(meminfo='/proc/meminfo'):
    """MemAvailable of the kernel: what a program can still get without swapping."""
    try:
        with open(meminfo) as f:
            for line in f:
                if line.startswith('MemAvailable:'):
                    return int(line.split()[1]) * 1024
    except (OSError, ValueError):
        pass
    return None


def size_limit(mem_total, mech_bytes=0, reserve_mb=None):
    """The largest piano bank that fits next to the mechanics bank, or None when the memory size is unknown."""
    if mem_total is None:
        return None
    if reserve_mb is None:
        reserve = RESERVE_MB * (1 << 20) if mem_total >= SMALL_BOARD_MB * (1 << 20) else int(mem_total * SMALL_BOARD_RESERVE)
    else:
        reserve = reserve_mb * (1 << 20)
    return max(0, mem_total - reserve - mech_bytes)


def memory_info(bank_dir, mem_total, mech_bytes=0, current_path=None, reserve_mb=None, statvfs=os.statvfs, mem_available=None):
    """What the bank list shows about memory: dict(total, used, limit, mech, reserve, current, disk_free). Bytes; None where it is not known.
    `used` is the memory in use now (what the Monitor shows), `total` minus what is available.
    `limit` is the largest piano bank that fits, `reserve` what is kept for the system, `current` the size of the bank that plays."""
    limit = size_limit(mem_total, mech_bytes, reserve_mb)
    try:
        current = os.path.getsize(current_path) if current_path else None
    except OSError:
        current = None
    try:
        st = statvfs(bank_dir)
        disk_free = st.f_bavail * st.f_frsize
    except OSError:
        disk_free = None
    reserve = None if limit is None else mem_total - limit - mech_bytes
    used = mem_total - mem_available if mem_total is not None and mem_available is not None else None
    return dict(total=mem_total, used=used, limit=limit, mech=mech_bytes, reserve=reserve, current=current, disk_free=disk_free)


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


def list_banks(bank_dir, exclude=(), mem_total=None, mech_bytes=0, reserve_mb=None):
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
        if not stat.S_ISREG(st.st_mode):  # no symlinks, no folders
            continue
        if any(_same(path, e) for e in exclude if e):
            continue
        info, error = inspect(path)
        fits = limit is None or st.st_size <= limit
        reason = error
        if reason is None and not fits:
            reason = 'too large for this device: %d MB, at most %d MB fit in memory' % (st.st_size >> 20, limit >> 20)
        out.append(
            dict(
                id=name,
                size=st.st_size,
                name=(info or {}).get('name') or os.path.splitext(name)[0],
                presets=len((info or {}).get('presets', [])),
                ok=error is None,
                fits=fits,
                reason=reason,
            )
        )
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
        out.append(dict(p, usable=not shadowed, reason='shadowed by the mechanics bank (bank 0, programs 1-6)' if shadowed else None))
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


def fitting_replacement(piano, mech, bank_dir, mem_total=None):
    """The path of the largest bank that fits the memory, when `piano` does not fit; None when `piano` fits (or the memory is unknown) or nothing fits."""
    mem_total = mem_total_bytes() if mem_total is None else mem_total
    try:
        mech_bytes = os.path.getsize(mech) if mech else 0
        piano_bytes = os.path.getsize(piano)
    except OSError:
        return None
    limit = size_limit(mem_total, mech_bytes)
    if limit is None or piano_bytes <= limit:
        return None
    best = None
    for b in list_banks(bank_dir, exclude=[mech] if mech else (), mem_total=mem_total, mech_bytes=mech_bytes):
        if b['ok'] and b['fits'] and (best is None or b['size'] > best['size']):
            best = b
    return os.path.join(bank_dir, best['id']) if best else None


if __name__ == '__main__':  # used by exxpiano-run:  banklib.py PIANO MECH BANK_DIR  ->  prints the replacement path, or nothing
    import sys

    if len(sys.argv) == 4:
        meminfo = os.environ.get('EXXPIANO_MEMINFO')  # for tests
        found = fitting_replacement(sys.argv[1], sys.argv[2], sys.argv[3], mem_total_bytes(meminfo) if meminfo else None)
        if found:
            print(found)


# ---- imported banks (the managed library next to the installed ones)
def _import_entry(import_dir, bank_id):
    """(path, meta) of a complete imported bank, or (None, None) when anything is missing or does not agree."""
    if not isinstance(bank_id, str) or not IMPORT_ID_RE.match(bank_id):
        return None, None
    folder = os.path.join(import_dir, bank_id)
    try:
        if os.path.islink(folder) or not os.path.isdir(folder):
            return None, None
        with open(os.path.join(folder, 'meta.json'), encoding='utf-8') as f:
            meta = json.load(f)
        name = meta.get('file')
        if meta.get('id') != bank_id or name not in ('bank.sf2', 'bank.sf3'):
            return None, None
        path = os.path.join(folder, name)
        st = os.lstat(path)
        if not stat.S_ISREG(st.st_mode) or st.st_size != meta.get('size'):
            return None, None
        return path, meta
    except (OSError, ValueError, AttributeError):
        return None, None


def list_imports(import_dir, mem_total=None, mech_bytes=0, reserve_mb=None):
    """Imported banks in the same shape as list_banks, with source='imported' and the name the user saw on the drive."""
    try:
        names = sorted(os.listdir(import_dir))
    except OSError:
        return []
    limit = size_limit(mem_total, mech_bytes, reserve_mb)
    out = []
    for name in names:
        path, meta = _import_entry(import_dir, name)
        if not path:
            continue
        info, error = inspect(path)
        size = meta['size']
        fits = limit is None or size <= limit
        reason = error
        if reason is None and not fits:
            reason = 'too large for this device: %d MB, at most %d MB fit in memory' % (size >> 20, limit >> 20)
        out.append(
            dict(
                id=name,
                size=size,
                name=(info or {}).get('name') or meta.get('name') or name,
                original_name=meta.get('original_name'),
                presets=len((info or {}).get('presets', [])),
                ok=error is None,
                fits=fits,
                reason=reason,
                source='imported',
            )
        )
    return out


def resolve_any(bank_dir, import_dir, bank_id):
    """The path of an installed bank (a plain file name) or of an imported one (i-<16 hex>); ValueError otherwise."""
    if isinstance(bank_id, str) and IMPORT_ID_RE.match(bank_id):
        path, _ = _import_entry(import_dir, bank_id)
        if not path:
            raise ValueError('no such bank in the library')
        return path
    return resolve(bank_dir, bank_id)


def delete_installed(bank_dir, bank_id, keep_paths=()):
    """Remove an installed bank (a plain file of the bank library) unless it is one of `keep_paths`: the current bank, the last good one and
    the mechanics bank. ValueError with a reason otherwise. -> the number of bytes freed. The file is renamed out of the catalog first."""
    path = resolve(bank_dir, bank_id)  # ValueError unless it is a plain file of the library
    if any(_same(path, k) for k in keep_paths if k):
        raise ValueError('this bank is in use: choose another working bank first')
    size = os.lstat(path).st_size
    gone = os.path.join(bank_dir, '.deleting-' + bank_id)
    os.rename(path, gone)
    try:
        os.remove(gone)
    except OSError:
        pass  # a leftover with a dot name is not listed; the next delete or restart can clear it
    return size


def delete_import(import_dir, bank_id, keep_paths=()):
    """Remove an imported bank unless it is one of `keep_paths` (the current and the last good bank). ValueError with a reason otherwise."""
    path, _ = _import_entry(import_dir, bank_id)
    if not path:
        raise ValueError('no such imported bank')
    if any(_same(path, k) for k in keep_paths if k):
        raise ValueError('this bank is in use: choose another working bank first')
    folder = os.path.join(import_dir, bank_id)
    gone = os.path.join(import_dir, '.deleting-' + bank_id)
    os.rename(folder, gone)  # out of the catalog first, then the files
    shutil.rmtree(gone, ignore_errors=True)
