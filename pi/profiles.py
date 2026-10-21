"""Per-instrument settings: the touch curve, volume, mechanics and preset a piano bank was last played with.

The running values stay in /etc/exxpiano.conf (the bridge and the synthesizer read only that). This file remembers them per bank,
keyed by the bank's file name, so choosing a bank again brings its settings back. The file is plain JSON, read defensively: whatever is
not a known setting, or is out of range, is dropped instead of being trusted.
"""

import json
import os
import tempfile

import exxpianoconf

DEFAULT_PATH = '/var/lib/exxpiano/profiles.json'
DEVICES_DEFAULT_PATH = '/var/lib/exxpiano/devices.json'
MAX_BYTES = 256 * 1024


def load(path, names=None):
    """{key: {setting: value}} with only valid settings from `names` (default: the per-instrument ones).
    A missing, huge or corrupt file gives {}."""
    names = exxpianoconf.PER_BANK_NAMES if names is None else names
    try:
        if os.path.getsize(path) > MAX_BYTES:
            return {}
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    out = {}
    for key, values in data.items():
        if not isinstance(key, str) or not isinstance(values, dict):
            continue
        clean = {}
        for name, raw in values.items():
            if name in names:
                try:
                    clean[name] = exxpianoconf._coerce(name, raw)
                except ValueError:
                    pass
        if clean:
            out[key] = clean
    return out


def get(path, key, names=None):
    return load(path, names).get(key)


def _sync_dir(directory):
    """After a rename: the new name must reach the card too, or a power cut brings the old file back."""
    try:
        fd = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def save(path, key, values, names=None):
    """Remember `values` (a dict of typed settings; only `names` are kept) under `key`: a bank's file name or an audio device."""
    names = exxpianoconf.PER_BANK_NAMES if names is None else names
    data = load(path, names)
    data[key] = {n: values[n] for n in names if n in values}
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix='.profiles.', dir=directory)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=1, sort_keys=True)
            f.write('\n')
            f.flush()
            os.fsync(f.fileno())  # durable: the settings of a bank survive a power cut right after they were changed
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
        _sync_dir(directory)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
