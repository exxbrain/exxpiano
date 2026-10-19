"""Fast tests for the reusable SD image builder (scripts/build-sd-image.py, scripts/sd-image-inner.sh) with a fake Docker executable.

The real image is built and read back by scripts/test-sd-image-pipeline.sh (Docker, minutes).
"""

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
SCRIPT = os.path.join(ROOT, 'scripts', 'build-sd-image.py')
INNER = os.path.join(ROOT, 'scripts', 'sd-image-inner.sh')
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
if args[:1] == ['run'] and 'sd-image-inner.sh' in ' '.join(args):
    if os.environ.get('FAKE_DOCKER_FAIL_BUILD'):
        sys.exit(1)
    host = [a.split(':')[0] for a in args if a.endswith(':/out')][0]
    with open(os.path.join(host, os.path.basename(args[args.index('--out') + 1])), 'wb') as f:
        f.write(b'fake image ' * 1000)
    info = os.path.join(host, os.path.basename(args[args.index('--info-out') + 1]))
    os.makedirs(info, exist_ok=True)
    open(os.path.join(info, 'alpine-version.txt'), 'w').write(os.environ.get('FAKE_ALPINE', '3.24.2') + '\n')
    open(os.path.join(info, 'packages.txt'), 'w').write('alpine-base-3.24.2-r0\nexxpiano-1.0-r0\nfluidsynth-2.5.4-r0\n')
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
        self.repo = os.path.join(t, 'apk')
        self.touch('apk/exxpiano/aarch64/APKINDEX.tar.gz', b'index')
        self.touch('apk/exxpiano/aarch64/exxpiano-1.0-r0.apk', b'package')
        self.touch('apk/exxpiano-dev.rsa.pub', b'-----BEGIN PUBLIC KEY-----\n')
        self.piano = self.touch('banks/piano.sf2', b'p' * 10)
        self.mech = self.touch('banks/mech.sf2', b'm' * 10)
        self.env = dict(os.environ, PATH=self.bin + os.pathsep + os.environ['PATH'], FAKE_DOCKER_LOG=self.log)
        self.env.pop('SD_PASSWORD', None)
        self.env.pop('SD_AP_PASSWORD', None)

    def touch(self, rel, data):
        path = os.path.join(self.tmp.name, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'wb') as f:
            f.write(data)
        return path

    def run_build(self, *extra, env=None, banks=True):
        args = ['--apk-repo', self.repo, '--out', self.out]
        if banks:
            args += ['--piano', self.piano, '--mech', self.mech]
        return subprocess.run([sys.executable, SCRIPT] + args + list(extra), capture_output=True, text=True, env=dict(self.env, **(env or {})))

    def docker_calls(self):
        try:
            with open(self.log) as f:
                return [json.loads(l) for l in f]
        except FileNotFoundError:
            return []

    def inner_call(self):
        return next(c for c in self.docker_calls() if c['args'][:1] == ['run'])


