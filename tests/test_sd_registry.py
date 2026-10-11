"""Tests for scripts/sd-registry.py: the register of the devices that were made."""
import contextlib
import importlib.util
import io
import os
import tempfile
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
spec = importlib.util.spec_from_file_location('sd_registry', os.path.join(ROOT, 'scripts', 'sd-registry.py'))
reg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reg)


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, 'sub', 'devices.csv')

    def add(self, number, **kw):
        return reg.add(dict(number=number, hostname='exxpiano-' + reg.normalize(number).lower(), version='1.8.1', **kw), self.path)

    def test_numbers_are_normalised(self):
        self.assertEqual([reg.normalize(x) for x in ('7', '0007', ' 12 ', 'ab12', 'A1')], ['0007', '0007', '0012', 'AB12', 'A1'])
        for bad in ('', 'a b', '123456789', '../1', 'é'):
            with self.subTest(bad), self.assertRaises(reg.RegistryError):
                reg.normalize(bad)

    def test_the_first_and_the_next_number(self):
        self.assertEqual(reg.next_number(self.path), '0001')                       # no file yet
        self.add('1')
        self.add('0005')
        self.add('AB12')                                                          # a number with letters is not counted
        self.assertEqual(reg.next_number(self.path), '0006')

    def test_a_number_cannot_be_used_twice(self):
        self.add('7', banks='piano.sf2')
        with self.assertRaises(reg.RegistryError) as ctx:
            self.add('0007')
        self.assertIn('already registered', str(ctx.exception))
        self.assertEqual(len(reg.load(self.path)), 1)

    def test_replacing_keeps_the_old_line_marked(self):
        self.add('7', note='first card')
        reg.add(dict(number='7', hostname='exxpiano-0007', version='1.9.0'), self.path, replace=True)
        rows = reg.load(self.path)
        self.assertEqual([r['number'] for r in rows], ['0007~', '0007'])
        self.assertIn('replaced', rows[0]['note'])
        self.assertEqual(reg.get('7', self.path)['version'], '1.9.0')

    def test_a_record_has_a_timestamp_and_survives_odd_text(self):
        row = reg.add(dict(number='3', hostname='h', version='1', note='sold to "Ivan", Moscow; 12 Oct\nsecond line'), self.path)
        self.assertRegex(row['built_at'], r'^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$')
        self.assertEqual(reg.get('3', self.path)['note'], 'sold to "Ivan", Moscow; 12 Oct\nsecond line')   # quotes, commas, newlines

    def test_notes_and_find(self):
        self.add('1')
        self.add('2')
        reg.set_note('2', 'Sold to Ivan', self.path)
        self.assertEqual([r['number'] for r in reg.find('ivan', self.path)], ['0002'])
        self.assertEqual([r['number'] for r in reg.find('EXXPIANO-0001', self.path)], ['0001'])
        self.assertEqual(reg.find('nobody', self.path), [])
        with self.assertRaises(reg.RegistryError):
            reg.set_note('9', 'x', self.path)

    def test_a_board_is_found_by_its_low_32_bits_and_replaced_cards_are_ignored(self):
        reg.add(dict(number='1', serial='00000000d2f2f1de', version='1'), self.path)
        self.assertEqual(reg.find_serial('D2F2F1DE', self.path)['number'], '0001')
        self.assertIsNone(reg.find_serial('00000000aaaabbbb', self.path))
        reg.add(dict(number='1', serial='00000000d2f2f1de', version='2'), self.path, replace=True)
        self.assertEqual(reg.find_serial('00000000d2f2f1de', self.path)['version'], '2')            # the old line (0001~) does not count

    def test_a_damaged_row_does_not_break_reading(self):
        os.makedirs(os.path.dirname(self.path))
        with open(self.path, 'w') as f:
            f.write('number,hostname\n0001,exxpiano-0001\n')                         # old file with fewer columns
        self.assertEqual(reg.load(self.path)[0]['version'], '')
        self.assertEqual(reg.next_number(self.path), '0002')

    def test_the_command_line(self):
        def run(*args):
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = reg.main(['--registry', self.path] + list(args))
            return code, out.getvalue(), err.getvalue()
        self.assertEqual(run('next')[1].strip(), '0001')
        self.assertIn('nothing registered', run('list')[1])
        self.add('1', banks='a.sf2')
        code, out, _ = run('list')
        self.assertTrue(out.startswith('number') and 'exxpiano-0001' in out)
        self.assertEqual(run('note', '1', 'for the kitchen')[0], 0)
        self.assertIn('for the kitchen', run('show', '1')[1])
        code, out, err = run('show', '5')
        self.assertEqual(code, 1)
        self.assertIn('no such number', err)
        self.assertIn('exxpiano-0001', run('find', 'kitchen')[1])


if __name__ == '__main__':
    unittest.main()
