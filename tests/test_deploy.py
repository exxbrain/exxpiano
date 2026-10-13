"""scripts/deploy-to-pi.sh: builds the package and streams it to the Pi over ssh (a fake ssh stands in for the Pi)."""
import io
import os
import subprocess
import tarfile
import tempfile
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
with open(os.path.join(ROOT, 'VERSION')) as _f:
    VERSION = _f.read().strip()


class DeployTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.bin = os.path.join(self.tmp.name, 'bin')
        os.makedirs(self.bin)
        self.args_file = os.path.join(self.tmp.name, 'ssh-args.txt')
        self.stdin_file = os.path.join(self.tmp.name, 'ssh-stdin.tgz')
        self.probe_log = os.path.join(self.tmp.name, 'probes.txt')
        self.key_marker = os.path.join(self.tmp.name, 'key-installed')
        self.copy_id_log = os.path.join(self.tmp.name, 'copy-id.txt')
        # the fake ssh: `ssh ... true` is the key-login probe (it works once the key is installed, or always when FAKE_KEY=1); anything else is the upgrade stream
        with open(os.path.join(self.bin, 'ssh'), 'w') as f:
            f.write('#!/bin/bash\nif [ "${@: -1}" = true ]; then\n  printf "%%s\\n" "$*" >> "%s"\n  [ "${FAKE_KEY:-0}" = 1 ] || [ -e "%s" ] && exit 0\n  exit 255\nfi\nprintf "%%s\\n" "$@" > "%s"\ncat > "%s"\n'
                    % (self.probe_log, self.key_marker, self.args_file, self.stdin_file))
        os.chmod(os.path.join(self.bin, 'ssh'), 0o755)
        with open(os.path.join(self.bin, 'ssh-copy-id'), 'w') as f:
            f.write('#!/bin/bash\nprintf "%%s\\n" "$*" >> "%s"\n[ "${FAKE_COPY_ID_FAILS:-0}" = 1 ] && exit 1\ntouch "%s"\n' % (self.copy_id_log, self.key_marker))
        os.chmod(os.path.join(self.bin, 'ssh-copy-id'), 0o755)

    def run_deploy(self, *args, **extra_env):
        env = dict(os.environ, PATH=self.bin + os.pathsep + os.environ['PATH'])
        env['FAKE_KEY'] = '1'
        env.update(extra_env)
        return subprocess.run(['bash', os.path.join(ROOT, 'scripts', 'deploy-to-pi.sh')] + list(args),
                              env=env, capture_output=True, text=True)

    def ssh_args(self):
        with open(self.args_file) as f:
            return f.read().split('\n')

    def sent_files(self):
        with open(self.stdin_file, 'rb') as f:
            data = f.read()
        return sorted(m.name.lstrip('./') for m in tarfile.open(fileobj=io.BytesIO(data), mode='r:gz').getmembers() if m.isfile())

    def test_a_bare_host_means_root_and_the_right_files_are_sent(self):
        r = self.run_deploy('192.168.1.95')
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        args = self.ssh_args()
        target = args.index('root@192.168.1.95')
        self.assertIn('upgrade-on-pi.sh', args[target + 1])
        self.assertEqual(self.sent_files(), sorted(['exxpiano_%s_all.deb' % VERSION, 'rollback-on-pi.sh', 'upgrade-on-pi.sh']))

    def test_an_explicit_user_is_kept(self):
        r = self.run_deploy('denis@piano.local')
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn('denis@piano.local', self.ssh_args())

    def test_the_upgrade_never_asks_for_a_password_or_a_host_key(self):
        self.run_deploy('192.168.1.95')
        args = self.ssh_args()
        self.assertIn('BatchMode=yes', args)                                     # a password prompt is impossible: key login or nothing
        self.assertIn('StrictHostKeyChecking=accept-new', args)                  # no yes/no question for a new host (a changed key still stops it)
        self.assertFalse(os.path.exists(self.copy_id_log))                      # the key was already there: nothing is installed

    def test_a_device_without_your_key_gets_it_once_and_then_the_upgrade_runs(self):
        r = self.run_deploy('192.168.1.95', FAKE_KEY='0')
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn('installing your SSH key', r.stdout)
        with open(self.copy_id_log) as f:
            self.assertEqual(len(f.read().strip().split('\n')), 1)
        with open(self.copy_id_log) as f:
            self.assertIn('root@192.168.1.95', f.read())
        self.assertEqual(self.sent_files(), sorted(['exxpiano_%s_all.deb' % VERSION, 'rollback-on-pi.sh', 'upgrade-on-pi.sh']))
        r = self.run_deploy('192.168.1.95', FAKE_KEY='0')                         # the second upgrade: the key is installed, no second prompt
        self.assertNotIn('installing your SSH key', r.stdout)
        with open(self.copy_id_log) as f:
            self.assertEqual(len(f.read().strip().split('\n')), 1)

    def test_when_the_key_cannot_be_installed_nothing_is_deployed(self):
        r = self.run_deploy('192.168.1.95', FAKE_KEY='0', FAKE_COPY_ID_FAILS='1')
        self.assertEqual(r.returncode, 1)
        self.assertIn('Could not install the key', r.stderr)
        self.assertFalse(os.path.exists(self.stdin_file))

    def test_no_host_prints_usage(self):
        r = self.run_deploy()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('usage', r.stderr)

    def test_the_bank_folder_is_derived_from_the_current_bank_when_missing(self):
        # run the very sed expression of the upgrade script on sample configuration lines
        with open(os.path.join(ROOT, 'deploy', 'upgrade-on-pi.sh')) as f:
            text = f.read()
        self.assertIn("grep -q '^BANK_DIR=' /etc/exxpiano.conf", text)
        expr = text.split("sed -n '")[1].split("' /etc/exxpiano.conf")[0]
        for line, expected in (('PIANO_SF2=/root/soundfonts/v5.sf2', '/root/soundfonts/v5.sf2'),
                               ('PIANO_SF2="/root/sound fonts/v 5.sf2"', '/root/sound fonts/v 5.sf2')):
            r = subprocess.run(['sed', '-n', expr], input=line + '\n', capture_output=True, text=True)
            self.assertEqual(r.stdout.strip(), expected)

    def test_the_scripts_on_the_pi_do_not_hardcode_a_version(self):
        for name in ('upgrade-on-pi.sh', 'rollback-on-pi.sh'):
            with open(os.path.join(ROOT, 'deploy', name)) as f:
                text = f.read()
            self.assertNotIn('1.0.0', text)
            self.assertNotIn('1.1.0', text)
        with open(os.path.join(ROOT, 'deploy', 'upgrade-on-pi.sh')) as f:
            self.assertIn('exxpiano_*_all.deb', f.read())


if __name__ == '__main__':
    unittest.main()