class PlanTests(Case):
    def test_a_dry_run_checks_and_makes_nothing(self):
        r = self.run_build('--dry-run')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('installation ID, hostname and setup Wi-Fi are generated automatically', r.stdout)
        self.assertIn('signed with exxpiano-dev.rsa.pub', r.stdout)
        self.assertIn('dry run', r.stdout)
        self.assertFalse(os.path.exists(self.out))
        self.assertEqual(self.docker_calls(), [])

    def test_missing_inputs_are_named(self):
        r = subprocess.run(
            [sys.executable, SCRIPT, '--apk-repo', os.path.join(self.tmp.name, 'none'), '--piano', self.piano, '--mech', self.mech, '--dry-run'],
            capture_output=True,
            text=True,
            env=self.env,
        )
        self.assertEqual(r.returncode, 1)
        self.assertIn('build-apks.py', r.stderr)
        r = self.run_build('--dry-run', '--piano', os.path.join(self.tmp.name, 'nope.sf2'))
        self.assertIn('required piano bank', r.stderr)
        self.touch('apk/second.rsa.pub', b'x')
        r = self.run_build('--dry-run')
        self.assertIn('exactly one public key', r.stderr)

    def test_bad_values_are_refused_before_anything_starts(self):
        for extra, text in (
            (('--password', 'short'), 'at least 8'),
            (('--ap-password', 'short'), '8 to 63'),
            (('--country', 'Russia'), 'two letters'),
            (('--timezone', '../etc/passwd'), 'zone name'),
        ):
            with self.subTest(extra):
                r = self.run_build('--dry-run', *extra)
                self.assertEqual(r.returncode, 1)
                self.assertIn(text, r.stderr)
        self.assertEqual(self.docker_calls(), [])

    def test_the_old_interface_is_gone(self):
        for args in (('--dietpi', 'x.img'), ('--target', 'rpi5'), ('--download',), ('--no-banks',), ('--serial', '3c1c783e')):
            r = self.run_build(*args, '--dry-run')
            self.assertEqual(r.returncode, 2, args)
            self.assertIn('unrecognized arguments', r.stderr)

    def test_default_banks_and_explicit_overrides_are_resolved_before_planning(self):
        old_root = bsi.ROOT
        try:
            bsi.ROOT = self.tmp.name
            default_piano = self.touch('build/piano-full.sf2', b'default piano')
            default_mech = self.touch('build/mech.sf2', b'default mech')
            args = bsi.parse_args(['--apk-repo', self.repo])
            self.assertEqual(bsi.resolve_inputs(args), dict(apk_repo=self.repo, piano=default_piano, mech=default_mech))
            lite = self.touch('build/piano-lite.sf2', b'default lite')  # the smaller bank is taken when it exists, it is not required
            self.assertEqual(bsi.resolve_inputs(bsi.parse_args(['--apk-repo', self.repo]))['piano_lite'], lite)
            os.unlink(lite)
            args = bsi.parse_args(['--apk-repo', self.repo, '--piano-lite', os.path.join(self.tmp.name, 'nothing.sf2')])
            with self.assertRaisesRegex(bsi.BuildError, 'required piano_lite bank'):
                bsi.resolve_inputs(args)
            self.touch('apk/exxpiano/aarch64/x', b'')
            os.makedirs(os.path.join(self.tmp.name, 'dist'), exist_ok=True)
            os.symlink(self.repo, os.path.join(self.tmp.name, 'dist', 'apk'))
            self.assertEqual(bsi.resolve_inputs(bsi.parse_args([]))['apk_repo'], os.path.join(self.tmp.name, 'dist', 'apk'))  # the default
        finally:
            bsi.ROOT = old_root

    def test_each_missing_default_bank_stops_before_building(self):
        old_root = bsi.ROOT
        try:
            bsi.ROOT = self.tmp.name
            args = bsi.parse_args(['--apk-repo', self.repo])
            for present, missing in (('mech.sf2', 'piano'), ('piano-full.sf2', 'mech')):
                build = os.path.join(self.tmp.name, 'build')
                os.makedirs(build, exist_ok=True)
                for name in ('piano-full.sf2', 'mech.sf2'):
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
    def test_a_build_makes_one_reusable_zip_readme_and_manifest(self):
        r = self.run_build('--country', 'ru', '--timezone', 'Europe/Moscow')
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        with open(os.path.join(ROOT, 'VERSION')) as f:
            version = f.read().strip()
        stem = 'exxpiano-rpi-v%s-alpine-3.24.2' % version
        self.assertEqual(sorted(os.listdir(self.out)), sorted([stem + '-sd.zip', 'README.txt', 'manifest.json']))
        with zipfile.ZipFile(os.path.join(self.out, stem + '-sd.zip')) as z:
            self.assertEqual(z.namelist(), [stem + '.img'])
        with open(os.path.join(self.out, 'manifest.json')) as f:
            m = json.load(f)
        self.assertEqual((m['schema_version'], m['target_family'], m['alpine_version'], m['alpine_branch']), (3, 'rpi', '3.24.2', 'v3.24'))
        self.assertEqual(m['boards'], ['Raspberry Pi 3', 'Raspberry Pi 4', 'Raspberry Pi 5'])
        self.assertIn('fluidsynth-2.5.4-r0', m['packages'])
        self.assertEqual(m['package_signing_key'], 'exxpiano-dev.rsa.pub')
        self.assertEqual(len(m['alpine_keys_sha256']), 2)
        self.assertEqual(m['installation_identity'], 'generated-at-first-boot')
        self.assertNotIn('dietpi', json.dumps(m).lower())
        with open(os.path.join(self.out, 'README.txt')) as f:
            text = f.read()
        self.assertIn('Alpine Linux: 3.24.2', text)
        self.assertIn('authorized_keys on the boot partition', text)  # no password given: key login only
        args = self.inner_call()['args']
        self.assertEqual(args[args.index('--country') + 1], 'RU')
        self.assertEqual(args[args.index('--timezone') + 1], 'Europe/Moscow')

    def test_the_passwords_go_through_the_environment_never_the_arguments(self):
        r = self.run_build('--password', 'root-secret-1', '--ap-password', 'ap-secret-22')
        self.assertEqual(r.returncode, 0, r.stderr)
        call = self.inner_call()
        self.assertFalse([a for a in call['args'] if 'secret' in a])
        self.assertEqual(call['env'], dict(SD_PASSWORD='root-secret-1', SD_AP_PASSWORD='ap-secret-22'))
        self.assertIn('-e', call['args'])
        self.assertNotIn('secret', r.stdout + r.stderr)
        r = self.run_build(env=dict(SD_PASSWORD='from-the-env-1'))
        self.assertEqual(self.inner_call()['env']['SD_PASSWORD'], 'root-secret-1')  # the first build of this log; the second:
        self.assertEqual([c for c in self.docker_calls() if c['args'][:1] == ['run']][1]['env']['SD_PASSWORD'], 'from-the-env-1')

    def test_a_custom_setup_password_stays_out_of_the_published_files(self):
        r = self.run_build('--ap-password', 'my-secret-setup-77', '--password', 'root-secret-88')
        self.assertEqual(r.returncode, 0, r.stderr)
        for name in os.listdir(self.out):
            if name.endswith(('.txt', '.json')):
                with open(os.path.join(self.out, name), encoding='utf-8') as f:
                    text = f.read()
                self.assertNotIn('my-secret-setup-77', text, name)
                self.assertNotIn('root-secret-88', text, name)
        with open(os.path.join(self.out, 'README.txt'), encoding='utf-8') as f:
            readme = f.read()
        self.assertIn('the one given with --ap-password at build time', readme)
        self.assertIn('with the password given when the image was built', readme)
        plan = bsi.plan_image(
            bsi.parse_args(['--apk-repo', self.repo, '--ap-password', 'my-secret-setup-77']), dict(apk_repo=self.repo, piano=self.piano, mech=self.mech)
        )
        self.assertNotIn('my-secret-setup-77', json.dumps(plan))  # the plan that feeds the README holds no password

    def test_the_container_gets_read_only_inputs_and_a_writable_output(self):
        r = self.run_build('--ssh-key', self.touch('me.pub', b'ssh-ed25519 AAAA me'))
        self.assertEqual(r.returncode, 0, r.stderr)
        args = self.inner_call()['args']
        volumes = [args[i + 1] for i, a in enumerate(args) if a == '-v']
        self.assertIn('%s:/out' % self.out, volumes)
        self.assertIn('%s:/repo:ro' % ROOT, volumes)
        self.assertIn('%s:/in/apk_repo:ro' % self.repo, volumes)
        self.assertEqual([v for v in volumes if not v.endswith(':ro')], ['%s:/out' % self.out, '%s:/cache' % bsi.APK_CACHE])  # and the downloads
        self.assertEqual(args[args.index('--cache-dir') + 1], '/cache')
        self.assertNotIn('--privileged', args)  # files, not loop devices
        self.assertEqual(args[args.index('--apk-repo') + 1], '/in/apk_repo')
        self.assertTrue(args[args.index('--ssh-key') + 1].endswith('/me.pub'))
        self.assertEqual(args[args.index('--platform') + 1], 'linux/arm64')
        self.assertNotIn('--mirror', args)  # the pinned mirror unless one is asked for

    def test_another_mirror_is_passed_on_and_recorded(self):
        r = self.run_build(env=dict(EXXPIANO_ALPINE_MIRROR='https://mirrors.dotsrc.org/alpine/'))
        self.assertEqual(r.returncode, 0, r.stderr)
        args = self.inner_call()['args']
        self.assertEqual(args[args.index('--mirror') + 1], 'https://mirrors.dotsrc.org/alpine')
        with open(os.path.join(self.out, 'manifest.json')) as f:
            m = json.load(f)
        self.assertEqual((m['alpine_mirror'], m['alpine_pinned_mirror']), ('https://mirrors.dotsrc.org/alpine', 'https://dl-cdn.alpinelinux.org/alpine'))
        r = self.run_build('--dry-run', '--mirror', 'http://insecure.example/alpine')
        self.assertEqual(r.returncode, 1)
        self.assertIn('https://', r.stderr)

    def test_a_failed_build_says_so_and_leaves_no_zip(self):
        r = self.run_build(env=dict(FAKE_DOCKER_FAIL_BUILD='1'))
        self.assertEqual(r.returncode, 1)
        self.assertIn('could not be made', r.stderr)
        self.assertFalse([n for n in os.listdir(self.out) if n.endswith('.zip')])

    def test_an_unexpected_alpine_version_stops_the_release(self):
        r = self.run_build(env=dict(FAKE_ALPINE='3.25.0'))
        self.assertEqual(r.returncode, 1)
        self.assertIn('not 3.24', r.stderr)

    def test_docker_that_is_not_running_is_explained(self):
        r = self.run_build(env=dict(FAKE_DOCKER_INFO='1'))
        self.assertEqual(r.returncode, 1)
        self.assertIn('Docker is not running', r.stderr)


