"""Import a SoundFont into the managed library of imported banks (/var/lib/exxpiano/banks/<id>/bank.sf2 + meta.json).

The copy is a transaction: a temporary folder on the same filesystem, SHA-256 while copying, a check of the SoundFont, fsync, and only then one
atomic rename into the library. Anything that goes wrong (the drive pulled out, the disk full, a damaged file) removes the temporary folder, so a
half-copied bank never shows up in the library. The source path is always one the caller has found itself in a listing, never one from the browser.
"""
import hashlib
import json
import os
import re
import shutil
import tempfile
import time

import sf2info

CHUNK = 1 << 20
ID_PREFIX = 'i-'
ID_RE = re.compile(r'^i-[0-9a-f]{16}$')
MIN_FREE_MB = 64                                   # kept free on the card after the copy
TMP_PREFIX = '.tmp-import-'


class ImportFailed(Exception):
    """The reason is safe to show to the user: no internal paths in it."""


def bank_id(sha256_hex):
    return ID_PREFIX + sha256_hex[:16]


def stale_cleanup(import_dir, max_age=3600, now=None):
    """Remove temporary folders of an interrupted import (only our own, only old ones)."""
    now = time.time() if now is None else now
    try:
        names = os.listdir(import_dir)
    except OSError:
        return
    for name in names:
        path = os.path.join(import_dir, name)
        if name.startswith(TMP_PREFIX) and os.path.isdir(path) and not os.path.islink(path):
            try:
                if now - os.stat(path).st_mtime > max_age:
                    shutil.rmtree(path, ignore_errors=True)
            except OSError:
                pass


def remove_entry(import_dir, entry_id):
    """Take a published entry out of its library (out of the catalog first, then the files)."""
    folder = os.path.join(import_dir, entry_id)
    gone = os.path.join(import_dir, '.deleting-' + entry_id)
    os.rename(folder, gone)
    shutil.rmtree(gone, ignore_errors=True)


def import_bank(src_path, display_name, import_dir, mem_limit=None, progress=None, statvfs=os.statvfs, opener=open):
    """Copy a SoundFont into the library of imported banks. -> (id, already_there)."""
    def validate(path):
        try:
            info = sf2info.read_info(path)
        except sf2info.Sf2Error as exc:
            raise ImportFailed('not a usable SoundFont: %s' % exc) from exc
        return dict(name=info.get('name') or display_name)
    return import_file(src_path, display_name, import_dir, prefix=ID_PREFIX, data_name='bank' + os.path.splitext(src_path)[1].lower(),
                       validate=validate, mem_limit=mem_limit, progress=progress, statvfs=statvfs, opener=opener)


def import_file(src_path, display_name, import_dir, prefix, data_name, validate, mem_limit=None, max_bytes=None, progress=None,
                statvfs=os.statvfs, opener=open):
    """The transaction: copy `src_path` into `<import_dir>/<prefix><16 hex of SHA-256>/<data_name>` plus meta.json. -> (id, already_there).
    `validate(path)` checks the copy and returns more fields of meta.json (ImportFailed when the content is not usable).
    `progress(done_bytes, total_bytes, phase)` is called while it works."""
    progress = progress or (lambda done, total, phase: None)
    os.makedirs(import_dir, exist_ok=True)
    try:
        st = os.lstat(src_path)
    except OSError:
        raise ImportFailed('the file is no longer on the drive') from None
    import stat as _stat
    if not _stat.S_ISREG(st.st_mode):
        raise ImportFailed('not a plain file')
    total = st.st_size
    if total <= 0:
        raise ImportFailed('the file is empty')
    if max_bytes is not None and total > max_bytes:
        raise ImportFailed('the file is too big (at most %d MB)' % (max_bytes >> 20))
    if mem_limit is not None and total > mem_limit:
        raise ImportFailed('too large for this device: %d MB, at most %d MB fit in memory' % (total >> 20, mem_limit >> 20))
    try:
        free = statvfs(import_dir)
        available = free.f_bavail * free.f_frsize
    except OSError:
        available = None
    need = total + MIN_FREE_MB * (1 << 20)
    if available is not None and available < need:
        raise ImportFailed('not enough space: %d MB needed, %d MB available' % (need >> 20, available >> 20))
    tmp = tempfile.mkdtemp(prefix=TMP_PREFIX, dir=import_dir)
    try:
        sha = hashlib.sha256()
        done = 0
        target = os.path.join(tmp, data_name)
        progress(0, total, 'copying')
        try:
            with opener(src_path, 'rb') as src, open(target, 'wb') as dst:
                while True:
                    block = src.read(CHUNK)
                    if not block:
                        break
                    dst.write(block)
                    sha.update(block)
                    done += len(block)
                    progress(done, total, 'copying')
                dst.flush()
                os.fsync(dst.fileno())
        except OSError as exc:
            raise ImportFailed('the copy was interrupted (the drive was removed or the disk is full)') from exc
        if done != total:
            raise ImportFailed('the file changed while it was copied')
        progress(done, total, 'verifying')
        extra = validate(target)
        digest = sha.hexdigest()
        new_id = prefix + digest[:16]
        final = os.path.join(import_dir, new_id)
        if os.path.isdir(final):
            return new_id, True                                   # the same file is already there: no second copy
        meta = dict(extra, id=new_id, original_name=display_name, size=total, sha256=digest, file=os.path.basename(target),
                    imported_at=int(time.time()))
        with open(os.path.join(tmp, 'meta.json'), 'w', encoding='utf-8') as f:
            json.dump(meta, f, ensure_ascii=False, indent=2, sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
        fd = os.open(tmp, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        os.rename(tmp, final)
        tmp = None
        progress(total, total, 'done')
        return new_id, False
    finally:
        if tmp is not None:
            shutil.rmtree(tmp, ignore_errors=True)
