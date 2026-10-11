"""Tests for pi/sysmon.py with fake /proc and /sys trees (no Pi needed)."""
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'pi'))
import sysmon  # noqa: E402


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


def put(root, rel, text):
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w') as f:
        f.write(text)


class MonitorCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.proc = os.path.join(self.tmp.name, 'proc')
        self.sys = os.path.join(self.tmp.name, 'sys')
        self.clock = Clock()
        self.raw = 0
        self.mon = sysmon.Monitor(self.proc, self.sys, throttled=lambda: self.raw, clock=self.clock, wall=lambda: 1000.0 + self.clock.t,
                                  sleep=self.clock.sleep, hz=100)

    def cpu(self, total, cores):
        """cpu lines: (idle, busy) pairs; user = busy, idle = idle."""
        lines = ['cpu  %d 0 0 %d 0 0 0 0 0 0' % (total[1], total[0])]
        for i, (idle, busy) in enumerate(cores):
            lines.append('cpu%d %d 0 0 %d 0 0 0 0 0 0' % (i, busy, idle))
        put(self.proc, 'stat', '\n'.join(lines) + '\nintr 0\n')

    def process(self, pid, comm, cmdline, ticks):
        put(self.proc, '%d/comm' % pid, comm + '\n')
        put(self.proc, '%d/cmdline' % pid, cmdline.replace(' ', '\0'))
        # pid (comm) S ppid pgrp session tty tpgid flags minflt cminflt majflt cmajflt utime stime ...
        put(self.proc, '%d/stat' % pid, '%d (%s) S 1 1 1 0 -1 0 0 0 0 0 %d %d 0 0 20 0 1 0 0 0 0\n' % (pid, comm, ticks, 0))


class CpuTests(MonitorCase):
    def test_the_first_call_measures_over_a_short_pause_and_gives_percentages(self):
        self.cpu((100, 100), [(100, 100)] * 4)
        calls = []
        orig = self.clock.sleep

        def sleeping(s):
            calls.append(s)
            orig(s)
            self.cpu((175, 125), [(175, 125)] * 4)       # in the pause: 75 idle, 25 busy -> 25 %
        self.mon.sleep = sleeping
        snap = self.mon.get()['now']
        self.assertEqual(calls, [0.25])
        self.assertEqual(snap['cpu'], 25.0)
        self.assertEqual(snap['cores'], [25.0] * 4)

    def test_later_calls_use_the_time_since_the_previous_sample(self):
        self.cpu((100, 100), [(100, 100)])
        self.mon.sleep = lambda s: (self.clock.sleep(s), self.cpu((100, 100), [(100, 100)]))
        self.mon.get()
        self.clock.t += 1
        self.cpu((100, 300), [(100, 300)])                 # nothing idle: 100 %
        self.assertEqual(self.mon.get()['now']['cpu'], 100.0)
        self.clock.t += 1
        self.cpu((300, 300), [(300, 300)])                 # all idle
        self.assertEqual(self.mon.get()['now']['cpu'], 0.0)

    def test_a_second_client_within_the_interval_shares_the_sample(self):
        self.cpu((100, 100), [(100, 100)])
        first = self.mon.get()
        self.clock.t += 0.1
        self.cpu((100, 900), [(100, 900)])
        second = self.mon.get()
        self.assertEqual(first['now'], second['now'])
        self.assertEqual(len(self.mon.history), 1)

    def test_a_counter_that_goes_backwards_never_gives_nonsense(self):
        self.cpu((500, 500), [(500, 500)])
        self.mon.get()
        self.clock.t += 1
        self.cpu((10, 10), [(10, 10)])                      # after a reset of the counters
        cpu = self.mon.get()['now']['cpu']
        self.assertTrue(0.0 <= cpu <= 100.0)

    def test_without_proc_everything_is_none_not_an_error(self):
        snap = self.mon.get()['now']
        self.assertIsNone(snap['cpu'])
        self.assertEqual(snap['cores'], [])
        for key in ('temp', 'freq', 'freq_max', 'mem', 'load'):
            self.assertIsNone(snap[key])


