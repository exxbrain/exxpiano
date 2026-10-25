"""The level meter of the output (pi/exxpiano-meter.py): peaks per channel, clipped samples, the status file, and the pipe loop."""

import importlib.util
import json
import math
import os
import struct
import tempfile
import threading
import time
import unittest
import unittest.mock

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location('meter', os.path.join(HERE, '..', 'pi', 'exxpiano-meter.py'))
meter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(meter)


def pcm(left, right):
    return b''.join(struct.pack('<hh', l, r) for l, r in zip(left, right))


class MeterTests(unittest.TestCase):
    def test_the_peak_of_each_channel_is_measured_separately_in_dbfs(self):
        m = meter.Meter()
        m.feed(pcm([3277, -3277, 0, 100], [16384, -16384, 0, 5]), 1.0)
        s = m.status(1.1, 1000.0)
        self.assertAlmostEqual(s['left']['peak'], 20 * math.log10(3277 / 32768.0), delta=0.6)  # about -20 dBFS (the high byte gives steps of 256)
        self.assertAlmostEqual(s['right']['peak'], -6.0, delta=0.15)  # half scale
        self.assertEqual((s['left']['clipped'], s['right']['clipped']), (0, 0))
        self.assertIsNone(s['clip_ago'])

    def test_samples_at_full_scale_are_counted_per_channel_and_remembered(self):
        m = meter.Meter()
        m.feed(pcm([0, 0, 0, 0], [32767, 32767, -32768, 100]), 5.0)
        m.feed(pcm([32767, 0, 0, 0], [0, 0, 0, 0]), 6.0)
        s = m.status(8.0, 1000.0)
        self.assertEqual((s['left']['clipped'], s['right']['clipped']), (1, 3))
        self.assertEqual(s['clip_ago'], 2.0)  # since the last clipped sample
        self.assertEqual(s['right']['peak'], 0.0)  # a clipped channel reads 0 dBFS

    def test_quiet_signals_are_still_measured_and_the_samples_at_the_limit_are_counted_with_a_margin(self):
        m = meter.Meter()
        m.feed(pcm([60] * 4000, [-100] * 4000), 0.0)  # below -42 dBFS: the high byte is 0, the thin sample takes over
        s = m.status(0.1, 1.0)
        self.assertAlmostEqual(s['left']['peak'], 20 * math.log10(60 / 32768.0), delta=0.1)
        self.assertAlmostEqual(s['right']['peak'], 20 * math.log10(100 / 32768.0), delta=0.1)
        m.feed(pcm([32600, 32511, 0, 0], [-32768, -32600, -32511, 0]), 1.0)  # within 0.06 dB of full scale counts (0x7F/0x80 high byte), 32511 does not
        s = m.status(1.1, 2.0)
        self.assertEqual((s['left']['clipped'], s['right']['clipped']), (1, 2))

    def test_silence_reads_as_the_floor_and_the_held_peak_falls_slowly(self):
        m = meter.Meter()
        m.feed(pcm([32767 // 2] * 10, [0] * 10), 0.0)
        first = m.status(0.1, 1.0)
        self.assertAlmostEqual(first['left']['hold'], -6.0, delta=0.1)
        m.feed(pcm([0] * 10, [0] * 10), 0.2)
        second = m.status(0.6, 1.5)  # half a second later: 20 dB/s -> about 10 dB lower
        self.assertEqual(second['left']['peak'], meter.SILENCE_DB)
        self.assertAlmostEqual(second['left']['hold'], -6.0 - 0.5 * meter.HOLD_FALL, delta=0.5)
        self.assertEqual(second['right']['hold'], meter.SILENCE_DB)

    def test_the_rate_in_the_status_is_the_one_the_synthesizer_plays_at(self):
        for value, want in (('48000', 48000), (None, 44100), ('22050', 44100), ('fast', 44100)):
            with self.subTest(value):
                env = {k: v for k, v in os.environ.items() if k != 'SAMPLE_RATE'}
                if value is not None:
                    env['SAMPLE_RATE'] = value
                with unittest.mock.patch.dict(os.environ, env, clear=True):
                    self.assertEqual(meter._rate(), want)

    def test_a_block_that_ends_inside_a_frame_does_not_shift_the_channels(self):
        m = meter.Meter()
        data = pcm([1000, 2000], [30000, 4000])
        m.feed(data[:6], 0.0)  # one frame and a half
        m.feed(data[6:], 0.0)
        s = m.status(0.1, 1.0)
        self.assertAlmostEqual(s['left']['peak'], 20 * math.log10(2000 / 32768.0), delta=0.6)
        self.assertAlmostEqual(s['right']['peak'], 20 * math.log10(30000 / 32768.0), delta=0.15)

    def test_the_pipe_loop_publishes_a_status_file_that_the_web_interface_can_read(self):
        with tempfile.TemporaryDirectory() as d:
            pipe, out = os.path.join(d, 'pcm'), os.path.join(d, 'levels.json')
            os.mkfifo(pipe)
            holder = os.open(pipe, os.O_RDWR)  # what exxpiano-run does: the pipe is always open
            t = threading.Thread(target=meter.run, args=(pipe, out), daemon=True)
            t.start()
            try:
                for _ in range(20):
                    os.write(holder, pcm([8000] * 500, [32767] * 500))
                    time.sleep(0.02)
                for _ in range(50):
                    if os.path.exists(out):
                        break
                    time.sleep(0.05)
                with open(out) as f:
                    s = json.load(f)
                self.assertEqual(s['rate'], meter.RATE)
                self.assertGreater(s['right']['clipped'], 0)
                self.assertEqual(s['left']['clipped'], 0)
                self.assertGreater(s['right']['hold'], s['left']['hold'])
            finally:
                os.close(holder)
            # The loop reads what is still in the pipe and publishes while it does; with the writer closed it reaches the end and waits for the next
            # start of the synthesizer. Wait until it is quiet, then no half-written file may be left (and the folder can be removed).
            last, quiet = None, 0
            for _ in range(100):
                state = (sorted(os.listdir(d)), os.stat(out).st_mtime_ns)
                quiet = quiet + 1 if state == last else 0
                if quiet >= 6:
                    break
                last = state
                time.sleep(0.05)
            self.assertEqual([n for n in os.listdir(d) if n.startswith('.levels-')], [])


class ParentTests(unittest.TestCase):
    """OpenRC stops only the synthesizer: the meter must end with it, or every restart leaves one more meter on the pipe."""

    def test_the_meter_ends_when_its_synthesizer_is_gone(self):
        import subprocess
        import sys

        with tempfile.TemporaryDirectory() as d:
            pipe, out = os.path.join(d, 'pcm'), os.path.join(d, 'levels.json')
            os.mkfifo(pipe)
            synth = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])
            writer = os.open(pipe, os.O_RDWR)  # what the synthesizer holds
            m = subprocess.Popen([sys.executable, os.path.join(HERE, '..', 'pi', 'exxpiano-meter.py'), pipe, out, str(synth.pid)])
            time.sleep(0.6)
            self.assertIsNone(m.poll())  # it runs while the synthesizer runs
            synth.kill()
            synth.wait()
            os.close(writer)  # the synthesizer's end of the pipe goes with it
            self.assertEqual(m.wait(timeout=10), 0)

    def test_without_a_parent_it_keeps_running_as_before(self):
        self.assertTrue(meter.alive(None))
        self.assertTrue(meter.alive(os.getpid()))
        self.assertFalse(meter.alive(2**22 + 12345))


if __name__ == '__main__':
    unittest.main()
