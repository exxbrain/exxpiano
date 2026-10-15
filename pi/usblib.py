"""USB drives with SoundFont banks: mounting (read only, never executed) and the safe list of files worth importing.

A USB drive is untrusted. The mount is `ro,nosuid,nodev,noexec`; only plain files of the folder `piano-banks/` in the root of the drive are offered;
the browser never sends a path, only the name of a file this module has just listed. Nothing is copied before the user asks for it.
"""

import json
import os
import re
import stat
import tempfile

MOUNT_ROOT = '/run/exxpiano-usb'
STATUS_FILE = os.path.join(MOUNT_ROOT, 'status.json')
BANK_FOLDER = 'piano-banks'
MIDI_FOLDER = 'piano-midi'
MIDI_EXT = ('.mid', '.midi')
SUPPORTED_FS = ('vfat', 'exfat', 'ext4')
MOUNT_OPTIONS = 'ro,nosuid,nodev,noexec'
DEVICE_RE = re.compile(r'^/dev/(sd[a-z]{1,2}[0-9]{0,2}|mmcblk[0-9]p[0-9]+|nvme[0-9]n[0-9]p[0-9]+)$')
BANK_EXT = ('.sf2', '.sf3')
NAME_RE = re.compile(r'^[^/\\\x00-\x1f][^/\\\x00-\x1f]{0,119}$')
MAX_CANDIDATES = 50


class UsbError(Exception):
    pass


def safe_label(label):
    """A folder name made from the drive label: letters, digits, dash and underscore only."""
    cleaned = re.sub(r'[^A-Za-z0-9_-]+', '_', label or '').strip('_')[:32]
    return cleaned or 'usb'


def _write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix='.status-')
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, sort_keys=True)
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)


def read_status(path=STATUS_FILE):
    """{'drives': [...]} as the helper last published it; an empty list when nothing is plugged in or the helper never ran."""
    try:
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
        drives = [d for d in data.get('drives', []) if isinstance(d, dict)]
    except (OSError, ValueError, AttributeError):
        drives = []
    return dict(drives=drives)


def list_candidates(mountpoint, folder_name=BANK_FOLDER, exts=BANK_EXT):
    """The files of a folder of a mounted drive (by default the SoundFont files of `piano-banks/`): [{name, size}].
    Plain files only: no links, no folders, nothing hidden."""
    folder = os.path.join(mountpoint, folder_name)
    try:
        if os.path.islink(folder) or not os.path.isdir(folder):
            return []
        names = sorted(os.listdir(folder), key=str.lower)
    except OSError:
        return []
    out = []
    for name in names:
        if name.startswith('.') or not NAME_RE.match(name) or not name.lower().endswith(exts):
            continue
        if folder_name == BANK_FOLDER and name.lower() in ('mech.sf2', 'mech.sf3'):  # the mechanics bank is not replaced from a drive
            continue
        try:
            st = os.lstat(os.path.join(folder, name))
        except OSError:
            continue
        if stat.S_ISREG(st.st_mode) and st.st_size > 0:
            out.append(dict(name=name, size=st.st_size))
        if len(out) >= MAX_CANDIDATES:
            break
    return out


def resolve_candidate(mountpoint, name, folder_name=BANK_FOLDER, exts=BANK_EXT):
    """The path of a file the drive really lists (ValueError otherwise): a name from the browser is only ever compared with the listing."""
    if not isinstance(name, str) or not any(c['name'] == name for c in list_candidates(mountpoint, folder_name, exts)):
        raise ValueError('that file is not on the drive')
    return os.path.join(mountpoint, folder_name, name)


def drive_state(drive):
    """One drive of the status file made ready for the web interface: label, readiness, reason, candidates."""
    mount = drive.get('mountpoint')
    ready = bool(drive.get('mounted') and mount and os.path.isdir(mount))
    return dict(
        id=drive.get('id'),
        label=drive.get('label') or 'USB',
        fs=drive.get('fs'),
        ready=ready,
        reason=drive.get('reason') if not ready else None,
        has_folder=bool(ready and os.path.isdir(os.path.join(mount, BANK_FOLDER))),
        files=list_candidates(mount) if ready else [],
        midi_files=list_candidates(mount, MIDI_FOLDER, MIDI_EXT) if ready else [],
        has_midi_folder=bool(ready and os.path.isdir(os.path.join(mount, MIDI_FOLDER))),
    )


# ---- the privileged side: mount and unmount (used by exxpiano-usb.py, run by udev/systemd as root)
def probe(devnode, run):
    """-> (fs type, label) from blkid; UsbError when the device cannot be identified."""
    rc, out = run(['blkid', '-o', 'export', devnode])
    if rc != 0:
        raise UsbError('the drive has no recognised file system')
    fields = dict(line.split('=', 1) for line in out.splitlines() if '=' in line)
    return fields.get('TYPE', ''), fields.get('LABEL', '')


def add(devnode, run, status_path=STATUS_FILE, mount_root=MOUNT_ROOT):
    """Mount a drive read only and publish it. `run(cmd) -> (returncode, output)` is the only way to touch the machine."""
    if not DEVICE_RE.match(devnode or ''):
        raise UsbError('not a USB partition name')
    status = read_status(status_path)
    drives = [d for d in status['drives'] if d.get('id') != devnode]
    entry = dict(id=devnode, label='USB', fs=None, mounted=False, mountpoint=None, reason=None)
    try:
        fs, label = probe(devnode, run)
        entry.update(fs=fs, label=label or 'USB')
        if fs not in SUPPORTED_FS:
            raise UsbError('file system %s is not supported (FAT32, exFAT or ext4 are)' % (fs or 'unknown'))
        mountpoint = os.path.join(mount_root, safe_label(label) + '-' + os.path.basename(devnode))
        os.makedirs(mountpoint, exist_ok=True)
        rc, _out = run(['mount', '-t', fs, '-o', MOUNT_OPTIONS, devnode, mountpoint])
        if rc != 0:
            raise UsbError('the drive could not be mounted')
        entry.update(mounted=True, mountpoint=mountpoint)
    except UsbError as exc:
        entry['reason'] = str(exc)
    drives.append(entry)
    _write_json(status_path, dict(drives=drives))
    return entry


def remove(devnode, run, status_path=STATUS_FILE):
    """Forget a drive and unmount it (lazily: it may be gone already)."""
    status = read_status(status_path)
    keep, gone = [], []
    for d in status['drives']:
        (gone if d.get('id') == devnode else keep).append(d)
    for d in gone:
        if d.get('mounted') and d.get('mountpoint'):
            run(['umount', '-l', d['mountpoint']])
            try:
                os.rmdir(d['mountpoint'])
            except OSError:
                pass
    _write_json(status_path, dict(drives=keep))
    return bool(gone)
