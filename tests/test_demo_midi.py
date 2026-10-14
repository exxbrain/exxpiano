"""The demonstration piece that comes with the package: imported once, not again after the owner deleted it, never a failure of the installation."""
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, '..')
sys.path.insert(0, os.path.join(ROOT, 'pi'))
sys.path.insert(0, HERE)
import midilib  # noqa: E402
from midifixture import make_midi  # noqa: E402

spec = importlib.util.spec_from_file_location('exxpiano_demo_midi', os.path.join(ROOT, 'pi', 'exxpiano-demo-midi.py'))
demo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(demo)

PIECE = 'liszt-un-sospiro-2011.mid'
TITLE = 'Liszt: Un Sospiro (Piano-e-Competition 2011)'


class DemoMidiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.share, self.lib = os.path.join(self.tmp.name, 'share'), os.path.join(self.tmp.name, 'lib', 'midi')
        os.makedirs(self.share)
        make_midi(os.path.join(self.share, PIECE), [(0, 60, 80, 1), (1, 64, 80, 1)])

    def names(self):
        return [e['name'] for e in midilib.list_library(self.lib)]

    def test_the_piece_is_imported_once_with_its_title(self):
        self.assertEqual(demo.import_demo(self.share, self.lib), [PIECE])
        self.assertEqual(self.names(), [TITLE])
        self.assertEqual(demo.import_demo(self.share, self.lib), [])                 # the next run: nothing to do
        self.assertEqual(len(midilib.list_library(self.lib)), 1)

    def test_a_piece_the_owner_deleted_does_not_come_back_with_an_update(self):
        demo.import_demo(self.share, self.lib)
        midilib.delete_file(self.lib, midilib.list_library(self.lib)[0]['id'])
        self.assertEqual(demo.import_demo(self.share, self.lib), [])
        self.assertEqual(self.names(), [])
        make_midi(os.path.join(self.share, 'new-piece.mid'), [(0, 62, 80, 1)])        # a piece of a later version is imported
        self.assertEqual(demo.import_demo(self.share, self.lib), ['new-piece.mid'])
        self.assertEqual(self.names(), ['new piece'])

    def test_nothing_in_the_way_is_a_failure(self):
        self.assertEqual(demo.import_demo(os.path.join(self.tmp.name, 'missing'), self.lib), [])         # no folder
        with open(os.path.join(self.share, 'broken.mid'), 'wb') as f:
            f.write(b'not midi' * 20)
        self.assertEqual(demo.import_demo(self.share, self.lib), [PIECE])                              # a bad file is skipped, the good one imported
        self.assertEqual(self.names(), [TITLE])
        with open(os.path.join(self.lib, demo.MARKER), encoding='utf-8') as f:
            self.assertEqual(json.load(f), [PIECE])

    def test_the_piece_in_the_repository_is_a_valid_midi_file_with_its_notice(self):
        path = os.path.join(ROOT, 'assets', 'midi', PIECE)
        info = midilib.inspect(path)
        self.assertGreater(info['notes'], 1000)
        self.assertLess(os.path.getsize(path), midilib.MAX_BYTES)
        with open(os.path.join(ROOT, 'assets', 'midi', 'NOTICE.md'), encoding='utf-8') as f:
            notice = f.read()
        for needle in ('CC BY-NC-SA 4.0', 'MAESTRO', 'unchanged', 'Google'):
            self.assertIn(needle, notice)
        real = os.path.join(self.tmp.name, 'real')
        os.makedirs(real)
        shutil.copy(path, real)
        self.assertEqual(demo.import_demo(real, self.lib), [PIECE])
        self.assertEqual(self.names(), [TITLE])


if __name__ == '__main__':
    unittest.main()