class ReleaseWorkflowTests(unittest.TestCase):
    def test_the_release_builds_one_signed_alpine_image(self):
        with open(os.path.join(ROOT, '.github', 'workflows', 'release.yml')) as f:
            text = f.read()
        self.assertIn('runs-on: ubuntu-24.04-arm', text)
        self.assertIn('secrets.EXXPIANO_APK_KEY', text)
        self.assertIn('test -n "$EXXPIANO_APK_KEY"', text)
        self.assertIn('scripts/build-apks.py --out dist/apk', text)
        self.assertIn('scripts/build-sd-image.py --apk-repo dist/apk', text)
        self.assertNotIn('dietpi', text.lower())
        self.assertFalse(os.path.exists(os.path.join(ROOT, '.github', 'workflows', 'dietpi-mirror.yml')))
        self.assertFalse(os.path.exists(os.path.join(ROOT, '.github', 'dietpi-image.env')))


class ConsoleScreenTests(unittest.TestCase):
    """The HDMI console shows a status screen: nothing is asked there, and no password or login prompt appears."""

    def screen(self, ids=None, ips='192.168.1.95'):
        with tempfile.TemporaryDirectory() as d:
            id_path = os.path.join(d, 'installation-id')
            if ids:
                with open(id_path, 'w') as f:
                    f.write(ids + '\n')
            env = dict(os.environ, EXXPIANO_CONSOLE_ONCE='1', EXXPIANO_INSTALLATION_ID_PATH=id_path, EXXPIANO_IPS=ips)
            r = subprocess.run(['bash', os.path.join(ROOT, 'pi', 'exxpiano-console.sh')], capture_output=True, text=True, env=env, timeout=10)
        self.assertEqual((r.returncode, r.stderr), (0, ''))
        return r.stdout

    def test_it_shows_the_id_the_addresses_and_that_nothing_is_asked(self):
        out = self.screen('0123456789ABCDEF', '192.168.1.95 fe80::1')
        for needle in ('0123456789ABCDEF', 'http://192.168.1.95:8080/', 'http://fe80::1:8080/', '.local:8080/', 'no login on this screen', 'ssh root@'):
            self.assertIn(needle, out)
        self.assertNotIn('login:', out.lower())
        self.assertNotIn('password', out.lower())  # no prompt, and no secret on a screen anybody can see

    def test_without_a_network_it_names_the_setup_wifi_of_this_installation(self):
        out = self.screen('0123456789ABCDEF', '')
        self.assertIn('exxpiano-Setup-0123456789ABCDEF', out)
        self.assertIn('http://10.42.0.1:8080/', out)
        self.assertNotIn('http://:8080', out)

    def test_before_the_id_exists_it_says_so(self):
        self.assertIn('is being created', self.screen(None, ''))

    def test_the_addresses_come_from_ip_not_from_hostname(self):
        with open(os.path.join(ROOT, 'pi', 'exxpiano-console.sh')) as f:
            text = f.read()
        self.assertNotIn('hostname -I', text)  # busybox hostname has no -I
        self.assertIn('ip -4 -o addr show scope global', text)


