"""Tests for pi/deviceid.py: the board serial and the binding of a card to a board."""
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


class StatusAndCheckTests(unittest.TestCase):
    ME, OTHER = '00000000d2f2f1de', '000000001234abcd'

    def test_status(self):
        self.assertEqual(deviceid.status(None, self.ME), dict(bound=False, serial=self.ME, bound_serial=None, ok=None))
        self.assertEqual(deviceid.status(self.ME, self.ME)['ok'], True)
        self.assertEqual(deviceid.status(self.OTHER, self.ME)['ok'], False)
        self.assertIsNone(deviceid.status(self.ME, None)['ok'])
        self.assertEqual(deviceid.status('garbage', self.ME)['bound'], False)                       # a bad value binds nothing

    def test_only_a_locked_card_on_another_board_is_stopped(self):
        locked = dict(DEVICE_SERIAL=self.ME, DEVICE_LOCK='1')
        self.assertEqual(deviceid.check(locked, self.ME)[0], 0)
        code, message = deviceid.check(locked, self.OTHER)
        self.assertEqual(code, 78)
        self.assertIn(self.ME, message)
        self.assertIn(self.OTHER, message)
        self.assertEqual(deviceid.check(dict(DEVICE_SERIAL=self.ME), self.OTHER)[0], 0)             # bound but not locked: a warning on the page only
        self.assertEqual(deviceid.check(dict(DEVICE_LOCK='1'), self.OTHER)[0], 0)                   # locked to nothing
        self.assertEqual(deviceid.check({}, self.OTHER)[0], 0)

    def test_a_locked_image_refuses_a_board_whose_serial_cannot_be_read(self):
        code, message = deviceid.check(dict(DEVICE_SERIAL=self.ME, DEVICE_LOCK='1'), None)
        self.assertEqual(code, 78)
        self.assertIn('cannot be read', message)

    def test_the_command_line(self):
        import contextlib
        import io
        env = dict(os.environ)
        old = dict(os.environ)
        try:
            os.environ.pop('DEVICE_SERIAL', None)
            os.environ.pop('DEVICE_LOCK', None)
            out = io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                code = deviceid.main(['check'])
            self.assertEqual(code, 0)
        finally:
            os.environ.clear()
            os.environ.update(old)


if __name__ == '__main__':
    unittest.main()
