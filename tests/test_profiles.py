"""Tests for pi/profiles.py (settings remembered per instrument)."""
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'pi'))
import exxpianoconf  # noqa: E402
import profiles  # noqa: E402


class ProfilesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, 'sub', 'profiles.json')      # the folder does not exist yet

    def test_save_and_get_roundtrip_creates_the_folder(self):
        profiles.save(self.path, 'a.sf2', {'velocity_curve': 1.4, 'gain': 2.0, 'piano_bank': 0, 'piano_prog': 0})
        self.assertEqual(profiles.get(self.path, 'a.sf2'), {'velocity_curve': 1.4, 'gain': 2.0, 'piano_bank': 0, 'piano_prog': 0})
        self.assertIsNone(profiles.get(self.path, 'other.sf2'))

    def test_every_bank_has_its_own_values(self):
        profiles.save(self.path, 'a.sf2', {'velocity_curve': 1.4})
        profiles.save(self.path, 'b.sf2', {'velocity_curve': 0.7})
        self.assertEqual(profiles.get(self.path, 'a.sf2')['velocity_curve'], 1.4)
        self.assertEqual(profiles.get(self.path, 'b.sf2')['velocity_curve'], 0.7)

    def test_only_per_instrument_settings_are_kept(self):
        profiles.save(self.path, 'a.sf2', {'gain': 1.0, 'alsa_device': 'plughw:U24', 'polyphony': 64, 'usb_periods': 4})
        self.assertEqual(profiles.get(self.path, 'a.sf2'), {'gain': 1.0})

    def test_device_settings_are_not_per_instrument(self):
        for name in ('alsa_device', 'usb_period_size', 'usb_periods', 'period_size', 'periods', 'polyphony', 'reverb'):
            self.assertNotIn(name, exxpianoconf.PER_BANK_NAMES)
        for name in ('velocity_curve', 'gain', 'hammer_boost', 'piano_prog'):
            self.assertIn(name, exxpianoconf.PER_BANK_NAMES)

    def test_a_second_store_keeps_only_the_names_it_is_given(self):
        names = exxpianoconf.PER_DEVICE_NAMES
        profiles.save(self.path, 'plughw:CARD=U24', {'usb_period_size': 128, 'gain': 2.0, 'periods': 4}, names)
        self.assertEqual(profiles.get(self.path, 'plughw:CARD=U24', names), {'usb_period_size': 128, 'periods': 4})
        self.assertEqual(profiles.load(self.path), {})                  # read as bank settings nothing of it is valid
        profiles.save(self.path, 'plughw:CARD=U24', {'usb_period_size': 9999}, names)    # out of range: dropped on reading
        self.assertEqual(profiles.get(self.path, 'plughw:CARD=U24', names), None)

    def test_a_missing_file_gives_nothing(self):
        self.assertEqual(profiles.load(self.path), {})

    def test_junk_in_the_file_is_dropped_not_trusted(self):
        os.makedirs(os.path.dirname(self.path))
        with open(self.path, 'w') as f:
            json.dump({'a.sf2': {'gain': 99, 'velocity_curve': 1.2, 'evil': 1, 'hammer_exp': 'x'}, 'b.sf2': 'nope', 'c.sf2': {'gain': -1},
                       5: {}}, f)
        self.assertEqual(profiles.load(self.path), {'a.sf2': {'velocity_curve': 1.2}})

    def test_corrupt_oversized_and_wrong_shape_files_give_nothing(self):
        os.makedirs(os.path.dirname(self.path))
        for content in (b'{not json', b'[1, 2]', b'null', b'x' * (profiles.MAX_BYTES + 1)):
            with open(self.path, 'wb') as f:
                f.write(content)
            with self.subTest(content[:10]):
                self.assertEqual(profiles.load(self.path), {})

    def test_saving_over_a_corrupt_file_recovers(self):
        os.makedirs(os.path.dirname(self.path))
        with open(self.path, 'w') as f:
            f.write('{broken')
        profiles.save(self.path, 'a.sf2', {'gain': 1.5})
        self.assertEqual(profiles.get(self.path, 'a.sf2'), {'gain': 1.5})

    def test_atomic_write_leaves_no_temporary_files_and_a_readable_mode(self):
        profiles.save(self.path, 'a.sf2', {'gain': 1.5})
        self.assertEqual(os.listdir(os.path.dirname(self.path)), ['profiles.json'])
        self.assertEqual(oct(os.stat(self.path).st_mode & 0o777), oct(0o644))


if __name__ == '__main__':
    unittest.main()
