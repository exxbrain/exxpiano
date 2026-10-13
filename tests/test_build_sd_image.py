"""Fast tests for the reusable SD image builder with a fake Docker executable."""
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
        out = args[args.index('--out') + 1]                       # /out/exxpiano.img
        version_out = args[args.index('--dietpi-version-out') + 1]
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
        with open(os.path.join(host, os.path.basename(version_out)), 'w') as f:
            f.write('10.7.0\n')
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
        self.out = os.path.join(t, 'out')
        self.dietpi = self.touch('dietpi/DietPi_test.img.xz', b'dietpi')
        self.piano = self.touch('banks/piano.sf2', b'p' * 10)
        self.mech = self.touch('banks/mech.sf2', b'm' * 10)
        self.env = dict(os.environ, PATH=self.bin + os.pathsep + os.environ['PATH'], FAKE_DOCKER_LOG=self.log, EXXPIANO_SD_WORK=os.path.join(t, 'work'))
        self.env.pop('SD_PASSWORD', None)
        self.env.pop('SD_AP_PASSWORD', None)

    def touch(self, rel, data):
        path = os.path.join(self.tmp.name, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'wb') as f:
            f.write(data)
        return path

    def run_build(self, *extra, env=None, banks=True):
        args = ['--dietpi', self.dietpi, '--out', self.out]
        if banks:
            args += ['--piano', self.piano, '--mech', self.mech]
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
        r = self.run_build('--dry-run')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('installation ID, hostname and setup Wi-Fi are generated automatically', r.stdout)
        self.assertIn('dry run', r.stdout)
        self.assertFalse(os.path.exists(self.out))
        self.assertEqual(self.docker_calls(), [])

    def test_missing_inputs_are_named(self):
        r = subprocess.run([sys.executable, SCRIPT, '--piano', self.piano, '--mech', self.mech, '--dry-run'], capture_output=True, text=True, env=self.env)
        self.assertEqual(r.returncode, 1)
        self.assertIn('--dietpi', r.stderr)
        r = self.run_build('--dry-run', '--piano', os.path.join(self.tmp.name, 'nope.sf2'))
        self.assertIn('required piano bank', r.stderr)
        r = subprocess.run([sys.executable, SCRIPT, '--dietpi', '/nonexistent.img.xz', '--piano', self.piano, '--mech', self.mech,
                            '--dry-run'], capture_output=True, text=True, env=self.env)
        self.assertIn('no such DietPi image', r.stderr)

    def test_bad_values_are_refused_before_anything_starts(self):
        for extra, text in ((('--password', 'short'), 'at least 8'), (('--ap-password', 'short'), '8 to 63')):
            with self.subTest(extra):
                r = self.run_build('--dry-run', *extra)
                self.assertEqual(r.returncode, 1)
                self.assertIn(text, r.stderr)

    def test_no_banks_mode_is_not_part_of_the_product_interface(self):
        r = self.run_build('--no-banks', '--dry-run')
        self.assertEqual(r.returncode, 2)
        self.assertIn('unrecognized arguments: --no-banks', r.stderr)

    def test_serials_and_numbers_are_not_part_of_the_product_interface(self):
        for args in (('--number', 'auto'), ('--serial', '000000003c1c783e'), ('--serials', 'boards.csv')):
            r = self.run_build(*args, '--dry-run')
            self.assertEqual(r.returncode, 2)
            self.assertIn('unrecognized arguments', r.stderr)

    def test_default_banks_and_explicit_overrides_are_resolved_before_planning(self):
        old_root = bsi.ROOT
        try:
            bsi.ROOT = self.tmp.name
            default_piano = self.touch('build/piano-standard.sf2', b'default piano')
            default_mech = self.touch('build/mech.sf2', b'default mech')
            args = bsi.parse_args(['--dietpi', self.dietpi])
            self.assertEqual(bsi.resolve_inputs(args), dict(dietpi=os.path.abspath(self.dietpi), piano=default_piano, mech=default_mech))
            args = bsi.parse_args(['--dietpi', self.dietpi, '--piano', self.piano, '--mech', self.mech])
            self.assertEqual((bsi.resolve_inputs(args)['piano'], bsi.resolve_inputs(args)['mech']), (self.piano, self.mech))
        finally:
            bsi.ROOT = old_root

    def test_each_missing_default_bank_stops_before_building(self):
        old_root = bsi.ROOT
        try:
            bsi.ROOT = self.tmp.name
            args = bsi.parse_args(['--dietpi', self.dietpi])
            for present, missing in (('mech.sf2', 'piano'), ('piano-standard.sf2', 'mech')):
                build = os.path.join(self.tmp.name, 'build')
                os.makedirs(build, exist_ok=True)
                for name in ('piano-standard.sf2', 'mech.sf2'):
                    path = os.path.join(build, name)
                    if os.path.exists(path):
                        os.unlink(path)
                with open(os.path.join(build, present), 'wb') as f:
                    f.write(b'bank')
                with self.assertRaisesRegex(bsi.BuildError, 'required %s bank' % missing):
                    bsi.resolve_inputs(args)
                self.assertEqual(self.docker_calls(), [])
        finally:
            bsi.ROOT = old_root


