"""The OpenRC services, the eudev rules and the first start of the image (pi/init.d, pi/90-exxpiano.rules, pi/exxpiano-firstboot).

These read the files; scripts/test-openrc-services.sh starts the services in an Alpine container.
Run from the repository root:   python3 -m unittest discover -s tests -v
"""

import os
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
INIT = os.path.join(ROOT, 'pi', 'init.d')
SUPERVISED = ('exxpiano', 'exxpiano-fx', 'exxpiano-web')
SERVICES = SUPERVISED + ('exxpiano-wifi', 'exxpiano-firstboot')


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding='utf-8') as f:
        return f.read()


def var(text, name):
    m = re.search(r'^%s=(.*)$' % re.escape(name), text, re.M)
    return m.group(1).strip('"') if m else None


class InitScriptTests(unittest.TestCase):
    def test_every_service_is_an_openrc_script_that_parses(self):
        self.assertEqual(sorted(os.listdir(INIT)), sorted(SERVICES))
        for name in SERVICES:
            path = os.path.join(INIT, name)
            self.assertTrue(read('pi', 'init.d', name).startswith('#!/sbin/openrc-run\n'), name)
            self.assertTrue(os.access(path, os.X_OK), name)
            r = subprocess.run(['sh', '-n', path], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)

    def test_no_systemd_unit_is_left(self):
        self.assertEqual([n for n in os.listdir(os.path.join(ROOT, 'pi')) if n.endswith('.service')], [])

    def test_the_settings_file_is_never_run_by_a_shell(self):
        for name in SERVICES:
            text = read('pi', 'init.d', name)
            self.assertNotRegex(text, r'(^|\s)(\.|source)\s+/etc/exxpiano\.conf', name)

    def test_the_supervised_services_restart_without_a_limit_and_log(self):
        for name in SUPERVISED:
            text = read('pi', 'init.d', name)
            self.assertEqual(var(text, 'supervisor'), 'supervise-daemon', name)
            self.assertEqual(var(text, 'respawn_max'), '0', name)
            self.assertEqual(var(text, 'respawn_delay'), '2', name)
            self.assertEqual(var(text, 'command'), '/opt/exxpiano/exxpiano-env', name)
            self.assertIn('/etc/exxpiano.conf -- ', var(text, 'command_args'), name)
            for logger in ('output_logger', 'error_logger'):
                self.assertEqual(var(text, logger), 'logger -t %s -p daemon.info' % name, name)

    def test_the_synthesizer_gets_its_limits_and_the_bridge_its_options(self):
        self.assertEqual(var(read('pi', 'init.d', 'exxpiano'), 'rc_ulimit'), '-r 95 -l unlimited')
        fx = read('pi', 'init.d', 'exxpiano-fx')
        self.assertIn("--nice -5 ", var(fx, 'command_args'))
        self.assertIn("'\\$FX_ARGS'", fx)  # kept from the shell that evaluates command_args, for exxpiano-env

    def test_the_order_of_the_services(self):
        for name in ('exxpiano-fx', 'exxpiano-web'):
            self.assertRegex(read('pi', 'init.d', name), r'\n\tafter [^\n]*\bexxpiano\b', name)
            self.assertNotRegex(read('pi', 'init.d', name), r'\n\tneed [^\n]*\bexxpiano\b', name)  # a restart of the synthesizer leaves them running
        self.assertRegex(read('pi', 'init.d', 'exxpiano-firstboot'), r'before exxpiano exxpiano-wifi exxpiano-web')

    def test_the_piano_starts_before_the_network(self):
        for name in ('exxpiano', 'exxpiano-fx', 'exxpiano-firstboot'):
            text = read('pi', 'init.d', name)
            self.assertRegex(text, r'\n\tbefore [^\n]*\bnetworking\b', name)
            self.assertNotRegex(text, r'\n\t(need|after|use) [^\n]*\b(net|networking|chronyd|sshd|exxpiano-web|exxpiano-wifi)\b', name)
        self.assertRegex(read('pi', 'init.d', 'exxpiano'), r'\n\tafter [^\n]*\bmodules\b')  # the ALSA sequencer modules: the MIDI port
        for script in ('exxpiano.post-install', 'exxpiano.post-upgrade'):
            text = read('packaging', 'alpine', 'exxpiano', script)
            self.assertIn('rc-update add "$service" boot', text)
            self.assertIn('rc-update del "$service" default', text)  # an older installation had them in default
        inner = read('scripts', 'sd-image-inner.sh')
        boot = next(l for l in inner.splitlines() if l.startswith('runlevel boot '))
        default = next(l for l in inner.splitlines() if l.startswith('runlevel default '))
        for s in ('alsa', 'exxpiano-firstboot', 'exxpiano', 'exxpiano-fx'):
            self.assertIn(' %s' % s, boot)
            self.assertNotIn(' %s ' % s, default + ' ')

    def test_the_wifi_supervisor_is_restarted_only_after_a_failure(self):
        text = read('pi', 'init.d', 'exxpiano-wifi')
        self.assertIsNone(var(text, 'supervisor'))
        self.assertEqual(var(text, 'command'), '/opt/exxpiano/exxpiano-respawn')
        self.assertIn('--service ${RC_SVCNAME} --', var(text, 'command_args'))
        self.assertIn('exxpiano-env --optional /etc/exxpiano.conf', var(text, 'command_args'))
        self.assertIn('checkpath -d -m 0700', text)


