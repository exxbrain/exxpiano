#!/usr/bin/env python3
"""Puts the demonstration pieces that come with the package into the MIDI library, once.

  exxpiano-demo-midi.py        import every piece of /usr/share/exxpiano/midi that has not been imported yet

A piece that was imported is written into `.demo-imported` of the library: when the owner deletes it in the MIDI player, it does not come back
with the next update. A missing folder, an unwritable library or a bad file are not an error: the package installation must never fail on this.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bankimport  # noqa: E402
import midilib  # noqa: E402

DEMO_DIR = os.environ.get('EXXPIANO_DEMO_MIDI_DIR', '/usr/share/exxpiano/midi')
MARKER = '.demo-imported'
TITLES = {'liszt-un-sospiro-2011.mid': 'Liszt: Un Sospiro (Piano-e-Competition 2011)',
          'liszt-la-campanella-2013.mid': 'Liszt: La Campanella (Piano-e-Competition 2013)'}


def _done(library_dir):
    try:
        with open(os.path.join(library_dir, MARKER), encoding='utf-8') as f:
            return set(json.load(f))
    except (OSError, ValueError, TypeError):
        return set()


def import_demo(demo_dir=None, library_dir=None):
    """-> the names imported now."""
    demo_dir, library_dir = demo_dir or DEMO_DIR, library_dir or midilib.LIBRARY_DIR
    try:
        names = sorted(n for n in os.listdir(demo_dir) if n.lower().endswith(midilib.EXT) and not n.startswith('.'))
    except OSError:
        return []
    done, now = _done(library_dir), []
    for name in names:
        if name in done:
            continue
        try:
            midilib.import_midi(os.path.join(demo_dir, name), name, library_dir, title=TITLES.get(name))
        except (OSError, ValueError, bankimport.ImportFailed) as exc:
            print('exxpiano: demo piece %s was not imported: %s' % (name, exc), file=sys.stderr)
            continue
        done.add(name)
        now.append(name)
    if now:
        try:
            with open(os.path.join(library_dir, MARKER), 'w', encoding='utf-8') as f:
                json.dump(sorted(done), f)
        except OSError:
            pass
    return now


if __name__ == '__main__':
    imported = import_demo()
    print('imported: %s' % (', '.join(imported) or 'nothing'))
