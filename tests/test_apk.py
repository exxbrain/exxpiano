"""The Alpine packages (packaging/alpine) and their builder (scripts/build-apks.py), read without building anything.

The real build runs in Docker (scripts/build-apks.py) and the installation, the upgrade and the rollback in scripts/test-apk-upgrade.sh.
Run from the repository root:   python3 -m unittest discover -s tests -v
"""

import importlib.util
import os
import re
import subprocess
import tarfile
import tempfile
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
spec = importlib.util.spec_from_file_location('build_apks', os.path.join(ROOT, 'scripts', 'build-apks.py'))
ba = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ba)

PKG = os.path.join(ROOT, 'packaging', 'alpine')
# files of pi/ that are not part of the package
NOT_PACKAGED = set()


def read(*parts):
    with open(os.path.join(*parts), encoding='utf-8') as f:
        return f.read()


def apkbuild(name):
    return read(PKG, name, 'APKBUILD')


def packaged_sources(text):
    """Every pi/ file the package() of an APKBUILD installs: the explicit pi/<path> and the lists of its for loops over pi/$f."""
    found = set(re.findall(r'\bpi/([\w./-]+)', text))
    for words, prefix in re.findall(r'for f in ([^;]+); do\s+install [^\n]*?\b(pi/(?:init\.d/)?)\$f', text, re.S):
        found |= {prefix[3:] + w for w in words.replace('\\', ' ').split()}
    return {f for f in found if '$' not in f and not f.endswith('/')}


class ProgramPackageTests(unittest.TestCase):
    def setUp(self):
        self.text = apkbuild('exxpiano')

    def test_every_program_file_is_packaged(self):
        tree = set()
        for dirpath, dirnames, filenames in os.walk(os.path.join(ROOT, 'pi')):
            dirnames[:] = [d for d in dirnames if d != '__pycache__']
            for name in filenames:
                if not name.endswith('.pyc') and name != '.DS_Store':
                    tree.add(os.path.relpath(os.path.join(dirpath, name), os.path.join(ROOT, 'pi')))
        self.assertEqual(packaged_sources(self.text), tree - NOT_PACKAGED)

    def test_the_services_rules_and_configuration_land_where_alpine_reads_them(self):
        for line in (
            '"$pkgdir"/etc/init.d/$f',
            '"$pkgdir"/etc/exxpiano.conf',
            '"$pkgdir"/usr/lib/udev/rules.d/90-exxpiano.rules',
            '"$pkgdir"/etc/alsa/conf.d/60-exxpiano-tap.conf',
        ):
            self.assertIn(line, self.text)
        self.assertNotIn('systemd', self.text)

    def test_the_dependencies(self):
        depends = re.search(r'depends="([^"]*)"', self.text).group(1).split()
        for needed in (
            'fluidsynth',
            'python3',
            'py3-mido',
            'py3-python-rtmidi',
            'hostapd',
            'dnsmasq',
            'wpa_supplicant',
            'iw',
            'eudev',
            'logger',
            'util-linux-misc',
            'avahi',
            'alsa-utils',
            'bash',
            'iproute2',
            'cloud-utils-growpart',
            'e2fsprogs-extra',
        ):
            self.assertIn(needed, depends)
        for graphical in ('qt', 'gtk', 'mesa', 'x11', 'wayland', 'libx11'):
            self.assertFalse([d for d in depends if graphical in d], graphical)
        self.assertIn('arch="noarch"', self.text)

    def test_the_install_scripts_parse_and_start_nothing_without_openrc(self):
        for script in ('exxpiano.post-install', 'exxpiano.post-upgrade', 'exxpiano.pre-deinstall'):
            path = os.path.join(PKG, 'exxpiano', script)
            self.assertTrue(os.access(path, os.X_OK), script)
            self.assertEqual(subprocess.run(['sh', '-n', path]).returncode, 0, script)
            self.assertIn('$pkgname.' + script.split('.', 1)[1], self.text)
            text = read(path)
            for line in text.splitlines():
                if re.search(r'rc-service [^|]*\b(start|restart|stop)\b', line):
                    self.assertTrue('/run/openrc/softlevel' in text, script)
        self.assertIn('exxpiano-demo-midi.py', read(PKG, 'exxpiano', 'exxpiano.post-install'))

    def test_removing_keeps_the_data_of_the_owner(self):
        text = read(PKG, 'exxpiano', 'exxpiano.pre-deinstall')
        self.assertNotIn('rm ', text)
        self.assertNotIn('/var/lib/exxpiano', text.split('\n', 3)[-1].replace('(/var/lib/exxpiano)', ''))