class BuildTests(Case):
    def test_passwords_default_to_environment_and_arguments_override_them(self):
        r = self.run_build(env={'SD_PASSWORD': 'environment root password',
                                'SD_AP_PASSWORD': 'environment setup password'})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        builds = [c for c in self.docker_calls() if 'sd-image-inner.sh' in ' '.join(c['args'])]
        self.assertEqual(builds[-1]['env'], {'SD_PASSWORD': 'environment root password',
                                             'SD_AP_PASSWORD': 'environment setup password'})

        r = self.run_build('--password', 'argument root password',
                           '--ap-password', 'argument setup password',
                           env={'SD_PASSWORD': 'ignored root password', 'SD_AP_PASSWORD': 'ignored setup password'})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        builds = [c for c in self.docker_calls() if 'sd-image-inner.sh' in ' '.join(c['args'])]
        self.assertEqual(builds[-1]['env'], {'SD_PASSWORD': 'argument root password',
                                             'SD_AP_PASSWORD': 'argument setup password'})

    def test_a_build_makes_one_reusable_zip_readme_and_manifest(self):
        r = self.run_build('--password', 'root password 1', '--ap-password', 'setup password 1', '--country', 'RU')
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        with open(os.path.join(ROOT, 'VERSION')) as vf:
            version = vf.read().strip()                             # the file name carries the current version: not a literal
        stem = 'exxpiano-rpi234-v%s-dietpi-10.7.0' % version
        zip_path = os.path.join(self.out, stem + '-sd.zip')
        with zipfile.ZipFile(zip_path) as z:
            self.assertEqual(z.namelist(), [stem + '.img'])
            self.assertEqual(z.read(stem + '.img'), b'fake image ' * 1000)
        self.assertFalse(os.path.exists(os.path.join(self.out, 'exxpiano.img')))
        self.assertFalse(os.path.exists(os.path.join(self.out, 'sticker')))
        with open(os.path.join(self.out, 'README.txt'), encoding='utf-8') as f:
            text = f.read()
        for needle in ('creates and permanently saves its own 16-character hexadecimal ID', 'exxpiano-Setup-<INSTALLATION-ID>', 'setup password 1',
                       'needs neither Internet nor an Ethernet cable', 'DietPi: 10.7.0'):
            self.assertIn(needle, text)
        with open(os.path.join(self.out, 'manifest.json'), encoding='utf-8') as f:
            manifest = json.load(f)
        self.assertEqual((manifest['product_version'], manifest['dietpi_version'], manifest['installation_identity'], manifest['image']),
                         (version, '10.7.0', 'generated-at-first-boot', stem + '-sd.zip'))
        self.assertNotIn('serial', manifest)
        self.assertNotIn('device_number', manifest)
        self.assertRegex(manifest['dietpi_sha256'], r'^[0-9a-f]{64}$')
        self.assertRegex(manifest['image_sha256'], r'^[0-9a-f]{64}$')
    def test_the_container_gets_read_only_inputs_a_writable_output_and_the_passwords_in_the_environment(self):
        self.run_build('--password', 'root password 1', '--ap-password', 'setup password 1')
        calls = [c for c in self.docker_calls() if c['args'][:1] == ['run']]
        build = [c for c in calls if 'sd-image-inner.sh' in ' '.join(c['args'])][0]
        args = build['args']
        self.assertIn('--privileged', args)
        mounts = [args[i + 1] for i, a in enumerate(args) if a == '-v']
        self.assertTrue(any(m.endswith(':/repo:ro') for m in mounts))
        self.assertTrue(any(m.endswith(':/out') for m in mounts))
        inputs = [m for m in mounts if m.endswith(':ro') and '/in/' in m]
        self.assertEqual(len(inputs), 4)                                                     # the image, the package, both banks
        self.assertEqual(args[args.index('--hostname') + 1], 'exxpiano')
        self.assertNotIn('--serial', args)
        self.assertNotIn('--number', args)
        self.assertNotIn('root password 1', ' '.join(args))                                  # no password in the arguments ...
        self.assertNotIn('setup password 1', ' '.join(args))
        self.assertEqual(build['env'], {'SD_PASSWORD': 'root password 1', 'SD_AP_PASSWORD': 'setup password 1'})

    def test_the_finished_image_is_run_in_a_container_too(self):
        self.run_build()
        smoke = [c for c in self.docker_calls() if 'sd-smoke-test.sh' in ' '.join(c['args'])]
        self.assertEqual(len(smoke), 1)
        self.assertIn('/out/exxpiano.img', smoke[0]['args'])
        self.docker_calls()
        r = self.run_build('--no-smoke')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(len([c for c in self.docker_calls() if 'sd-smoke-test.sh' in ' '.join(c['args'])]), 1)    # still only the first one

    def test_a_failed_build_says_so(self):
        r = self.run_build(env=dict(FAKE_DOCKER_FAIL_BUILD='1'))
        self.assertEqual(r.returncode, 1)
        self.assertIn('could not be made', r.stderr)
        r = self.run_build(env=dict(FAKE_DOCKER_FAIL_SMOKE='1'))
        self.assertEqual(r.returncode, 1)
        self.assertIn('smoke test', r.stderr)

    def test_docker_that_is_not_running_is_explained(self):
        r = self.run_build(env=dict(FAKE_DOCKER_INFO='1'))
        self.assertEqual(r.returncode, 1)
        self.assertIn('Docker is not running', r.stderr)


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

    def test_the_public_downloader_identifies_the_builder(self):
        calls = []

        def urlopen(request, timeout):
            calls.append((request, timeout))
            return self.Response(b'data')

        old = bsi.urllib.request.urlopen
        bsi.urllib.request.urlopen = urlopen
        self.addCleanup(setattr, bsi.urllib.request, 'urlopen', old)
        with bsi.fetch_url('https://example.invalid/image.img.xz') as response:
            self.assertEqual(response.read(), b'data')
        self.assertEqual(len(calls), 1)
        request, timeout = calls[0]
        self.assertEqual(request.full_url, 'https://example.invalid/image.img.xz')
        self.assertEqual(request.get_header('User-agent'), bsi.HTTP_USER_AGENT)
        self.assertEqual(timeout, 60)