class DeviceRuleTests(unittest.TestCase):
    def setUp(self):
        self.rules = [line for line in read('pi', '90-exxpiano.rules').splitlines() if line and not line.startswith('#')]

    def test_no_systemd_is_left(self):
        for line in self.rules:
            self.assertNotIn('systemctl', line)
            self.assertNotIn('SYSTEMD', line)

    def test_every_action_returns_at_once(self):
        self.assertEqual(len(self.rules), 4)
        for line in self.rules:
            self.assertIn('RUN+="/usr/bin/setsid -f ', line)

    def test_a_stopped_synthesizer_stays_stopped(self):
        sound = [line for line in self.rules if 'SUBSYSTEM=="sound"' in line]
        self.assertEqual(len(sound), 2)
        for line in sound:
            self.assertIn('rc-service --ifstarted exxpiano restart', line)

    def test_drives_are_mounted_and_forgotten(self):
        block = [line for line in self.rules if 'SUBSYSTEM=="block"' in line]
        self.assertTrue(any('ACTION=="add"' in r and 'exxpiano-usb.py add /dev/%k' in r for r in block))
        self.assertTrue(any('ACTION=="remove"' in r and 'exxpiano-usb.py remove /dev/%k' in r for r in block))


@unittest.skipUnless(shutil.which('bash'), 'needs bash')
class FirstBootTests(unittest.TestCase):
    CONF = 'PIANO_SF2=/opt/exxpiano/soundfonts/piano.sf2\nMECH_SF2=/opt/exxpiano/soundfonts/mech.sf2\n'

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name
        self.conf = self.path('exxpiano.conf')
        self.write(self.conf, self.CONF)
        self.lite = self.path('piano-lite.sf2')
        self.write(self.lite, 'a smaller bank')
        self.calls = self.path('calls')

    def tearDown(self):
        self.tmp.cleanup()

    def path(self, name):
        return os.path.join(self.dir, name)

    def write(self, path, text):
        with open(path, 'w') as f:
            f.write(text)

    def tool(self, name, code):
        """A stand-in program that records how it was called and ends with `code`."""
        path = self.path(name)
        self.write(path, '#!/bin/sh\necho "%s $*" >> %s\nexit %d\n' % (name, self.calls, code))
        os.chmod(path, 0o755)
        return path

    def run_firstboot(self, mem_kb, root='/dev/mmcblk0p2', grow=0, resize=0, wifi=0):
        self.write(self.path('meminfo'), 'MemTotal:       %d kB\nMemFree:        1000 kB\n' % mem_kb)
        env = dict(
            os.environ,
            FIRSTBOOT_LOG=self.path('log'),
            STAMP=self.path('stamp'),
            CONF=self.conf,
            MEMINFO=self.path('meminfo'),
            BOOT_KEYS=self.path('boot-authorized_keys'),
            ROOT_KEYS=self.path('root-ssh/authorized_keys'),
            LITE=self.lite,
            ROOT_SOURCE=root,
            GROWPART=self.tool('growpart', grow),
            RESIZE2FS=self.tool('resize2fs', resize),
            WIFI=self.tool('ensure-name', wifi),
            ASOUND=self.path('asound'),
            AMIXER=self.tool('amixer', 0),
            ALSACTL=self.tool('alsactl', 0),
        )
        r = subprocess.run(['bash', os.path.join(ROOT, 'pi', 'exxpiano-firstboot')], env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        with open(self.conf) as f:
            conf = f.read()
        calls = open(self.calls).read() if os.path.exists(self.calls) else ''
        return conf, calls, open(self.path('log')).read()

    def test_a_board_with_512_mb_gets_the_smaller_bank(self):
        conf, calls, _ = self.run_firstboot(480000)
        self.assertIn('PIANO_SF2=/opt/exxpiano/soundfonts/piano-lite.sf2\n', conf)
        self.assertIn('growpart /dev/mmcblk0 2', calls)
        self.assertIn('resize2fs /dev/mmcblk0p2', calls)
        self.assertIn('ensure-name', calls)
        with open(os.path.join(ROOT, 'pi', 'exxpiano-firstboot')) as f:
            self.assertIn('exxpiano-wifi.py ensure-name exxpiano}', f.read())  # the generic name of the image is renamed
        self.assertTrue(os.path.exists(self.path('stamp')))

    def test_a_board_with_1_gb_keeps_the_full_bank(self):
        conf, _, _ = self.run_firstboot(948000)
        self.assertEqual(conf, self.CONF)

    def test_a_failed_growth_does_not_stop_the_rest(self):
        conf, calls, log = self.run_firstboot(480000, grow=1, wifi=1)
        self.assertNotIn('resize2fs', calls)
        self.assertIn('was not grown', log)
        self.assertIn('piano-lite.sf2', conf)
        self.assertTrue(os.path.exists(self.path('stamp')))

    def test_the_jack_starts_at_the_full_volume_once(self):
        os.makedirs(self.path('asound'))
        self.write(self.path('asound/cards'), ' 0 [Headphones     ]: bcm2835_headpho - bcm2835 Headphones\n')
        _, calls, log = self.run_firstboot(948000)
        self.assertIn('amixer -c Headphones -q sset PCM 100% unmute', calls)
        self.assertIn('alsactl store', calls)
        self.assertIn('the 3.5 mm jack is at the full volume', log)
        os.unlink(self.calls)
        self.run_firstboot(948000)  # a later start: the owner's volume is kept
        self.assertFalse(os.path.exists(self.calls) and 'amixer' in open(self.calls).read())

    def test_a_board_without_the_jack_is_left_alone(self):
        os.makedirs(self.path('asound'))
        self.write(self.path('asound/cards'), ' 0 [U24            ]: USB-Audio - U-24\n')  # a Pi 5 with a USB sound card
        _, calls, _ = self.run_firstboot(948000)
        self.assertNotIn('amixer', calls)

    def test_it_runs_once(self):
        self.run_firstboot(948000)
        os.unlink(self.calls)
        self.run_firstboot(480000)
        self.assertFalse(os.path.exists(self.calls))
        with open(self.conf) as f:
            self.assertEqual(f.read(), self.CONF)

    def test_keys_on_the_boot_partition_are_added_at_every_start(self):
        self.write(self.path('boot-authorized_keys'), 'ssh-ed25519 AAAAone owner@laptop\r\n# a comment\ncommand="rm -rf /" ssh-rsa AAAAbad\n')
        self.run_firstboot(948000)
        keys = self.path('root-ssh/authorized_keys')
        with open(keys) as f:
            self.assertEqual(f.read(), 'ssh-ed25519 AAAAone owner@laptop\n')  # only plain key lines
        self.assertEqual(os.stat(keys).st_mode & 0o777, 0o600)
        self.assertEqual(os.stat(os.path.dirname(keys)).st_mode & 0o777, 0o700)
        self.write(self.path('boot-authorized_keys'), 'ssh-ed25519 AAAAone owner@laptop\nssh-ed25519 AAAAtwo phone')
        self.run_firstboot(948000)  # a later start: the stamp is there
        with open(keys) as f:
            self.assertEqual(f.read(), 'ssh-ed25519 AAAAone owner@laptop\nssh-ed25519 AAAAtwo phone\n')

    def test_a_root_that_is_not_a_card_partition_is_left_alone(self):
        _, calls, log = self.run_firstboot(948000, root='overlay')
        self.assertNotIn('growpart', calls)
        self.assertIn('not a card partition', log)


if __name__ == '__main__':
    unittest.main()