class ReadingsTests(MonitorCase):
    def test_temperature_frequency_memory_and_load(self):
        put(self.sys, 'class/thermal/thermal_zone0/temp', '61234\n')
        put(self.sys, 'devices/system/cpu/cpu0/cpufreq/scaling_cur_freq', '1200000\n')
        put(self.sys, 'devices/system/cpu/cpu0/cpufreq/cpuinfo_max_freq', '1400000\n')
        put(self.proc, 'meminfo', 'MemTotal:         954000 kB\nMemFree: 1 kB\nMemAvailable:    454000 kB\n')
        put(self.proc, 'loadavg', '0.52 0.40 0.30 1/200 999\n')
        snap = self.mon.get()['now']
        self.assertEqual((snap['temp'], snap['freq'], snap['freq_max']), (61.2, 1200, 1400))
        self.assertEqual(snap['mem'], dict(total=931, used=488))
        self.assertEqual(snap['load'], [0.52, 0.4, 0.3])

    def test_junk_values_are_ignored(self):
        put(self.sys, 'class/thermal/thermal_zone0/temp', 'hot\n')
        put(self.proc, 'meminfo', 'MemTotal: lots kB\n')
        put(self.proc, 'loadavg', 'a b c\n')
        snap = self.mon.get()['now']
        self.assertEqual((snap['temp'], snap['mem'], snap['load']), (None, None, None))


class ThrottleTests(unittest.TestCase):
    def test_bits_say_what_happens_now_and_what_happened_since_the_boot(self):
        self.assertEqual(sysmon.parse_throttled(0), dict(raw=0, now=[], past=[]))
        t = sysmon.parse_throttled(0x50005)                # under-voltage and throttling, now and before
        self.assertEqual(t['now'], ['undervoltage', 'throttled'])
        self.assertEqual(t['past'], ['undervoltage', 'throttled'])
        t = sysmon.parse_throttled(0x80008)
        self.assertEqual((t['now'], t['past']), (['soft_limit'], ['soft_limit']))
        t = sysmon.parse_throttled(0x20000)
        self.assertEqual((t['now'], t['past']), ([], ['freq_capped']))
        self.assertIsNone(sysmon.parse_throttled(None))

    def test_the_kernel_file_is_the_fallback_and_nothing_gives_none(self):
        with tempfile.TemporaryDirectory() as d:
            old = os.environ['PATH']
            os.environ['PATH'] = d                          # no vcgencmd here
            try:
                self.assertIsNone(sysmon.read_throttled(d))
                put(d, 'devices/platform/soc/soc:firmware/get_throttled', '50000\n')
                self.assertEqual(sysmon.read_throttled(d), 0x50000)
            finally:
                os.environ['PATH'] = old

    def test_vcgencmd_is_used_when_it_exists(self):
        with tempfile.TemporaryDirectory() as d:
            script = os.path.join(d, 'vcgencmd')
            with open(script, 'w') as f:
                f.write('#!/bin/sh\necho "throttled=0x50005"\n')
            os.chmod(script, 0o755)
            old = os.environ['PATH']
            os.environ['PATH'] = d + os.pathsep + old
            try:
                self.assertEqual(sysmon.read_throttled(d), 0x50005)
            finally:
                os.environ['PATH'] = old

    def test_the_sample_carries_the_throttle_state(self):
        with tempfile.TemporaryDirectory() as d:
            mon = sysmon.Monitor(os.path.join(d, 'p'), os.path.join(d, 's'), throttled=lambda: 0x50005, sleep=lambda s: None)
            self.assertEqual(mon.get()['now']['throttle']['now'], ['undervoltage', 'throttled'])