class ReleaseWorkflowTests(unittest.TestCase):
    def test_release_uses_the_pinned_github_dietpi_asset(self):
        metadata = {}
        with open(os.path.join(ROOT, '.github', 'dietpi-image.env')) as f:
            for line in f:
                key, value = line.rstrip('\n').split('=', 1)
                metadata[key] = value
        self.assertEqual(metadata['DIETPI_IMAGE'], 'DietPi_RPi234-ARMv8-Trixie.img.xz')
        self.assertRegex(metadata['DIETPI_SHA256'], r'^[0-9a-f]{64}$')
        self.assertTrue(metadata['DIETPI_RELEASE_TAG'].endswith(metadata['DIETPI_SHA256'][:8]))
        with open(os.path.join(ROOT, '.github', 'workflows', 'release.yml')) as f:
            workflow = f.read()
        self.assertIn('gh release download "$DIETPI_RELEASE_TAG"', workflow)
        self.assertIn('sha256sum -c -', workflow)
        self.assertIn('--dietpi "build/cache/$DIETPI_IMAGE"', workflow)
        self.assertNotIn('build-sd-image.py --download', workflow)


class ConsoleScreenTests(unittest.TestCase):
    """The HDMI console shows a status screen: nothing is asked there, and no password or login prompt appears."""

    def screen(self, ids=None, ips='192.168.1.95'):
        with tempfile.TemporaryDirectory() as d:
            id_path = os.path.join(d, 'installation-id')
            if ids:
                with open(id_path, 'w') as f:
                    f.write(ids + '\n')
            env = dict(os.environ, EXXPIANO_CONSOLE_ONCE='1', EXXPIANO_INSTALLATION_ID_PATH=id_path, EXXPIANO_IPS=ips)
            r = subprocess.run(['bash', os.path.join(ROOT, 'dietpi', 'exxpiano-console.sh')], capture_output=True, text=True, env=env, timeout=10)
        self.assertEqual((r.returncode, r.stderr), (0, ''))
        return r.stdout

    def test_it_shows_the_id_the_addresses_and_that_nothing_is_asked(self):
        out = self.screen('0123456789ABCDEF', '192.168.1.95 fe80::1')
        for needle in ('0123456789ABCDEF', 'http://192.168.1.95:8080/', 'http://fe80::1:8080/', '.local:8080/', 'no login on this screen', 'ssh root@'):
            self.assertIn(needle, out)
        self.assertNotIn('login:', out.lower())
        self.assertNotIn('password', out.lower())                       # no prompt, and no secret on a screen anybody can see

    def test_without_a_network_it_names_the_setup_wifi_of_this_installation(self):
        out = self.screen('0123456789ABCDEF', '')
        self.assertIn('exxpiano-Setup-0123456789ABCDEF', out)
        self.assertIn('http://10.42.0.1:8080/', out)
        self.assertNotIn('http://:8080', out)

    def test_before_the_id_exists_it_says_so(self):
        self.assertIn('is being created', self.screen(None, ''))

    def test_the_unit_replaces_the_login_prompt_and_nothing_re_enables_autologin(self):
        with open(os.path.join(ROOT, 'dietpi', 'getty-tty1-exxpiano.conf')) as f:
            unit = f.read()
        self.assertIn('ExecStart=\nExecStart=/usr/local/sbin/exxpiano-console', unit)       # the empty line clears the agetty of getty@.service
        active = [l for l in unit.splitlines() if l.strip() and not l.lstrip().startswith('#')]            # the comment may mention agetty
        self.assertFalse([l for l in active if 'agetty' in l or 'autologin' in l.lower()])
        with open(os.path.join(ROOT, 'dietpi', 'exxpiano-dietpi-preinstalled.sh')) as f:
            self.assertIn('dietpi-autologin.conf', f.read())                                   # the autologin DietPi creates for its online setup is removed


