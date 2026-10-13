"""Tests for pi/sf2info.py (the SoundFont reader) and pi/banklib.py (the bank library)."""
import importlib.util
import os
import struct
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
PI = os.path.join(HERE, '..', 'pi')
sys.path.insert(0, PI)
sys.path.insert(0, HERE)
import banklib  # noqa: E402
import sf2info  # noqa: E402
from sf2fixture import make_sf2  # noqa: E402


class Sf2InfoTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = self.tmp.name

    def path(self, name='a.sf2'):
        return os.path.join(self.dir, name)

    def test_reads_name_version_size_and_presets(self):
        make_sf2(self.path(), presets=[('Grand', 0, 0), ('Bright', 0, 1), ('Drums', 128, 0)], name='My Piano', sample_bytes=4000)
        info = sf2info.read_info(self.path())
        self.assertEqual((info['name'], info['version']), ('My Piano', '2.1'))
        self.assertEqual(info['sample_bytes'], 4000)
        self.assertEqual(info['size'], os.path.getsize(self.path()))
        self.assertEqual([(p['bank'], p['program'], p['name']) for p in info['presets']],
                         [(0, 0, 'Grand'), (0, 1, 'Bright'), (128, 0, 'Drums')])

    def test_sf3_version_is_accepted(self):
        make_sf2(self.path('b.sf3'), version=(3, 0))
        self.assertEqual(sf2info.read_info(self.path('b.sf3'))['version'], '3.0')

    def test_junk_and_empty_and_missing_files(self):
        with open(self.path('junk.sf2'), 'wb') as f:
            f.write(os.urandom(200))
        open(self.path('empty.sf2'), 'wb').close()
        for name in ('junk.sf2', 'empty.sf2', 'missing.sf2'):
            with self.subTest(name), self.assertRaises(sf2info.Sf2Error):
                sf2info.read_info(self.path(name))
        with self.assertRaises(sf2info.Sf2Error):
            sf2info.read_info(self.dir)                              # a folder

    def test_truncated_file_is_rejected(self):
        make_sf2(self.path(), sample_bytes=5000)
        with open(self.path(), 'rb') as f:
            data = f.read()
        for cut in (len(data) // 2, len(data) - 1, 20):
            with open(self.path('cut.sf2'), 'wb') as f:
                f.write(data[:cut])
            with self.subTest(cut), self.assertRaises(sf2info.Sf2Error):
                sf2info.read_info(self.path('cut.sf2'))

    def test_chunk_sizes_that_lie_are_rejected(self):
        make_sf2(self.path(), sample_bytes=1000)
        with open(self.path(), 'rb') as f:
            data = bytearray(f.read())
        first_list = 12
        lying = bytearray(data)
        struct.pack_into('<I', lying, first_list + 4, 0x7FFFFFF0)      # the INFO list claims 2 GB
        with open(self.path('lie.sf2'), 'wb') as f:
            f.write(lying)
        with self.assertRaises(sf2info.Sf2Error):
            sf2info.read_info(self.path('lie.sf2'))
        huge = bytearray(data)
        struct.pack_into('<I', huge, 4, 0xFFFFFFF0)                    # the RIFF size claims 4 GB
        with open(self.path('huge.sf2'), 'wb') as f:
            f.write(huge)
        with self.assertRaises(sf2info.Sf2Error):
            sf2info.read_info(self.path('huge.sf2'))

    def test_a_bank_without_presets_or_headers_is_rejected(self):
        make_sf2(self.path('none.sf2'), presets=[])
        with self.assertRaises(sf2info.Sf2Error):
            sf2info.read_info(self.path('none.sf2'))

    def test_check_mode_exit_codes(self):
        make_sf2(self.path())
        with open(self.path('junk.sf2'), 'wb') as f:
            f.write(b'nope')
        self.assertEqual(sf2info.main(['--check', self.path()]), 0)
        self.assertEqual(sf2info.main(['--check', self.path('junk.sf2')]), 1)


class BankLibTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = self.tmp.name

    def make(self, filename, **kw):
        return make_sf2(os.path.join(self.dir, filename), **kw)

    def test_lists_banks_with_names_and_sorted(self):
        self.make('b-second.sf2', name='Second', presets=[('P', 0, 0), ('Q', 0, 9)])
        self.make('A-first.sf2', name='First')
        ids = [b['id'] for b in banklib.list_banks(self.dir)]
        self.assertEqual(ids, ['A-first.sf2', 'b-second.sf2'])
        banks = {b['id']: b for b in banklib.list_banks(self.dir)}
        self.assertEqual((banks['b-second.sf2']['name'], banks['b-second.sf2']['presets']), ('Second', 2))
        self.assertTrue(banks['A-first.sf2']['ok'] and banks['A-first.sf2']['fits'])

    def test_ignores_other_files_hidden_files_folders_and_symlinks(self):
        self.make('ok.sf2')
        open(os.path.join(self.dir, 'notes.txt'), 'w').close()
        self.make('.hidden.sf2')
        os.makedirs(os.path.join(self.dir, 'folder.sf2'))
        os.symlink(os.path.join(self.dir, 'ok.sf2'), os.path.join(self.dir, 'link.sf2'))
        self.assertEqual([b['id'] for b in banklib.list_banks(self.dir)], ['ok.sf2'])

    def test_a_broken_bank_is_listed_with_the_reason(self):
        with open(os.path.join(self.dir, 'bad.sf2'), 'wb') as f:
            f.write(b'not a bank at all')
        (bank,) = banklib.list_banks(self.dir)
        self.assertFalse(bank['ok'])
        self.assertIn('not a SoundFont', bank['reason'])

    def test_the_mechanics_bank_is_excluded_even_through_a_symlink(self):
        mech = self.make('mech.sf2')
        self.make('piano.sf2')
        link = os.path.join(self.tmp.name, 'elsewhere.sf2')
        os.symlink(mech, link)
        self.assertEqual([b['id'] for b in banklib.list_banks(self.dir, exclude=[link])], ['piano.sf2'])

    def test_size_limit_marks_banks_that_do_not_fit(self):
        self.make('big.sf2', sample_bytes=3 << 20)
        self.make('small.sf2', sample_bytes=1000)
        mem = (800 << 20) + (2 << 20)                       # a board with 1 GB: the reserve of 400 MB leaves 402 MB
        banks = {b['id']: b for b in banklib.list_banks(self.dir, mem_total=mem, reserve_mb=800)}
        self.assertTrue(banks['small.sf2']['fits'])
        self.assertFalse(banks['big.sf2']['fits'])
        self.assertIn('too large', banks['big.sf2']['reason'])
        self.assertIsNone(banklib.size_limit(None))
        self.assertEqual(banklib.size_limit(100 << 20, reserve_mb=400), 0)

    def test_a_board_with_512_mb_keeps_less_for_the_system(self):
        mem = 414 << 20                                     # what a Pi 3 A+ reports
        mech = 36 << 20
        limit = banklib.size_limit(mem, mech)
        self.assertGreater(limit, 248325370)                # the smaller bank fits ...
        self.assertLess(limit, 387396570)                   # ... the standard one does not
        self.assertEqual(banklib.size_limit(954 << 20, mech), (954 << 20) - (200 << 20) - mech)    # a 1 GB board keeps 200 MB
        self.assertGreater(banklib.size_limit(954 << 20, mech), 700 << 20)         # the 695 MB bank `full` fits

    def test_resolve_accepts_only_listed_plain_files(self):
        self.make('ok.sf2')
        self.assertEqual(banklib.resolve(self.dir, 'ok.sf2'), os.path.join(self.dir, 'ok.sf2'))
        for bad in ('../ok.sf2', '/etc/passwd', 'ok.sf2/../ok.sf2', 'missing.sf2', 'ok.txt', '', '.hidden.sf2', None, 5, 'a\x00.sf2',
                    'sub\\ok.sf2'):
            with self.subTest(bad), self.assertRaises(ValueError):
                banklib.resolve(self.dir, bad)
        os.symlink(os.path.join(self.dir, 'ok.sf2'), os.path.join(self.dir, 'link.sf2'))
        with self.assertRaises(ValueError):
            banklib.resolve(self.dir, 'link.sf2')

    def test_presets_in_bank_zero_programs_one_to_six_are_not_usable(self):
        path = self.make('gm.sf2', presets=[('Piano', 0, 0), ('Bright', 0, 1), ('Strings', 0, 48), ('Alt', 3, 2)])
        p = {x['name']: x for x in banklib.presets_of(path)}
        self.assertTrue(p['Piano']['usable'] and p['Strings']['usable'] and p['Alt']['usable'])
        self.assertFalse(p['Bright']['usable'])
        self.assertIn('mechanics', p['Bright']['reason'])

    def test_default_preset(self):
        both = banklib.presets_of(self.make('a.sf2', presets=[('X', 0, 5), ('Piano', 0, 0)]))
        self.assertEqual(banklib.default_preset(both), (0, 0))
        no_zero = banklib.presets_of(self.make('b.sf2', presets=[('Odd', 0, 3), ('Other', 2, 7)]))
        self.assertEqual(banklib.default_preset(no_zero), (2, 7))
        self.assertIsNone(banklib.default_preset(banklib.presets_of(self.make('c.sf2', presets=[('Only', 0, 4)]))))

    def test_the_cache_follows_changes_of_the_file(self):
        path = self.make('x.sf2', name='Old')
        self.assertEqual(banklib.list_banks(self.dir)[0]['name'], 'Old')
        make_sf2(path, name='New name', sample_bytes=3000)        # different size: the cache entry no longer matches
        self.assertEqual(banklib.list_banks(self.dir)[0]['name'], 'New name')

    def test_unreadable_folder_gives_an_empty_list(self):
        self.assertEqual(banklib.list_banks(os.path.join(self.dir, 'nope')), [])


if __name__ == '__main__':
    unittest.main()
