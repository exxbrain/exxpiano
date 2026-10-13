"""Importing SoundFont banks from a USB drive: the managed library, the transactional copy, the drive discovery and the API.

No hardware: the drive is a folder, the mount commands go through a fake runner. What a real Pi does with a real drive is a separate checklist.
Run from the repository root:   python3 -m unittest discover -s tests -v
"""
import os
import sys
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'pi'))
sys.path.insert(0, HERE)
import bankimport  # noqa: E402
import banklib  # noqa: E402
import usblib  # noqa: E402
from sf2fixture import make_sf2  # noqa: E402
from test_web import ServerCase, web  # noqa: E402


def drive_with(tmp, files):
    """A mounted-drive folder with piano-banks/ and the given {name: bytes-or-None (None = a real small SoundFont)} files."""
    mount = os.path.join(tmp, 'drive')
    folder = os.path.join(mount, 'piano-banks')
    os.makedirs(folder)
    for name, data in files.items():
        path = os.path.join(folder, name)
        if data is None:
            make_sf2(path, presets=[(name, 0, 0)], name=name, sample_bytes=2000)
        else:
            with open(path, 'wb') as f:
                f.write(data)
    return mount


class ImporterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.lib = os.path.join(self.tmp.name, 'library')
        self.src = make_sf2(os.path.join(self.tmp.name, 'Grand.sf2'), presets=[('Grand', 0, 0)], name='Grand', sample_bytes=5000)

    def leftovers(self):
        return [n for n in os.listdir(self.lib) if n.startswith(('.tmp', '.deleting'))] if os.path.isdir(self.lib) else []

    def test_a_good_bank_is_published_with_its_meta_and_listed(self):
        seen = []
        bank_id, already = bankimport.import_bank(self.src, 'Grand.sf2', self.lib, progress=lambda d, t, p: seen.append((d, t, p)))
        self.assertRegex(bank_id, r'^i-[0-9a-f]{16}$')
        self.assertFalse(already)
        self.assertEqual(self.leftovers(), [])
        listed = banklib.list_imports(self.lib)
        self.assertEqual([b['id'] for b in listed], [bank_id])
        self.assertEqual((listed[0]['source'], listed[0]['original_name'], listed[0]['ok']), ('imported', 'Grand.sf2', True))
        self.assertEqual(seen[-1][2], 'done')
        done = [d for d, _, _ in seen]
        self.assertEqual(done, sorted(done))                                     # the progress never goes back

    def test_the_same_bank_is_not_copied_twice(self):
        first, _ = bankimport.import_bank(self.src, 'Grand.sf2', self.lib)
        second, already = bankimport.import_bank(self.src, 'copy.sf2', self.lib)
        self.assertEqual((first, already), (second, True))
        self.assertEqual(len(banklib.list_imports(self.lib)), 1)
        self.assertEqual(self.leftovers(), [])

    def test_not_enough_space_refuses_before_anything_is_written(self):
        full = lambda path: SimpleNamespace(f_bavail=10, f_frsize=4096)         # noqa: E731 - 40 KB free
        with self.assertRaisesRegex(bankimport.ImportFailed, 'not enough space'):
            bankimport.import_bank(self.src, 'Grand.sf2', self.lib, statvfs=full)
        self.assertEqual(banklib.list_imports(self.lib), [])
        self.assertEqual(self.leftovers(), [])

    def test_a_drive_pulled_out_during_the_copy_leaves_no_partial_bank(self):
        class Failing:
            def __init__(self, path, mode):
                self.f = open(path, mode)
                self.reads = 0

            def read(self, n):
                self.reads += 1
                if self.reads > 1:
                    raise OSError('I/O error')
                return self.f.read(100)

            def __enter__(self):
                return self

            def __exit__(self, *a):
                self.f.close()
        with self.assertRaisesRegex(bankimport.ImportFailed, 'interrupted'):
            bankimport.import_bank(self.src, 'Grand.sf2', self.lib, opener=Failing)
        self.assertEqual(banklib.list_imports(self.lib), [])
        self.assertEqual(self.leftovers(), [])

    def test_a_damaged_file_and_a_too_big_one_are_refused(self):
        junk = os.path.join(self.tmp.name, 'junk.sf2')
        with open(junk, 'wb') as f:
            f.write(b'not a soundfont at all' * 100)
        with self.assertRaisesRegex(bankimport.ImportFailed, 'not a usable SoundFont'):
            bankimport.import_bank(junk, 'junk.sf2', self.lib)
        with self.assertRaisesRegex(bankimport.ImportFailed, 'too large'):
            bankimport.import_bank(self.src, 'Grand.sf2', self.lib, mem_limit=1000)
        with self.assertRaisesRegex(bankimport.ImportFailed, 'no longer on the drive'):
            bankimport.import_bank(os.path.join(self.tmp.name, 'gone.sf2'), 'gone.sf2', self.lib)
        self.assertEqual(banklib.list_imports(self.lib), [])
        self.assertEqual(self.leftovers(), [])

    def test_an_incomplete_or_forged_entry_is_not_in_the_catalog(self):
        bank_id, _ = bankimport.import_bank(self.src, 'Grand.sf2', self.lib)
        os.makedirs(os.path.join(self.lib, 'i-0123456789abcdef'))                 # a folder without meta.json
        with open(os.path.join(self.lib, bank_id, 'bank.sf2'), 'ab') as f:         # the size no longer agrees with meta.json
            f.write(b'x')
        self.assertEqual(banklib.list_imports(self.lib), [])
        for bad in ('../x', 'i-zz', 'bank.sf2', None, 5, 'i-0123456789abcdef'):
            with self.subTest(bad), self.assertRaises(ValueError):
                banklib.resolve_any(self.tmp.name, self.lib, bad)

    def test_delete_refuses_the_banks_in_use_and_removes_the_others(self):
        a, _ = bankimport.import_bank(self.src, 'Grand.sf2', self.lib)
        other = make_sf2(os.path.join(self.tmp.name, 'Upright.sf2'), presets=[('Up', 0, 0)], name='Upright', sample_bytes=3000)
        b, _ = bankimport.import_bank(other, 'Upright.sf2', self.lib)
        path_a = banklib.resolve_any(self.tmp.name, self.lib, a)
        with self.assertRaisesRegex(ValueError, 'in use'):
            banklib.delete_import(self.lib, a, keep_paths=[path_a])               # the current bank
        with self.assertRaisesRegex(ValueError, 'in use'):
            banklib.delete_import(self.lib, a, keep_paths=[None, path_a])         # the last good bank
        with self.assertRaisesRegex(ValueError, 'no such imported bank'):
            banklib.delete_import(self.lib, 'i-0000000000000000')
        banklib.delete_import(self.lib, b, keep_paths=[path_a])
        self.assertEqual([x['id'] for x in banklib.list_imports(self.lib)], [a])
        self.assertEqual(self.leftovers(), [])

    def test_stale_temporary_folders_are_cleaned_but_only_ours_and_only_old(self):
        os.makedirs(os.path.join(self.lib, '.tmp-import-old'))
        os.makedirs(os.path.join(self.lib, '.tmp-import-new'))
        os.makedirs(os.path.join(self.lib, 'precious'))
        old = time.time() - 7200
        os.utime(os.path.join(self.lib, '.tmp-import-old'), (old, old))
        bankimport.stale_cleanup(self.lib)
        self.assertEqual(sorted(os.listdir(self.lib)), ['.tmp-import-new', 'precious'])


class DriveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_only_plain_sound_font_files_of_piano_banks_are_offered(self):
        mount = drive_with(self.tmp.name, {'Grand.sf2': b'x' * 10, 'Soft.SF3': b'y' * 10, '.hidden.sf2': b'z', 'notes.txt': b'n', 'mech.sf2': b'm',
                                           'strings.sfz': b's', 'empty.sf2': b''})
        folder = os.path.join(mount, 'piano-banks')
        os.makedirs(os.path.join(folder, 'inner.sf2'))                              # a folder named like a bank
        os.symlink('/etc/passwd', os.path.join(folder, 'link.sf2'))                  # a link
        os.makedirs(os.path.join(folder, 'sub'))
        with open(os.path.join(folder, 'sub', 'deep.sf2'), 'wb') as f:
            f.write(b'q')                                                           # one level deeper is not looked at
        self.assertEqual([c['name'] for c in usblib.list_candidates(mount)], ['Grand.sf2', 'Soft.SF3'])

    def test_no_folder_or_a_linked_folder_gives_nothing(self):
        plain = os.path.join(self.tmp.name, 'plain')
        os.makedirs(plain)
        self.assertEqual(usblib.list_candidates(plain), [])
        linked = os.path.join(self.tmp.name, 'linked')
        os.makedirs(linked)
        os.symlink(self.tmp.name, os.path.join(linked, 'piano-banks'))
        self.assertEqual(usblib.list_candidates(linked), [])

    def test_a_name_from_the_browser_must_be_in_the_listing(self):
        mount = drive_with(self.tmp.name, {'Grand.sf2': b'x'})
        self.assertTrue(usblib.resolve_candidate(mount, 'Grand.sf2').endswith(os.path.join('piano-banks', 'Grand.sf2')))
        for bad in ('../Grand.sf2', '/etc/passwd', 'piano-banks/Grand.sf2', 'Other.sf2', '', None, 5):
            with self.subTest(bad), self.assertRaises(ValueError):
                usblib.resolve_candidate(mount, bad)

    def runner(self, blkid_out, mount_rc=0):
        calls = []

        def run(cmd):
            calls.append(cmd)
            if cmd[0] == 'blkid':
                return (0, blkid_out) if blkid_out is not None else (2, '')
            if cmd[0] == 'mount':
                return mount_rc, ''
            return 0, ''
        return run, calls

    def test_a_supported_drive_is_mounted_read_only_and_published_then_forgotten(self):
        status, root = os.path.join(self.tmp.name, 'status.json'), os.path.join(self.tmp.name, 'mnt')
        run, calls = self.runner('TYPE=exfat\nLABEL=My piano / banks\n')
        entry = usblib.add('/dev/sda1', run, status, root)
        self.assertTrue(entry['mounted'])
        mount = [c for c in calls if c[0] == 'mount'][0]
        self.assertEqual(mount[:5], ['mount', '-t', 'exfat', '-o', 'ro,nosuid,nodev,noexec'])
        self.assertEqual(os.path.dirname(entry['mountpoint']), root)                # a label with spaces and a slash stays inside the root
        self.assertNotIn(' ', os.path.basename(entry['mountpoint']))
        self.assertEqual([d['id'] for d in usblib.read_status(status)['drives']], ['/dev/sda1'])
        usblib.add('/dev/sda1', run, status, root)                                  # the same event twice: still one drive
        self.assertEqual(len(usblib.read_status(status)['drives']), 1)
        self.assertTrue(usblib.remove('/dev/sda1', run, status))
        self.assertEqual(usblib.read_status(status)['drives'], [])
        self.assertIn(['umount', '-l', entry['mountpoint']], calls)
        self.assertFalse(usblib.remove('/dev/sda1', run, status))                   # already gone

    def test_unsupported_file_system_unknown_device_and_failed_mount_are_explained(self):
        status, root = os.path.join(self.tmp.name, 'status.json'), os.path.join(self.tmp.name, 'mnt')
        run, calls = self.runner('TYPE=ntfs\nLABEL=Disk\n')
        entry = usblib.add('/dev/sdb1', run, status, root)
        self.assertEqual((entry['mounted'], 'not supported' in entry['reason']), (False, True))
        self.assertFalse([c for c in calls if c[0] == 'mount'])                     # nothing was mounted
        run, _ = self.runner(None)
        self.assertIn('no recognised', usblib.add('/dev/sdc1', run, status, root)['reason'])
        run, _ = self.runner('TYPE=vfat\nLABEL=X\n', mount_rc=32)
        self.assertIn('could not be mounted', usblib.add('/dev/sdd1', run, status, root)['reason'])
        for bad in ('/dev/../etc/passwd', '/dev/sda1; rm -rf /', 'sda1', '/tmp/x', '', None):
            with self.subTest(bad), self.assertRaises(usblib.UsbError):
                usblib.add(bad, run, status, root)

    def test_a_missing_or_broken_status_means_no_drive(self):
        self.assertEqual(usblib.read_status(os.path.join(self.tmp.name, 'none.json')), dict(drives=[]))
        path = os.path.join(self.tmp.name, 'broken.json')
        with open(path, 'w') as f:
            f.write('{not json')
        self.assertEqual(usblib.read_status(path), dict(drives=[]))
        gone = dict(id='/dev/sda1', label='X', fs='vfat', mounted=True, mountpoint=os.path.join(self.tmp.name, 'vanished'))
        self.assertFalse(usblib.drive_state(gone)['ready'])                         # a stale entry whose folder is gone is not offered


