"""Tests for pi/oslib.py: OpenRC service states, the process of the synthesizer, and the busybox system log of this boot.

The log lines are copied from what Alpine 3.24 (busybox syslogd, util-linux logger, OpenRC supervise-daemon) really writes.
Run from the repository root:   python3 -m unittest discover -s tests -v
"""

import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'pi'))
import oslib  # noqa: E402

NOW = time.mktime((2026, 10, 19, 14, 0, 0, 0, 0, -1))
UNITS = ('exxpiano', 'exxpiano-fx', 'exxpiano-web', 'exxpiano-wifi')

OLD_BOOT = """\
Oct 18 22:10:00 exxpiano-81de syslog.info syslogd started: BusyBox v1.37.0
Oct 18 22:10:05 exxpiano-81de daemon.warn exxpiano-fx: a warning of the last boot
"""
THIS_BOOT = """\
Oct 19 13:37:27 exxpiano-81de syslog.info syslogd started: BusyBox v1.37.0
Oct 19 13:37:27 exxpiano-81de daemon.info supervise-daemon[17]: Child command line: /opt/exxpiano/exxpiano-env /etc/exxpiano.conf -- /opt/exxpiano/exxpiano-run
Oct 19 13:37:28 exxpiano-81de daemon.info exxpiano: exxpiano: audio output plughw:U24 (usb), period 64 x 3
Oct 19 13:37:28 exxpiano-81de daemon.info exxpiano: fluidsynth: chatter that is not ours
Oct 19 13:37:29 exxpiano-81de daemon.warn supervise-daemon[16]: /usr/bin/python3, pid 17, exited with return code 3
Oct 19 13:37:29 exxpiano-81de daemon.info supervise-daemon[22]: Child command line: /opt/exxpiano/exxpiano-env /etc/exxpiano.conf -- /usr/bin/python3 /opt/exxpiano/exxpiano-fx.py --random 1
Oct 19 13:37:30 exxpiano-81de daemon.err exxpiano-fx: no synthesizer port
Oct 19 13:37:31 exxpiano-81de daemon.err exxpiano-fx: no synthesizer port
Oct 19 13:37:32 exxpiano-81de daemon.warn sshd[400]: not one of ours
Oct 19 13:37:33 exxpiano-81de kern.err kernel: [  612.345678] Out of memory: Killed process 812 (fluidsynth) total-vm:1MB, anon-rss:900000kB
Oct 19 13:37:34 exxpiano-81de daemon.warn supervise-daemon[16]: /usr/bin/python3, pid 99, exited with return code 1
"""


class ServiceTests(unittest.TestCase):
    def test_the_states_of_rc_service_become_the_words_the_page_knows(self):
        self.assertEqual(oslib.parse_status(0, ' * status: started\n'), 'active')
        self.assertEqual(oslib.parse_status(3, ' * status: stopped\n'), 'inactive')
        self.assertEqual(oslib.parse_status(32, ' * status: crashed\n'), 'failed')
        self.assertEqual(oslib.parse_status(0, ' * status: starting\n'), 'activating')
        self.assertEqual(oslib.parse_status(1, " * rc-service: service `nosuch' does not exist\n"), 'unknown')
        self.assertEqual(oslib.parse_status(127, 'No such file or directory'), 'unknown')

    def test_service_state_asks_rc_service(self):
        calls = []
        state = oslib.service_state('exxpiano', run=lambda cmd, t: (calls.append(cmd), (0, ' * status: started'))[1])
        self.assertEqual(state, 'active')
        self.assertEqual(calls, [['rc-service', 'exxpiano', 'status']])

    def test_restart_does_not_wait(self):
        seen = {}

        def popen(cmd, **kw):
            seen.update(cmd=cmd, **kw)

        oslib.restart('exxpiano', popen=popen)
        self.assertEqual(seen['cmd'], ['rc-service', 'exxpiano', 'restart'])
        self.assertTrue(seen['start_new_session'])

    def test_the_synthesizer_is_found_by_its_process_name(self):
        with tempfile.TemporaryDirectory() as proc:
            for pid, comm in (('1', 'init'), ('400', 'fluidsynth'), ('512', 'fluidsynth'), ('600', 'python3'), ('self', 'x')):
                os.makedirs(os.path.join(proc, pid))
                with open(os.path.join(proc, pid, 'comm'), 'w') as f:
                    f.write(comm + '\n')
            os.makedirs(os.path.join(proc, '700'))  # a process that ended while it was being read: no comm
            self.assertEqual(oslib.synth_pid(proc), 512)
            self.assertEqual(oslib.synth_pid(proc, comm='nothing'), 0)
        self.assertEqual(oslib.synth_pid('/nonexistent'), 0)


class LogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.rotated = os.path.join(self.tmp.name, 'messages.0')
        self.current = os.path.join(self.tmp.name, 'messages')

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, path, text):
        with open(path, 'w') as f:
            f.write(text)

    def entries(self):
        return oslib.read_messages((self.rotated, self.current), now=NOW)

    def test_only_this_boot_counts_even_across_the_rotated_file(self):
        self.write(self.rotated, OLD_BOOT + THIS_BOOT[: THIS_BOOT.index('Oct 19 13:37:30')])
        self.write(self.current, THIS_BOOT[THIS_BOOT.index('Oct 19 13:37:30') :])
        texts = [e['text'] for e in self.entries()]
        self.assertNotIn('a warning of the last boot', texts)
        self.assertIn('no synthesizer port', texts)
        self.assertIn('exxpiano: audio output plughw:U24 (usb), period 64 x 3', texts)

    def test_service_messages_keep_warnings_errors_our_notes_and_exits(self):
        self.write(self.current, OLD_BOOT + THIS_BOOT)
        found = oslib.service_messages(self.entries(), UNITS)
        self.assertEqual(
            [(e['unit'], e['level'], e['text']) for e in found],
            [
                ('exxpiano', 'info', 'exxpiano: audio output plughw:U24 (usb), period 64 x 3'),
                ('exxpiano', 'warning', 'the process 17 exited with return code 3'),
                ('exxpiano-fx', 'error', 'no synthesizer port'),  # listed once, with the time of the last one
            ],
        )
        self.assertEqual(found[-1]['t'], int(time.mktime((2026, 10, 19, 13, 37, 31, 0, 0, -1)) * 1000))

    def test_the_list_is_limited_and_long_texts_are_cut(self):
        lines = ['Oct 19 12:00:%02d h daemon.err exxpiano-web: error number %d %s' % (i, i, 'x' * 400) for i in range(50)]
        self.write(self.current, '\n'.join(lines) + '\n')
        found = oslib.service_messages(self.entries(), UNITS, limit=40)
        self.assertEqual(len(found), 40)
        self.assertTrue(found[-1]['text'].startswith('error number 49'))
        self.assertEqual(len(found[-1]['text']), 300)

    def test_the_kernel_lines_carry_the_oom_kill(self):
        self.write(self.current, THIS_BOOT)
        kernel = oslib.kernel_messages(self.entries())
        self.assertEqual(len(kernel), 1)
        self.assertIn('Killed process 812 (fluidsynth)', kernel[0]['text'])

    def test_the_text_of_one_service(self):
        self.write(self.current, THIS_BOOT)
        text = oslib.tagged_text(self.entries(), 'exxpiano')
        self.assertIn('audio output plughw:U24 (usb), period 64 x 3', text)
        self.assertNotIn('no synthesizer port', text)

    def test_a_line_of_december_read_in_january_belongs_to_the_last_year(self):
        january = time.mktime((2027, 1, 2, 10, 0, 0, 0, 0, -1))
        e = oslib.parse_line('Dec 31 23:59:58 h daemon.err exxpiano: late', january)
        self.assertEqual(time.localtime(e['t'] / 1000).tm_year, 2026)
        e = oslib.parse_line('Jan  2 09:59:00 h daemon.err exxpiano: early', january)
        self.assertEqual(time.localtime(e['t'] / 1000).tm_year, 2027)

    def test_an_unreadable_log_is_none_and_odd_lines_are_skipped(self):
        self.assertIsNone(self.entries())
        self.write(self.current, 'garbage\n\nOct 19 12:00:00 h daemon.err exxpiano: kept\n')
        self.assertEqual([e['text'] for e in self.entries()], ['kept'])


if __name__ == '__main__':
    unittest.main()
