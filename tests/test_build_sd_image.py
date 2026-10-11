"""Tests for scripts/build-sd-image.py with a fake `docker` (no Docker, no image, no root): inputs, the number and the register, the mounts, the
packing, the sticker, the README. The real build is exercised by scripts/sd-fake-dietpi.sh + sd-image-inner.sh + sd-smoke-test.sh in Docker."""
import importlib.util
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
import zipfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
SCRIPT = os.path.join(ROOT, 'scripts', 'build-sd-image.py')
spec = importlib.util.spec_from_file_location('build_sd_image', SCRIPT)
bsi = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bsi)

FAKE_DOCKER = r'''#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
log = os.environ['FAKE_DOCKER_LOG']
with open(log, 'a') as f:
    f.write(json.dumps(dict(args=args, env={k: os.environ.get(k) for k in ('SD_PASSWORD', 'SD_AP_PASSWORD')})) + '\n')
if args[:1] == ['info']:
    sys.exit(int(os.environ.get('FAKE_DOCKER_INFO', '0')))
if args[:2] == ['image', 'inspect']:
    sys.exit(0)
if args[:1] == ['run']:
    if 'sd-image-inner.sh' in ' '.join(args):
        out = args[args.index('--out') + 1]                       # /out/piano-synth-0007.img
        host = [a.split(':')[0] for a in args if a.endswith(':/out')][0]
        if os.environ.get('FAKE_DOCKER_FAIL_BUILD'):
            sys.exit(1)
        after = os.environ.get('FAKE_DOCKER_FAIL_AFTER')
        if after:                                                  # the builds after the first N fail
            counter = log + '.builds'
            n = int(open(counter).read()) + 1 if os.path.exists(counter) else 1
            open(counter, 'w').write(str(n))
            if n > int(after):
                sys.exit(1)
        with open(os.path.join(host, os.path.basename(out)), 'wb') as f:
            f.write(b'fake image ' * 1000)
    if 'sd-smoke-test.sh' in ' '.join(args) and os.environ.get('FAKE_DOCKER_FAIL_SMOKE'):
        sys.exit(1)
sys.exit(0)
'''