class UsbApiTests(ServerCase):
    def setUp(self):
        self.work = tempfile.TemporaryDirectory()
        self.addCleanup(self.work.cleanup)
        w = self.work.name
        self.bank_dir = os.path.join(w, 'installed')
        os.makedirs(self.bank_dir)
        self.mech = make_sf2(os.path.join(self.bank_dir, 'mech.sf2'), presets=[('Hammer', 0, 1)], name='Mechanics')
        self.old = make_sf2(os.path.join(self.bank_dir, 'old.sf2'), presets=[('Old', 0, 0)], name='Old piano')
        self.import_dir = os.path.join(w, 'library')
        self.status = os.path.join(w, 'usb-status.json')
        self.mount = drive_with(w, {'Grand.sf2': None, 'Upright.sf3': b'broken bank'})
        self.start('BANK_DIR=%s\nPIANO_SF2="%s"\nMECH_SF2="%s"\nALSA_DEVICE=auto\nGAIN=1.0\nREVERB=0\nFX_ARGS="--hammer-boost 0"\n'
                   'IMPORT_DIR=%s\nUSB_STATUS=%s\n' % (self.bank_dir, self.old, self.mech, self.import_dir, self.status))

    def plug(self):
        usblib.add('/dev/sda1', lambda cmd: (0, 'TYPE=vfat\nLABEL=PIANO\n') if cmd[0] == 'blkid' else (0, ''), self.status, os.path.join(self.work.name, 'm'))
        entries = usblib.read_status(self.status)['drives']
        entries[0]['mountpoint'] = self.mount                                         # the "mounted" drive is this folder
        usblib._write_json(self.status, dict(drives=entries))

    def wait_import(self):
        for _ in range(200):
            s = self.jcall('/api/usb')[1]['importing']
            if s['state'] in ('done', 'failed'):
                return s
            time.sleep(0.02)
        self.fail('the import did not finish')

    def test_no_drive_means_an_empty_state(self):
        code, s = self.jcall('/api/usb')
        self.assertEqual((code, s['drives'], s['importing']['state']), (200, [], 'idle'))

    def test_the_drive_and_its_files_are_listed(self):
        self.plug()
        d = self.jcall('/api/usb')[1]['drives'][0]
        self.assertEqual((d['label'], d['ready'], d['has_folder']), ('PIANO', True, True))
        self.assertEqual([f['name'] for f in d['files']], ['Grand.sf2', 'Upright.sf3'])

    def test_import_then_switch_then_the_drive_is_not_needed_and_delete_rules_hold(self):
        self.plug()
        code, r = self.jcall('/api/usb', {'action': 'import', 'name': 'Grand.sf2'})
        self.assertEqual((code, r['started']), (200, True))
        s = self.wait_import()
        self.assertEqual(s['state'], 'done', s)
        bank_id = s['bank_id']
        usblib.remove('/dev/sda1', lambda cmd: (0, ''), self.status)                  # the drive is pulled out
        banks = {b['id']: b for b in self.jcall('/api/state')[1]['banks']['banks']}
        self.assertEqual((banks[bank_id]['source'], banks['old.sf2']['source']), ('imported', 'installed'))
        code, r = self.jcall('/api/bank', {'id': bank_id})
        self.assertEqual((code, r['started']), (200, True))
        self.app.switcher.join(10)
        self.assertEqual(self.jcall('/api/state')[1]['banks']['current'], bank_id)
        self.assertIn(self.import_dir, self.conf_text())                              # the configuration points into the library
        code, r = self.jcall('/api/usb', {'action': 'delete', 'id': bank_id})
        self.assertEqual(code, 400)                                                   # the bank in use
        self.assertIn('in use', r['error'])
        self.jcall('/api/bank', {'id': 'old.sf2'})
        self.app.switcher.join(10)
        path = banklib.resolve_any(self.bank_dir, self.import_dir, bank_id)
        with open(self.conf, 'a') as f:
            f.write('PIANO_SF2_LAST_GOOD="%s"\n' % path)                              # an old bank is still the fallback
        code, r = self.jcall('/api/usb', {'action': 'delete', 'id': bank_id})
        self.assertEqual(code, 400)
        self.assertEqual(os.listdir(self.import_dir), [bank_id])
        with open(self.conf, 'a') as f:
            f.write('PIANO_SF2_LAST_GOOD="%s"\n' % self.old)                          # the fallback moved on: nothing holds the bank any more
        code, r = self.jcall('/api/usb', {'action': 'delete', 'id': bank_id})
        self.assertEqual((code, r['deleted']), (200, True))
        self.assertEqual(os.listdir(self.import_dir), [])

    def test_a_broken_bank_fails_with_a_safe_message_and_nothing_stays(self):
        self.plug()
        self.jcall('/api/usb', {'action': 'import', 'name': 'Upright.sf3'})
        s = self.wait_import()
        self.assertEqual(s['state'], 'failed')
        self.assertIn('not a usable SoundFont', s['error'])
        self.assertNotIn(self.work.name, s['error'])                                  # no internal paths
        self.assertEqual([n for n in os.listdir(self.import_dir)], [])

    def test_refusals_and_a_second_import_while_one_runs(self):
        self.plug()
        for body in ({'action': 'import', 'name': '../Grand.sf2'}, {'action': 'import', 'name': 'Missing.sf2'}, {'action': 'import'},
                     {'action': 'delete', 'id': '../x'}, {'action': 'explode'}):
            with self.subTest(body):
                self.assertEqual(self.call('/api/usb', body)[0], 400)
        self.assertEqual(self.call('/api/usb', raw=b'[1]', ctype='application/json')[0], 400)
        gate = threading.Event()
        real = web.bankimport.import_bank

        def slow(*a, **kw):
            gate.wait(5)
            return real(*a, **kw)
        web.bankimport.import_bank = slow
        self.addCleanup(setattr, web.bankimport, 'import_bank', real)
        self.addCleanup(gate.set)
        self.assertEqual(self.jcall('/api/usb', {'action': 'import', 'name': 'Grand.sf2'})[0], 200)
        code, r = self.jcall('/api/usb', {'action': 'import', 'name': 'Grand.sf2'})
        self.assertEqual(code, 409)
        gate.set()
        self.assertEqual(self.wait_import()['state'], 'done')

    def test_the_token_protects_the_usb_api_too(self):
        self.start('BANK_DIR=%s\nPIANO_SF2="%s"\nMECH_SF2="%s"\nWEB_TOKEN=secret\nIMPORT_DIR=%s\nUSB_STATUS=%s\n' %
                   (self.bank_dir, self.old, self.mech, self.import_dir, self.status))
        self.assertEqual(self.call('/api/usb')[0], 401)
        self.assertEqual(self.call('/api/usb', {'action': 'delete', 'id': 'i-0000000000000000'})[0], 401)


if __name__ == '__main__':
    unittest.main()
