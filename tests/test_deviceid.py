"""Tests for pi/deviceid.py: the current board serial and the identity recorded in an image."""
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'pi'))
import deviceid  # noqa: E402

CPUINFO = """processor\t: 0
model name\t: ARMv8 Processor rev 4 (v8l)
Hardware\t: BCM2835
Revision\t: a02082
Serial\t\t: 00000000d2f2f1de
Model\t\t: Raspberry Pi 3 Model B Rev 1.2
"""


class SerialTests(unittest.TestCase):
    def write(self, text, name='cpuinfo'):
        d = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__('shutil').rmtree(d, ignore_errors=True))
        path = os.path.join(d, name)
        with open(path, 'w') as f:
            f.write(text)
        return path

    def test_the_serial_is_read_from_cpuinfo(self):
        self.assertEqual(deviceid.read_serial(self.write(CPUINFO)), '00000000d2f2f1de')

    def test_no_serial_gives_none_and_the_devicetree_is_the_fallback(self):
        missing = '/nonexistent/x'
        self.assertIsNone(deviceid.read_serial(self.write('processor : 0\n'), missing))
        self.assertIsNone(deviceid.read_serial(missing, missing))
        dt = self.write('00000000d2f2f1de\0', 'serial-number')
        self.assertEqual(deviceid.read_serial(self.write('Hardware : x\n'), dt), '00000000d2f2f1de')
        self.assertIsNone(deviceid.read_serial(self.write('Serial : not-hex\n'), missing))

    def test_normalising(self):
        self.assertEqual(deviceid.normalize_serial('D2F2F1DE'), '00000000d2f2f1de')
        self.assertEqual(deviceid.normalize_serial(' 10000000D2F2F1DE '), '10000000d2f2f1de')
        for bad in ('', 'xyz', '1234567', '1' * 17, None, 'd2f2 f1de'):
            with self.subTest(bad), self.assertRaises(ValueError):
                deviceid.normalize_serial(bad)

    def test_a_board_is_known_by_its_low_32_bits(self):
        self.assertTrue(deviceid.same_board('00000000d2f2f1de', 'D2F2F1DE'))
        self.assertTrue(deviceid.same_board('10000000d2f2f1de', '00000000d2f2f1de'))
        self.assertFalse(deviceid.same_board('00000000d2f2f1de', '00000000d2f2f1df'))


class StatusTests(unittest.TestCase):
    ME, OTHER = '00000000d2f2f1de', '000000001234abcd'

    def test_status(self):
        self.assertEqual(deviceid.status(None, self.ME), dict(bound=False, serial=self.ME, bound_serial=None, ok=None))
        self.assertEqual(deviceid.status(self.ME, self.ME)['ok'], True)
        self.assertEqual(deviceid.status(self.OTHER, self.ME)['ok'], False)
        self.assertIsNone(deviceid.status(self.ME, None)['ok'])
        self.assertEqual(deviceid.status('garbage', self.ME)['bound'], False)                       # a bad value binds nothing

if __name__ == '__main__':
    unittest.main()
