"""pi/exxpiano-respawn: a program that fails is started again, one that ends well is not (Restart=on-failure under OpenRC)."""
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest

WRAPPER = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'pi', 'exxpiano-respawn')
# counts its starts in a file and fails until the given start
PROGRAM = 'import sys; p = sys.argv[1]; n = int(open(p).read() or 0) + 1 if __import__("os").path.exists(p) else 1; open(p, "w").write(str(n)); sys.exit(0 if n >= int(sys.argv[2]) else 1)'


class RespawnTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.count = os.path.join(self.tmp.name, 'count')

    def tearDown(self):
        self.tmp.cleanup()

    def starts(self):
        with open(self.count) as f:
            return int(f.read())

    def test_a_clean_end_is_not_restarted(self):
        r = subprocess.run(['bash', WRAPPER, '--delay', '0', '--', sys.executable, '-c', PROGRAM, self.count, '1'], timeout=20)
        self.assertEqual(r.returncode, 0)
        self.assertEqual(self.starts(), 1)

    def test_a_failure_is_restarted_until_it_ends_well(self):
        r = subprocess.run(['bash', WRAPPER, '--delay', '0', '--', sys.executable, '-c', PROGRAM, self.count, '3'],
                           capture_output=True, text=True, timeout=20)
        self.assertEqual(r.returncode, 0)
        self.assertEqual(self.starts(), 3)
        self.assertEqual(r.stderr.count('ended with code 1'), 2)

    def test_a_stop_ends_the_program_and_the_wrapper(self):
        p = subprocess.Popen(['bash', WRAPPER, '--', 'sleep', '30'])
        time.sleep(0.5)
        p.send_signal(signal.SIGTERM)
        self.assertEqual(p.wait(timeout=10), 0)


if __name__ == '__main__':
    unittest.main()