class InnerScriptTests(unittest.TestCase):
    def setUp(self):
        with open(INNER, encoding='utf-8') as f:
            self.src = f.read()

    def function(self, name):
        start = self.src.index(name + '() {')
        return self.src[start : self.src.index('\n}\n', start) + 3]

    def test_it_parses(self):
        self.assertEqual(subprocess.run(['bash', '-n', INNER]).returncode, 0)

    def test_a_tricky_password_comes_back_unchanged_through_the_programs_parser(self):
        sys.path.insert(0, os.path.join(ROOT, 'pi'))
        import exxpianoconf

        for password in ('plain password', 'a"b\\c d#e$f', "it's; 100% `ok`", '\\\\\\', 'x"'):
            with self.subTest(password), tempfile.TemporaryDirectory() as d:
                os.makedirs(os.path.join(d, 'etc'))
                f = os.path.join(d, 'etc', 'exxpiano.conf')
                with open(f, 'w') as fh:
                    fh.write('GAIN=1.0\n# WIFI_AP_PASSWORD=exxpianosetup\n')
                script = (
                    self.function('set_conf') + self.function('quote_conf') + '\nset_conf WIFI_AP_PASSWORD "$(quote_conf "$P")"\nset_conf WIFI_COUNTRY RU\n'
                )
                subprocess.run(['bash', '-c', script], env=dict(os.environ, ROOTFS=d, P=password), check=True)
                conf = exxpianoconf.parse_conf(f)
                self.assertEqual(conf['WIFI_AP_PASSWORD'], password)
                self.assertEqual((conf['GAIN'], conf['WIFI_COUNTRY']), ('1.0', 'RU'))
                with open(f) as fh:
                    self.assertEqual(fh.read().count('WIFI_AP_PASSWORD='), 1)  # the commented default became the setting

    def test_it_refuses_bad_inputs_before_it_changes_anything(self):
        with tempfile.TemporaryDirectory() as d:
            for args, text in (
                (['--out', d + '/x.img'], '--out and --info-out'),
                (['--out', d + '/x.img', '--info-out', d, '--apk-repo', d], 'no package repository'),
            ):
                r = subprocess.run(['bash', INNER] + args, capture_output=True, text=True, env=dict(os.environ, SD_PASSWORD=''))
                self.assertNotEqual(r.returncode, 0)
                if os.geteuid() == 0 and os.uname().machine == 'aarch64':
                    self.assertIn(text, r.stderr)
            self.assertEqual(os.listdir(d), [])

    def test_the_passwords_never_reach_a_command_line(self):
        self.assertIn('PASSWORD="${SD_PASSWORD:-}"; AP_PASSWORD="${SD_AP_PASSWORD:-}"', self.src)
        self.assertNotIn('--password', self.src)
        self.assertIn("printf 'root:%s\\n' \"$PASSWORD\" | chroot \"$ROOTFS\" /usr/sbin/chpasswd", self.src)  # through a pipe

    def test_the_image_is_alpine_with_openrc_eudev_and_the_console_screen(self):
        for needle in (
            '--initdb add alpine-base',
            'linux-rpi raspberrypi-bootloader',
            'runlevel sysinit devfs dmesg udev udev-trigger udev-settle',
            'rm -f "$ROOTFS"/etc/runlevels/*/mdev',
            'tty1::respawn:/opt/exxpiano/exxpiano-console.sh',
            'arm_64bit=1',
            'dtparam=audio=on',
            'usercfg.txt',
            'gpu_mem=16',
            'raspberrypi-bootloader-cutdown',
            'snd-seq snd-seq-midi',
            'root=LABEL=exxroot',
            'mkfs.ext4 -q -F -L exxroot -E root_owner=0:0 -d "$ROOTFS"',
            'exxpiano-firstboot exxpiano exxpiano-fx exxpiano-web exxpiano-wifi',
        ):
            self.assertIn(needle, self.src)
        self.assertNotIn('losetup', self.src)
        self.assertNotIn('dietpi', self.src.lower().replace('nothing of debian or dietpi', '').replace('/boot/dietpi', '').replace('grep -qi dietpi', ''))

    def test_the_read_back_covers_every_promise_of_the_image(self):
        for needle in (
            'device trees for the Pi 3, 4 and 5',
            'no installation ID is baked into the reusable image',
            'no automatic login',
            'the console shows a status screen, no login prompt anywhere',
            'hostapd and dnsmasq do not start by themselves',
            'the demonstration pieces are in the MIDI library',
            'the smaller bank for 512 MB boards is in place',
            'root logs in by password only when one was given',
            'nothing of Debian or DietPi',
            '16 MB for the graphics processor, with the cut-down firmware',
            'the ALSA sequencer modules load at boot',
        ):
            self.assertIn(needle, self.src)


if __name__ == '__main__':
    unittest.main()