class LibraryPackageTests(unittest.TestCase):
    def test_the_midi_libraries_are_pinned_releases(self):
        for name in ('py3-mido', 'py3-python-rtmidi'):
            text = apkbuild(name)
            self.assertRegex(text, r'(?m)^pkgver=\d+\.\d+\.\d+$', name)
            self.assertRegex(text, r'source="https://files\.pythonhosted\.org/', name)
            self.assertRegex(text, r'sha512sums="\n[0-9a-f]{128}  \S+\.tar\.gz\n"', name)

    def test_rtmidi_is_built_for_alsa_only(self):
        text = apkbuild('py3-python-rtmidi')
        self.assertIn('alsa-lib-dev', text)
        self.assertIn('-Djack=false', text)

    def test_the_banks_package_needs_the_program(self):
        text = apkbuild('exxpiano-soundfonts')
        self.assertIn('depends="exxpiano"', text)
        self.assertIn('/opt/exxpiano/soundfonts/piano.sf2', text)


class BuilderTests(unittest.TestCase):
    def test_versions(self):
        self.assertEqual(ba.apk_version('1.10.20\n'), '1.10.20')
        self.assertEqual(ba.apk_version('1.11.0-rc1'), '1.11.0_rc1')
        for bad in ('v1.0', '1.0-foo', '', '1..2'):
            with self.subTest(bad), self.assertRaises(ba.BuildError):
                ba.apk_version(bad)

    def test_pkgver_is_set_once(self):
        self.assertIn('pkgver=1.2.3\npkgrel=0', ba.set_pkgver(apkbuild('exxpiano'), '1.2.3'))
        with self.assertRaises(ba.BuildError):
            ba.set_pkgver('pkgname=x\n', '1.0')

    def test_the_source_archive_is_reproducible_and_complete(self):
        with tempfile.TemporaryDirectory() as d:
            a, b = os.path.join(d, 'a.tar.gz'), os.path.join(d, 'b.tar.gz')
            ba.write_source_archive(a, 'exxpiano-1.0', 1700000000)
            ba.write_source_archive(b, 'exxpiano-1.0', 1700000000)
            with open(a, 'rb') as fa, open(b, 'rb') as fb:
                self.assertEqual(fa.read(), fb.read())
            with tarfile.open(a) as tf:
                names = set(tf.getnames())
                mode = tf.getmember('exxpiano-1.0/pi/exxpiano-run').mode
        for needed in (
            'pi/oslib.py',
            'pi/init.d/exxpiano',
            'pi/web/index.html',
            'VERSION',
            'packaging/exxpiano-install-banks',
            'assets/midi/NOTICE.md',
            'docs/TROUBLESHOOTING.md',
        ):
            self.assertIn('exxpiano-1.0/' + needed, names)
        self.assertFalse([n for n in names if '__pycache__' in n or n.endswith('.pyc')])
        self.assertEqual(mode, 0o755)

    def test_without_a_key_the_development_key_is_used_with_a_warning(self):
        name, pem, warning = ba.signing_key({})
        self.assertEqual((name, pem), ('exxpiano-dev', None))
        self.assertIn('development key', warning)
        with self.assertRaises(ba.BuildError):
            ba.signing_key({'EXXPIANO_APK_KEY': 'not a key'})
        with self.assertRaises(ba.BuildError):
            ba.signing_key({'EXXPIANO_APK_KEY': '-----BEGIN PRIVATE KEY-----\nx', 'EXXPIANO_APK_KEY_NAME': '../x'})

    def test_the_key_reaches_the_container_only_through_its_environment(self):
        secret = '-----BEGIN PRIVATE KEY-----\nTOP-SECRET-BYTES\n-----END PRIVATE KEY-----'
        seen = {}

        class Done:
            returncode = 0

        def runner(cmd, env):
            if cmd[:2] == ['docker', 'run']:
                seen.update(cmd=cmd, env=env)
            return Done()

        class Args:
            version, banks = '1.2.3', None

        with tempfile.TemporaryDirectory() as out:
            Args.out, Args.work = out, os.path.join(out, 'work')
            ba.build(Args, env={'EXXPIANO_APK_KEY': secret, 'PATH': os.environ.get('PATH', '')}, runner=runner)
        self.assertFalse([a for a in seen['cmd'] if 'TOP-SECRET' in a])
        self.assertIn('EXXPIANO_APK_KEY', seen['cmd'])  # "-e NAME": the value comes from the environment
        self.assertEqual(seen['env']['EXXPIANO_APK_KEY'], secret)
        self.assertEqual(seen['env']['KEY_NAME'], 'exxpiano')
        self.assertIn('unset EXXPIANO_APK_KEY', ba.INNER)  # the build itself does not see it
        self.assertIn(ba.APK_CACHE + ':/var/cache/apk', seen['cmd'])  # the downloads are kept for the next build

    def test_another_mirror_must_be_https(self):
        self.assertIsNone(ba.alpine_mirror({}))
        self.assertEqual(ba.alpine_mirror({'EXXPIANO_ALPINE_MIRROR': 'https://mirror.yandex.ru/mirrors/alpine/'}), 'https://mirror.yandex.ru/mirrors/alpine')
        for bad in ('http://mirror/alpine', 'https://x/a b', 'ftp://x'):
            with self.subTest(bad), self.assertRaises(ba.BuildError):
                ba.alpine_mirror({'EXXPIANO_ALPINE_MIRROR': bad})


if __name__ == '__main__':
    unittest.main()