class ConfQuotingTests(unittest.TestCase):
    def test_a_tricky_password_comes_back_unchanged_through_the_programs_parser(self):
        sys.path.insert(0, os.path.join(ROOT, 'pi'))
        import exxpianoconf
        src = open(os.path.join(ROOT, 'scripts', 'sd-image-inner.sh')).read()
        start = src.index('set_kv() {')
        end = src.index('\n}\n', start) + 3
        qstart = src.index('quote_conf() {')
        qend = src.index('\n}\n', qstart) + 3
        for password in ('plain password', 'a"b\\c d#e$f', "it's; 100% `ok`", '\\\\\\', 'x"'):
            with self.subTest(password), tempfile.TemporaryDirectory() as d:
                f = os.path.join(d, 'exxpiano.conf')
                with open(f, 'w') as fh:
                    fh.write('GAIN=1.0\n# WIFI_AP_PASSWORD=exxpianosetup\n')
                script = src[start:end] + src[qstart:qend] + '\nset_kv "$F" WIFI_AP_PASSWORD "$(quote_conf "$P")"\n'
                subprocess.run(['bash', '-c', script], env=dict(os.environ, F=f, P=password), check=True)
                self.assertEqual(exxpianoconf.parse_conf(f)['WIFI_AP_PASSWORD'], password)
                self.assertEqual(exxpianoconf.parse_conf(f)['GAIN'], '1.0')