class Case(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        t = self.tmp.name
        self.bin = os.path.join(t, 'bin')
        os.makedirs(self.bin)
        with open(os.path.join(self.bin, 'docker'), 'w') as f:
            f.write(FAKE_DOCKER)
        os.chmod(os.path.join(self.bin, 'docker'), 0o755)
        self.log = os.path.join(t, 'docker.log')
        self.registry = os.path.join(t, 'reg', 'devices.csv')
        self.out = os.path.join(t, 'out')
        self.dietpi = self.touch('dietpi/DietPi_test.img.xz', b'dietpi')
        self.piano = self.touch('banks/piano.sf2', b'p' * 10)
        self.mech = self.touch('banks/mech.sf2', b'm' * 10)
        self.env = dict(os.environ, PATH=self.bin + os.pathsep + os.environ['PATH'], FAKE_DOCKER_LOG=self.log, PIANO_SD_WORK=os.path.join(t, 'work'))

    def touch(self, rel, data):
        path = os.path.join(self.tmp.name, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'wb') as f:
            f.write(data)
        return path

    def run_build(self, *extra, env=None):
        args = ['--dietpi', self.dietpi, '--piano', self.piano, '--mech', self.mech, '--registry', self.registry, '--out', self.out]
        r = subprocess.run([sys.executable, SCRIPT] + args + list(extra), capture_output=True, text=True, env=dict(self.env, **(env or {})))
        return r

    def docker_calls(self):
        try:
            with open(self.log) as f:
                return [json.loads(l) for l in f]
        except FileNotFoundError:
            return []


class PlanTests(Case):
    def test_a_dry_run_checks_and_makes_nothing(self):
        r = self.run_build('--number', '7', '--dry-run')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('Device № 0007 (piano-0007)', r.stdout)
        self.assertIn('Piano-Setup-0007', r.stdout)
        self.assertIn('dry run', r.stdout)
        self.assertFalse(os.path.exists(self.out) or os.path.exists(self.registry))
        self.assertEqual(self.docker_calls(), [])

    def test_missing_inputs_are_named(self):
        r = subprocess.run([sys.executable, SCRIPT, '--registry', self.registry, '--piano', self.piano, '--dry-run'], capture_output=True, text=True, env=self.env)
        self.assertEqual(r.returncode, 1)
        self.assertIn('--dietpi', r.stderr)
        r = self.run_build('--dry-run', '--piano', os.path.join(self.tmp.name, 'nope.sf2'))
        self.assertIn('no such bank file', r.stderr)
        r = subprocess.run([sys.executable, SCRIPT, '--dietpi', self.dietpi, '--registry', self.registry, '--dry-run'], capture_output=True, text=True, env=self.env)
        self.assertIn('--piano', r.stderr)
        r = subprocess.run([sys.executable, SCRIPT, '--dietpi', '/nonexistent.img.xz', '--no-banks', '--registry', self.registry, '--dry-run'], capture_output=True, text=True, env=self.env)
        self.assertIn('no such DietPi image', r.stderr)

    def test_bad_values_are_refused_before_anything_starts(self):
        for extra, text in ((('--number', 'a b'), 'number'), (('--password', 'short'), 'at least 8'), (('--ap-password', 'short'), '8 to 63'),
                            (('--name', 'Bad_Name'), 'lowercase')):
            with self.subTest(extra):
                r = self.run_build('--dry-run', *extra)
                self.assertEqual(r.returncode, 1)
                self.assertIn(text, r.stderr)

    def test_without_banks_no_bank_files_are_needed(self):
        r = subprocess.run([sys.executable, SCRIPT, '--dietpi', self.dietpi, '--no-banks', '--registry', self.registry, '--out', self.out, '--dry-run'],
                           capture_output=True, text=True, env=self.env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('(none: install later)', r.stdout)


class BuildTests(Case):
    def test_a_build_makes_the_zip_the_sticker_the_readme_and_the_register_entry(self):
        r = self.run_build('--number', 'auto', '--password', 'root password 1', '--ap-password', 'sticker pw 1', '--note', 'for Ivan', '--country', 'RU')
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        out = os.path.join(self.out, '0001')
        zip_path = os.path.join(out, 'piano-synth-0001-sd.zip')
        with zipfile.ZipFile(zip_path) as z:
            self.assertEqual(z.namelist(), ['piano-synth-0001.img'])
            self.assertEqual(z.read('piano-synth-0001.img'), b'fake image ' * 1000)
        self.assertFalse(os.path.exists(os.path.join(out, 'piano-synth-0001.img')))        # the raw image is not kept
        sticker = os.listdir(os.path.join(out, 'sticker'))
        self.assertIn('setup-codes-Piano-Setup-0001.html', sticker)
        self.assertIn('join-wifi-Piano-Setup-0001.png', sticker)
        with open(os.path.join(out, 'sticker', 'setup-codes-Piano-Setup-0001.html'), encoding='utf-8') as f:
            self.assertIn('0001', f.read())
        with open(os.path.join(out, 'README-0001.txt'), encoding='utf-8') as f:
            text = f.read()
        for needle in ('№ 0001', 'piano-0001', 'Piano-Setup-0001', 'sticker pw 1', 'Ethernet'):
            self.assertIn(needle, text)
        import importlib.util as iu
        spec2 = iu.spec_from_file_location('reg', os.path.join(ROOT, 'scripts', 'sd-registry.py'))
        reg = iu.module_from_spec(spec2)
        spec2.loader.exec_module(reg)
        row = reg.get('1', self.registry)
        self.assertEqual((row['hostname'], row['ssid'], row['note'], row['image']), ('piano-0001', 'Piano-Setup-0001', 'for Ivan', 'piano-synth-0001-sd.zip'))
        self.assertRegex(row['image_sha256'], r'^[0-9a-f]{64}$')
        self.assertEqual(row['banks'], 'piano.sf2, mech.sf2')

    def test_the_container_gets_read_only_inputs_a_writable_output_and_the_passwords_in_the_environment(self):
        self.run_build('--number', '7', '--password', 'root password 1', '--ap-password', 'sticker pw 1')
        calls = [c for c in self.docker_calls() if c['args'][:1] == ['run']]
        build = [c for c in calls if 'sd-image-inner.sh' in ' '.join(c['args'])][0]
        args = build['args']
        self.assertIn('--privileged', args)
        mounts = [args[i + 1] for i, a in enumerate(args) if a == '-v']
        self.assertTrue(any(m.endswith(':/repo:ro') for m in mounts))
        self.assertTrue(any(m.endswith(':/out') for m in mounts))
        inputs = [m for m in mounts if m.endswith(':ro') and '/in/' in m]
        self.assertEqual(len(inputs), 4)                                                     # the image, the package, both banks
        self.assertEqual((args[args.index('--number') + 1], args[args.index('--hostname') + 1]), ('0007', 'piano-0007'))
        self.assertNotIn('root password 1', ' '.join(args))                                  # no password in the arguments ...
        self.assertNotIn('sticker pw 1', ' '.join(args))
        self.assertEqual(build['env'], {'SD_PASSWORD': 'root password 1', 'SD_AP_PASSWORD': 'sticker pw 1'})   # ... but in the environment

    def test_the_finished_image_is_run_in_a_container_too(self):
        self.run_build('--number', '3')
        smoke = [c for c in self.docker_calls() if 'sd-smoke-test.sh' in ' '.join(c['args'])]
        self.assertEqual(len(smoke), 1)
        self.assertIn('/out/piano-synth-0003.img', smoke[0]['args'])
        self.docker_calls()
        r = self.run_build('--number', '4', '--no-smoke')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(len([c for c in self.docker_calls() if 'sd-smoke-test.sh' in ' '.join(c['args'])]), 1)    # still only the first one

    def test_numbers_are_unique_and_auto_takes_the_next(self):
        self.assertEqual(self.run_build('--number', '5').returncode, 0)
        r = self.run_build('--number', '5')
        self.assertEqual(r.returncode, 1)
        self.assertIn('already registered', r.stderr)
        self.assertEqual(self.run_build('--number', 'auto').returncode, 0)
        self.assertTrue(os.path.isdir(os.path.join(self.out, '0006')))
        r = self.run_build('--number', '5', '--force', '--note', 'new card')
        self.assertEqual(r.returncode, 0, r.stderr)
        with open(self.registry) as f:
            text = f.read()
        self.assertIn('0005~', text)                                                         # the old line stays, marked

    def test_a_failed_build_leaves_no_entry_and_says_so(self):
        r = self.run_build('--number', '9', env=dict(FAKE_DOCKER_FAIL_BUILD='1'))
        self.assertEqual(r.returncode, 1)
        self.assertIn('could not be made', r.stderr)
        self.assertFalse(os.path.exists(self.registry))
        r = self.run_build('--number', '9', env=dict(FAKE_DOCKER_FAIL_SMOKE='1'))
        self.assertEqual(r.returncode, 1)
        self.assertIn('smoke test', r.stderr)
        self.assertFalse(os.path.exists(self.registry))                                      # a card that fails its check is not registered

    def test_docker_that_is_not_running_is_explained(self):
        r = self.run_build('--number', '2', env=dict(FAKE_DOCKER_INFO='1'))
        self.assertEqual(r.returncode, 1)
        self.assertIn('Docker is not running', r.stderr)
        self.assertFalse(os.path.exists(self.registry))


class SerialTests(Case):
    ME = '00000000d2f2f1de'

    def registry_module(self):
        spec2 = importlib.util.spec_from_file_location('reg2', os.path.join(ROOT, 'scripts', 'sd-registry.py'))
        reg = importlib.util.module_from_spec(spec2)
        spec2.loader.exec_module(reg)
        return reg

    def inner_args(self):
        build = [c for c in self.docker_calls() if 'sd-image-inner.sh' in ' '.join(c['args'])]
        return [c['args'] for c in build]

    def test_a_card_for_one_board_carries_its_serial_and_the_lock(self):
        r = self.run_build('--serial', 'D2F2F1DE', '--lock', '--number', 'serial')
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        (args,) = self.inner_args()
        self.assertEqual(args[args.index('--serial') + 1], self.ME)                         # normalised to 16 digits
        self.assertIn('--lock', args)
        self.assertEqual(args[args.index('--number') + 1], 'F1DE')                           # the last four digits of the serial
        row = self.registry_module().get('F1DE', self.registry)
        self.assertEqual((row['serial'], row['locked']), (self.ME, 'yes'))
        with open(os.path.join(self.out, 'F1DE', 'README-F1DE.txt'), encoding='utf-8') as f:
            text = f.read()
        self.assertIn(self.ME, text)
        self.assertIn('refuses another board', text)

    def test_without_the_lock_only_the_serial_is_written(self):
        self.run_build('--serial', self.ME)
        (args,) = self.inner_args()
        self.assertIn('--serial', args)
        self.assertNotIn('--lock', args)
        self.assertEqual(self.registry_module().get('1', self.registry)['locked'], '')

    def test_bad_serials_and_a_lock_without_a_serial_are_refused(self):
        for extra, text in ((('--serial', 'nonsense'), 'hex digits'), (('--lock',), 'needs --serial'), (('--serial', self.ME, '--serials', 'x.csv'), 'not both'),
                            (('--number', 'serial'), 'needs --serial')):
            with self.subTest(extra):
                r = self.run_build('--dry-run', *extra)
                self.assertEqual(r.returncode, 1)
                self.assertIn(text, r.stderr)

    def test_a_board_gets_one_card_unless_forced(self):
        self.assertEqual(self.run_build('--serial', self.ME).returncode, 0)
        r = self.run_build('--serial', 'D2F2F1DE')                                           # the same board, written differently
        self.assertEqual(r.returncode, 1)
        self.assertIn('already has a card', r.stderr)
        self.assertEqual(self.run_build('--serial', self.ME, '--force').returncode, 0)

    def write_list(self, text):
        path = os.path.join(self.tmp.name, 'boards.csv')
        with open(path, 'w') as f:
            f.write(text)
        return path

    def test_a_list_makes_a_card_per_board_with_numbers_names_and_notes(self):
        path = self.write_list('# the boards of the first batch\nserial,number,name,note\n00000000d2f2f1de,,,Ivan\n00000000aaaabbbb,0042,piano-kitchen,Olga\n100000001234cdef,,,\n')
        r = self.run_build('--serials', path, '--lock', '--note', 'batch 1')
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn('All 3 cards are made: 0001, 0042, 0043', r.stdout)
        reg = self.registry_module()
        self.assertEqual([(r_['number'], r_['hostname'], r_['note'], r_['locked']) for r_ in reg.load(self.registry)],
                         [('0001', 'piano-0001', 'Ivan', 'yes'), ('0042', 'piano-kitchen', 'Olga', 'yes'), ('0043', 'piano-0043', 'batch 1', 'yes')])
        self.assertEqual(len(self.inner_args()), 3)

    def test_a_bad_list_is_refused_before_the_first_card(self):
        for text, needle in (('serial\n00000000d2f2f1de\n00000000D2F2F1DE\n', 'listed twice'), ('serial\nxyz\n', 'hex digits'), ('number\n7\n', 'columns'),
                             ('', 'columns'), ('serial,number\n00000000d2f2f1de,7\n00000000aaaabbbb,0007\n', 'twice in this run')):
            with self.subTest(text):
                r = self.run_build('--serials', self.write_list(text))
                self.assertEqual(r.returncode, 1)
                self.assertIn(needle, r.stderr)
                self.assertEqual(self.inner_args(), [])
                self.assertFalse(os.path.exists(self.registry))

    def test_a_failure_in_the_middle_says_which_cards_were_made(self):
        path = self.write_list('serial\n00000000d2f2f1de\n00000000aaaabbbb\n100000001234cdef\n')
        r = self.run_build('--serials', path, env=dict(FAKE_DOCKER_FAIL_AFTER='1'))
        self.assertEqual(r.returncode, 1)
        self.assertIn('could not be made', r.stderr)
        self.assertIn('made before the failure: 0001', r.stderr)
        self.assertEqual([row['number'] for row in self.registry_module().load(self.registry)], ['0001'])       # only the finished card is registered
        self.assertEqual(len(self.inner_args()), 2)                                                             # and the third was not attempted


class DownloadTests(unittest.TestCase):
    class Response:
        def __init__(self, data):
            import io
            self.buf = io.BytesIO(data)

        def read(self, n=-1):
            return self.buf.read(n)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def test_the_image_is_checked_against_its_published_sha256(self):
        import hashlib
        data = b'x' * 3000
        good = hashlib.sha256(data).hexdigest()
        with tempfile.TemporaryDirectory() as d:
            dest = os.path.join(d, 'sub', 'image.img.xz')
            fetch = lambda url: self.Response(data if not url.endswith('.sha256') else (good + '  image.img.xz\n').encode())
            self.assertEqual(bsi.download('http://x/image.img.xz', dest, fetch), dest)
            with open(dest, 'rb') as f:
                self.assertEqual(f.read(), data)
            bad = lambda url: self.Response(data if not url.endswith('.sha256') else b'0' * 64 + b'  image\n')
            with self.assertRaises(bsi.BuildError):
                bsi.download('http://x/other.img.xz', os.path.join(d, 'other.img.xz'), bad)
            self.assertEqual(sorted(os.listdir(d)), ['sub'])                               # a bad download leaves nothing behind

    def test_a_missing_checksum_is_not_fatal(self):
        def fetch(url):
            if url.endswith('.sha256'):
                raise OSError('404')
            return self.Response(b'data')
        with tempfile.TemporaryDirectory() as d:
            bsi.download('http://x/a.img.xz', os.path.join(d, 'a.img.xz'), fetch)
            self.assertTrue(os.path.isfile(os.path.join(d, 'a.img.xz')))


class ConfQuotingTests(unittest.TestCase):
    def test_a_tricky_password_comes_back_unchanged_through_the_programs_parser(self):
        sys.path.insert(0, os.path.join(ROOT, 'pi'))
        import pianoconf
        src = open(os.path.join(ROOT, 'scripts', 'sd-image-inner.sh')).read()
        start = src.index('set_kv() {')
        end = src.index('\n}\n', start) + 3
        qstart = src.index('quote_conf() {')
        qend = src.index('\n}\n', qstart) + 3
        for password in ('plain password', 'a"b\\c d#e$f', "it's; 100% `ok`", '\\\\\\', 'x"'):
            with self.subTest(password), tempfile.TemporaryDirectory() as d:
                f = os.path.join(d, 'piano-synth.conf')
                with open(f, 'w') as fh:
                    fh.write('GAIN=1.0\n# WIFI_AP_PASSWORD=pianosetup\n')
                script = src[start:end] + src[qstart:qend] + '\nset_kv "$F" WIFI_AP_PASSWORD "$(quote_conf "$P")"\n'
                subprocess.run(['bash', '-c', script], env=dict(os.environ, F=f, P=password), check=True)
                self.assertEqual(pianoconf.parse_conf(f)['WIFI_AP_PASSWORD'], password)
                self.assertEqual(pianoconf.parse_conf(f)['GAIN'], '1.0')


class ShellScriptTests(unittest.TestCase):
    def test_the_scripts_have_valid_syntax_and_the_key_value_helper_behaves(self):
        for name in ('sd-image-inner.sh', 'sd-fake-dietpi.sh', 'sd-smoke-test.sh'):
            r = subprocess.run(['bash', '-n', os.path.join(ROOT, 'scripts', name)], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, name + r.stderr)
        r = subprocess.run(['bash', '-n', os.path.join(ROOT, 'dietpi', 'Automation_Custom_Script.image.sh')], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        # set_kv is copied out of the script and tried on a file (awk is on every machine)
        src = open(os.path.join(ROOT, 'scripts', 'sd-image-inner.sh')).read()
        start = src.index('set_kv() {')
        end = src.index('\n}\n', start) + 3
        with tempfile.TemporaryDirectory() as d:
            f = os.path.join(d, 'dietpi.txt')
            with open(f, 'w') as fh:
                fh.write('A=1\n#B=2\nC=3\nB_OTHER=keep\ndtparam=audio=off\n')
            script = src[start:end] + '\nset_kv "$F" A 10; set_kv "$F" B "two words & \\\\ back"; set_kv "$F" NEW x; set_kv "$F" dtparam=audio on\n'
            subprocess.run(['bash', '-c', script], env=dict(os.environ, F=f), check=True)
            with open(f) as fh:
                lines = fh.read().split('\n')
        self.assertEqual(lines[:6], ['A=10', 'B=two words & \\ back', 'C=3', 'B_OTHER=keep', 'dtparam=audio=on', 'NEW=x'])        # a value is kept literally


if __name__ == '__main__':
    unittest.main()