class ProcessTests(MonitorCase):
    def test_the_piano_services_are_found_and_measured_as_a_share_of_one_core(self):
        self.cpu((100, 100), [(100, 100)])
        self.process(10, 'fluidsynth', '/usr/bin/fluidsynth -a alsa', 1000)
        self.process(11, 'python3', '/usr/bin/python3 /opt/exxpiano/exxpiano-fx.py --x', 50)
        self.process(12, 'python3', '/usr/bin/python3 /opt/exxpiano/exxpiano-web.py', 5)
        self.process(13, 'bash', 'bash', 99999)
        self.process(14, 'python3', 'python3 other.py', 99999)

        def pause(s):
            self.clock.sleep(s)
            self.process(10, 'fluidsynth', '/usr/bin/fluidsynth', 1025)        # 25 ticks in 0.25 s at 100 Hz: one full core
            self.process(11, 'python3', '/opt/exxpiano/exxpiano-fx.py', 51)
            self.process(12, 'python3', '/opt/exxpiano/exxpiano-web.py', 5)
        self.mon.sleep = pause
        p = self.mon.get()['now']['procs']
        self.assertEqual(p['synth'], 100.0)
        self.assertEqual(p['bridge'], 4.0)
        self.assertEqual(p['web'], 0.0)

    def test_a_missing_service_is_none_and_a_restart_is_picked_up(self):
        self.cpu((100, 100), [(100, 100)])
        p = self.mon.get()['now']['procs']
        self.assertEqual(p, dict(synth=None, bridge=None, web=None))
        self.process(20, 'fluidsynth', 'fluidsynth', 10)
        self.clock.t += 30                                  # the process list is scanned again after the cache time
        self.mon.get()
        self.process(20, 'fluidsynth', 'fluidsynth', 110)
        self.clock.t += 1
        self.assertEqual(self.mon.get()['now']['procs']['synth'], 100.0)

    def test_unreadable_process_files_do_not_break_the_sample(self):
        self.cpu((100, 100), [(100, 100)])
        self.process(30, 'fluidsynth', 'fluidsynth', 1)
        put(self.proc, '30/stat', 'garbage')
        self.assertIn('synth', self.mon.get()['now']['procs'])


CARDS = """ 0 [Headphones     ]: bcm2835_headphon - bcm2835 Headphones
                      bcm2835 Headphones
 1 [U24            ]: USB-Audio - U-24
                      ZOOM Corporation U-24 at usb-3f980000.usb-1.3, high speed
 2 [mk3            ]: USB-Audio - KeyLab Essential 49 mk3
                      Arturia KeyLab Essential 49 mk3 at usb-3f980000.usb-1.1, full speed
"""
HW_U24 = """access: MMAP_INTERLEAVED
format: S32_LE
subformat: STD
channels: 4
rate: 44100 (44100/1)
period_size: 64
buffer_size: 192
"""
STATUS = "state: RUNNING\nowner_pid   : 1234\ntrigger_time: 1.1\ndelay       : 120\navail       : 72\navail_max   : 100\n"
HW_ONBOARD = "access: RW_INTERLEAVED\nformat: S16_LE\nsubformat: STD\nchannels: 2\nrate: 44100 (44100/1)\nperiod_size: 444\nbuffer_size: 1332\n"
STREAM = """ZOOM Corporation U-24 at usb-3f980000.usb-1.3, high speed : USB Audio

Playback:
  Status: Running
    Interface = 1
    Altset = 1
    Packet Size = 112
    Momentary freq = 44100 Hz (0x2c.199a)
  Interface 1
    Altset 1
    Format: S32_LE
    Channels: 4
    Endpoint: 0x01 (1 OUT) (ASYNC)
    Rates: 44100, 48000, 88200, 96000
    Bits: 24
Capture:
  Status: Stop
  Interface 2
    Format: S32_LE
    Endpoint: 0x82 (2 IN) (ASYNC)
    Rates: 44100
"""