class ShellScriptTests(unittest.TestCase):
    def test_product_image_first_boot_is_offline_and_leaves_wifi_to_exxpiano(self):
        with open(os.path.join(ROOT, 'scripts', 'sd-image-inner.sh')) as f:
            src = f.read()
        set_start = src.index('set_kv() {')
        set_end = src.index('\n}\n', set_start) + 3
        setup_start = src.index('configure_offline_product_boot() {')
        setup_end = src.index('\n}\n', setup_start) + 3
        with tempfile.TemporaryDirectory() as d:
            root = os.path.join(d, 'root')
            bootdir = '/boot/firmware'
            os.makedirs(root + bootdir)
            dietpi_txt = root + bootdir + '/dietpi.txt'
            with open(dietpi_txt, 'w') as f:
                f.write('AUTO_SETUP_NET_WIFI_ENABLED=1\nAUTO_SETUP_BOOT_WAIT_FOR_NETWORK=1\n'
                        'CONFIG_CHECK_DIETPI_UPDATES=1\nCONFIG_CHECK_APT_UPDATES=1\n')
            script = src[set_start:set_end] + src[setup_start:setup_end] + '\nconfigure_offline_product_boot "$ROOT" "$BOOTDIR"\n'
            r = subprocess.run(['bash', '-c', script], capture_output=True, text=True,
                               env=dict(os.environ, ROOT=root, BOOTDIR=bootdir, HERE=os.path.join(ROOT, 'scripts')))
            self.assertEqual(r.returncode, 0, r.stderr)
            with open(dietpi_txt) as f:
                settings = f.read()
            self.assertIn('AUTO_SETUP_NET_WIFI_ENABLED=0\n', settings)
            self.assertIn('AUTO_SETUP_BOOT_WAIT_FOR_NETWORK=0\n', settings)
            self.assertIn('CONFIG_CHECK_DIETPI_UPDATES=0\n', settings)
            self.assertIn('CONFIG_CHECK_APT_UPDATES=0\n', settings)
            dropin = os.path.join(root, 'etc/systemd/system/dietpi-firstboot.service.d/exxpiano-preinstalled.conf')
            with open(dropin) as f:
                self.assertIn('ExecStartPost=/usr/local/sbin/exxpiano-dietpi-preinstalled', f.read())
            self.assertTrue(os.access(os.path.join(root, 'usr/local/sbin/exxpiano-dietpi-preinstalled'), os.X_OK))
            self.assertTrue(os.access(os.path.join(root, 'var/lib/dietpi/postboot.d/10-exxpiano-image-firstboot'), os.X_OK))
            self.assertTrue(os.access(os.path.join(root, 'usr/local/sbin/exxpiano-console'), os.X_OK))
            with open(os.path.join(root, 'etc/systemd/system/getty@tty1.service.d/exxpiano-console.conf')) as f:
                self.assertIn('ExecStart=/usr/local/sbin/exxpiano-console', f.read())

    def test_the_offline_chroot_gets_the_runtime_tmpfs_permissions(self):
        with open(os.path.join(ROOT, 'scripts', 'sd-image-inner.sh')) as f:
            src = f.read()
        start = src.index('prepare_chroot_tmp() {')
        end = src.index('\n}\n', start) + 3
        with tempfile.TemporaryDirectory() as d:
            tmp = os.path.join(d, 'tmp')
            os.mkdir(tmp, 0o755)
            script = src[start:end] + '\nprepare_chroot_tmp "$ROOT"\n'
            r = subprocess.run(['bash', '-c', script], capture_output=True, text=True, env=dict(os.environ, ROOT=d))
            mode = stat.S_IMODE(os.stat(tmp).st_mode)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(mode, 0o1777)

    def test_dietpi_version_is_read_from_root_boot_when_firmware_is_a_separate_partition(self):
        with open(os.path.join(ROOT, 'scripts', 'sd-image-inner.sh')) as f:
            src = f.read()
        start = src.index('read_dietpi_version() {')
        end = src.index('\n}\n', start) + 3
        with tempfile.TemporaryDirectory() as d:
            version = os.path.join(d, 'boot', 'dietpi', '.version')
            os.makedirs(os.path.dirname(version))
            with open(version, 'w') as f:
                f.write('G_DIETPI_VERSION_CORE=10\nG_DIETPI_VERSION_SUB=7\nG_DIETPI_VERSION_RC=2\n')
            os.makedirs(os.path.join(d, 'boot', 'firmware'))
            script = 'die() { echo "sd-image: $*" >&2; exit 1; }\n' + src[start:end] + '\nread_dietpi_version "$ROOT" /boot/firmware\n'
            r = subprocess.run(['bash', '-c', script], capture_output=True, text=True, env=dict(os.environ, ROOT=d))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), '10.7.2')

    def test_the_scripts_have_valid_syntax_and_the_key_value_helper_behaves(self):
        for name in ('sd-image-inner.sh', 'sd-fake-dietpi.sh', 'sd-smoke-test.sh'):
            r = subprocess.run(['bash', '-n', os.path.join(ROOT, 'scripts', name)], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, name + r.stderr)
        for name in ('Automation_Custom_Script.image.sh', 'exxpiano-dietpi-preinstalled.sh', 'exxpiano-console.sh'):
            r = subprocess.run(['bash', '-n', os.path.join(ROOT, 'dietpi', name)], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, name + r.stderr)
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

    def test_inner_builder_rejects_a_missing_base_or_bank_before_mutation(self):
        with tempfile.TemporaryDirectory() as d:
            fake_bin = os.path.join(d, 'bin')
            os.makedirs(fake_bin)
            fake_id = os.path.join(fake_bin, 'id')
            with open(fake_id, 'w') as f:
                f.write('#!/bin/sh\necho 0\n')
            os.chmod(fake_id, 0o755)
            deb = os.path.join(d, 'app.deb')
            piano = os.path.join(d, 'piano.sf2')
            mech = os.path.join(d, 'mech.sf2')
            for path in (deb, piano, mech):
                with open(path, 'wb') as f:
                    f.write(b'x')
            base = ['bash', os.path.join(ROOT, 'scripts', 'sd-image-inner.sh'), '--out', os.path.join(d, 'out.img'),
                    '--dietpi-version-out', os.path.join(d, 'version'), '--deb', deb, '--piano', piano, '--mech', mech]
            env = dict(os.environ, PATH=fake_bin + os.pathsep + os.environ['PATH'])
            r = subprocess.run(base + ['--image', os.path.join(d, 'missing.img')], capture_output=True, text=True, env=env)
            self.assertEqual(r.returncode, 1)
            self.assertIn('no such image', r.stderr)
            image = os.path.join(d, 'base.img')
            with open(image, 'wb') as f:
                f.write(b'x')
            r = subprocess.run([x if x != piano else os.path.join(d, 'missing.sf2') for x in base] + ['--image', image],
                               capture_output=True, text=True, env=env)
            self.assertEqual(r.returncode, 1)
            self.assertIn('--piano is required', r.stderr)

    def test_smoke_layout_check_rejects_every_missing_required_component(self):
        script = os.path.join(ROOT, 'scripts', 'sd-smoke-test.sh')

        def tree(base):
            root, boot = os.path.join(base, 'root'), os.path.join(base, 'boot')
            files = {
                os.path.join(boot, 'dietpi.txt'): ('AUTO_SETUP_AUTOMATED=1\nAUTO_SETUP_NET_WIFI_ENABLED=0\n'
                                                      'AUTO_SETUP_BOOT_WAIT_FOR_NETWORK=0\nCONFIG_CHECK_DIETPI_UPDATES=0\n'
                                                      'CONFIG_CHECK_APT_UPDATES=0\n'),
                os.path.join(root, 'boot', 'dietpi', '.version'): 'G_DIETPI_VERSION_CORE=10\n',
                os.path.join(boot, 'Automation_Custom_Script.sh'): '#!/bin/sh\n',
                os.path.join(root, 'usr', 'local', 'sbin', 'exxpiano-dietpi-preinstalled'): '#!/bin/sh\n',
                os.path.join(root, 'etc', 'systemd', 'system', 'dietpi-firstboot.service.d', 'exxpiano-preinstalled.conf'):
                    '[Service]\nExecStartPost=/usr/local/sbin/exxpiano-dietpi-preinstalled\n',
                os.path.join(root, 'var', 'lib', 'dietpi', 'postboot.d', '10-exxpiano-image-firstboot'): '#!/bin/sh\n',
                os.path.join(root, 'usr', 'local', 'sbin', 'exxpiano-console'): '#!/bin/sh\n',
                os.path.join(root, 'etc', 'systemd', 'system', 'getty@tty1.service.d', 'exxpiano-console.conf'): '[Service]\nExecStart=\nExecStart=/usr/local/sbin/exxpiano-console\n',
                os.path.join(root, 'opt', 'exxpiano', 'exxpiano-web.py'): '#!/usr/bin/python3\n',
                os.path.join(root, 'opt', 'exxpiano', 'exxpiano-wifi.py'): '#!/usr/bin/python3\n',
                os.path.join(root, 'lib', 'systemd', 'system', 'exxpiano.service'): '[Service]\n',
                os.path.join(root, 'opt', 'exxpiano', 'soundfonts', 'piano.sf2'): 'piano',
                os.path.join(root, 'opt', 'exxpiano', 'soundfonts', 'mech.sf2'): 'mech',
                os.path.join(root, 'etc', 'exxpiano.conf'): ('PIANO_SF2=/opt/exxpiano/soundfonts/piano.sf2\n'
                                                                  'MECH_SF2=/opt/exxpiano/soundfonts/mech.sf2\n'),
            }
            for path, content in files.items():
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, 'w') as f:
                    f.write(content)
                if path.endswith(('.sh', '.py', 'exxpiano-dietpi-preinstalled', '10-exxpiano-image-firstboot', 'exxpiano-console')):
                    os.chmod(path, 0o755)
            return root, boot, files

        with tempfile.TemporaryDirectory() as d:
            root, boot, _ = tree(d)
            self.assertEqual(subprocess.run(['bash', script, '--check-tree', root, boot], capture_output=True).returncode, 0)
        for good, bad in (('AUTO_SETUP_NET_WIFI_ENABLED=0', 'AUTO_SETUP_NET_WIFI_ENABLED=1'),
                          ('AUTO_SETUP_BOOT_WAIT_FOR_NETWORK=0', 'AUTO_SETUP_BOOT_WAIT_FOR_NETWORK=1'),
                          ('CONFIG_CHECK_DIETPI_UPDATES=0', 'CONFIG_CHECK_DIETPI_UPDATES=1'),
                          ('CONFIG_CHECK_APT_UPDATES=0', 'CONFIG_CHECK_APT_UPDATES=1')):
            with self.subTest(bad), tempfile.TemporaryDirectory() as d:
                root, boot, _ = tree(d)
                path = os.path.join(boot, 'dietpi.txt')
                with open(path) as f:
                    text = f.read()
                with open(path, 'w') as f:
                    f.write(text.replace(good, bad))
                self.assertNotEqual(subprocess.run(['bash', script, '--check-tree', root, boot], capture_output=True).returncode, 0)
        for rel in ('boot/dietpi.txt', 'root/boot/dietpi/.version', 'boot/Automation_Custom_Script.sh',
                    'root/usr/local/sbin/exxpiano-dietpi-preinstalled',
                    'root/etc/systemd/system/dietpi-firstboot.service.d/exxpiano-preinstalled.conf',
                    'root/var/lib/dietpi/postboot.d/10-exxpiano-image-firstboot',
                    'root/usr/local/sbin/exxpiano-console', 'root/etc/systemd/system/getty@tty1.service.d/exxpiano-console.conf',
                    'root/opt/exxpiano/exxpiano-web.py', 'root/lib/systemd/system/exxpiano.service',
                    'root/opt/exxpiano/soundfonts/piano.sf2', 'root/opt/exxpiano/soundfonts/mech.sf2',
                    'root/etc/exxpiano.conf'):
            with self.subTest(rel), tempfile.TemporaryDirectory() as d:
                root, boot, _ = tree(d)
                os.unlink(os.path.join(d, rel))
                self.assertNotEqual(subprocess.run(['bash', script, '--check-tree', root, boot], capture_output=True).returncode, 0)


if __name__ == '__main__':
    unittest.main()