class AudioTests(MonitorCase):
    def setUp(self):
        super().setUp()
        self.snd = os.path.join(self.proc, 'asound')
        put(self.snd, 'cards', CARDS)

    def stream(self, card, hw, status=STATUS):
        put(self.snd, 'card%d/pcm0p/sub0/hw_params' % card, hw)
        put(self.snd, 'card%d/pcm0p/sub0/status' % card, status)

    def test_a_usb_interface_in_use_shows_what_the_driver_negotiated(self):
        self.stream(1, HW_U24)
        put(self.snd, 'card1/stream0', STREAM)
        (a,) = self.mon.get()['now']['audio']
        self.assertEqual((a['card'], a['id'], a['name']), (1, 'U24', 'U-24'))
        self.assertEqual((a['format'], a['channels'], a['rate']), ('S32_LE', 4, 44100))
        self.assertEqual((a['period'], a['buffer']), (64, 192))
        self.assertEqual((a['period_ms'], a['buffer_ms']), (1.45, 4.35))
        self.assertEqual((a['state'], a['delay'], a['avail'], a['avail_max']), ('RUNNING', 120, 72, 100))
        self.assertEqual(a['usb']['sync'], 'ASYNC')
        self.assertEqual(a['usb']['rates'], [44100, 48000, 88200, 96000])
        self.assertEqual((a['usb']['bits'], a['usb']['momentary']), (24, 44100))

    def test_the_onboard_jack_shows_the_period_the_driver_chose_not_the_one_asked_for(self):
        self.stream(0, HW_ONBOARD)
        (a,) = self.mon.get()['now']['audio']
        self.assertEqual((a['id'], a['period'], a['buffer'], a['usb']), ('Headphones', 444, 1332, None))
        self.assertEqual(a['buffer_ms'], 30.2)

    def test_closed_streams_and_capture_only_cards_are_not_listed(self):
        put(self.snd, 'card0/pcm0p/sub0/hw_params', 'closed\n')
        put(self.snd, 'card2/pcm0c/sub0/hw_params', HW_U24)                  # the keyboard's capture side
        self.assertEqual(self.mon.get()['now']['audio'], [])

    def test_only_the_card_in_use_is_listed_when_several_are_present(self):
        put(self.snd, 'card0/pcm0p/sub0/hw_params', 'closed\n')
        self.stream(1, HW_U24)
        self.assertEqual([a['id'] for a in self.mon.get()['now']['audio']], ['U24'])

    def test_junk_and_a_missing_asound_give_a_partial_answer_or_nothing(self):
        self.assertEqual(self.mon.get()['now']['audio'], [])
        self.clock.t += 1                                            # a new sample, not the cached one
        self.stream(1, 'rate: fast\nperiod_size: x\nbuffer_size:\n', 'nonsense')
        (a,) = self.mon.get()['now']['audio']
        self.assertEqual((a['rate'], a['period'], a['period_ms'], a['buffer_ms'], a['state']), (None, None, None, None, None))

    def test_parse_usb_stream_reads_only_the_playback_half(self):
        u = sysmon.parse_usb_stream(STREAM)
        self.assertEqual((u['sync'], u['rates'], u['bits']), ('ASYNC', [44100, 48000, 88200, 96000], 24))
        self.assertIsNone(sysmon.parse_usb_stream(''))
        self.assertIsNone(sysmon.parse_usb_stream('Capture:\n  Status: Stop\n'))


class HistoryTests(MonitorCase):
    def test_points_accumulate_and_since_returns_only_the_new_ones(self):
        self.cpu((100, 100), [(100, 100)])
        first = self.mon.get()
        self.assertEqual(len(first['points']), 1)
        t0 = first['points'][0]['t']
        for _ in range(3):
            self.clock.t += 1
            self.mon.get()
        self.assertEqual(len(self.mon.get(0)['points']), 4)
        self.assertEqual(len(self.mon.get(t0)['points']), 3)
        self.assertEqual(self.mon.get(10 ** 15)['points'], [])

    def test_the_history_is_bounded(self):
        self.cpu((100, 100), [(100, 100)])
        for _ in range(sysmon.HISTORY + 20):
            self.clock.t += 1
            self.mon.get()
        self.assertEqual(len(self.mon.history), sysmon.HISTORY)


if __name__ == '__main__':
    unittest.main()
