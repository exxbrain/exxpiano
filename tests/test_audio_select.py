"""Tests for the automatic audio output choice in pi/exxpiano-run (a fake /proc/asound, no hardware needed).

Run from the repository root:   python3 -m unittest discover -s tests -v
"""
import os
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
RUN = os.path.join(ROOT, 'pi', 'exxpiano-run')

HEADPHONES = (0, 'Headphones', 'bcm2835_headphon - bcm2835 Headphones', False)
HDMI = (1, 'vc4hdmi', 'vc4-hdmi - vc4-hdmi', False)


def usb(num, card_id, name='USB-Audio - Some card', playback=True):
    return (num, card_id, name, True, playback)


def keyboard(num, card_id='mk3'):
    """A MIDI keyboard that shows up as a capture-only USB audio card (seen on a real Pi)."""
    return usb(num, card_id, 'USB-Audio - KL Essential 49 mk3', playback=False)


class AudioSelectTests(unittest.TestCase):
    def run_script(self, cards, **env):
        with tempfile.TemporaryDirectory() as asound:
            lines = []
            for card in cards:
                num, card_id, desc, is_usb = card[:4]
                playback = card[4] if len(card) > 4 else True
                lines.append('%2d [%-15s]: %s' % (num, card_id, desc))
                lines.append('                      %s' % desc)
                os.makedirs(os.path.join(asound, 'card%d' % num, 'pcm0p' if playback else 'pcm0c'))
                if is_usb:
                    with open(os.path.join(asound, 'card%d' % num, 'usbid'), 'w') as f:
                        f.write('1686:0090\n')
            with open(os.path.join(asound, 'cards'), 'w') as f:
                f.write('\n'.join(lines) + '\n')
            e = dict(os.environ, ASOUND_DIR=asound, DRY_RUN='1', USB_WAIT='0',
                     PIANO_SF2='/p.sf2', MECH_SF2='/m.sf2', FLUIDSYNTH='/usr/bin/fluidsynth')
            e.update({k: str(v) for k, v in env.items()})
            r = subprocess.run(['bash', RUN], env=e, capture_output=True, text=True)
            return r

    def test_a_bank_that_does_not_fit_the_memory_is_replaced_by_a_smaller_one(self):
        sys.path.insert(0, os.path.join(ROOT, 'tests'))
        from sf2fixture import make_sf2
        with tempfile.TemporaryDirectory() as d:
            big = make_sf2(os.path.join(d, 'big.sf2'), presets=[('Big', 0, 0)], name='Big', sample_bytes=8 << 20)
            small = make_sf2(os.path.join(d, 'small.sf2'), presets=[('Small', 0, 0)], name='Small', sample_bytes=1 << 20)
            mech = make_sf2(os.path.join(d, 'mech.sf2'), presets=[('Hammer', 0, 1)], name='Mech', sample_bytes=1000)
            meminfo = os.path.join(d, 'meminfo')
            with open(meminfo, 'w') as f:
                f.write('MemTotal:       %d kB\n' % (8 * 1024))        # 8 MB: the library has to leave 28 %
            conf = os.path.join(d, 'exxpiano.conf')
            with open(conf, 'w') as f:
                f.write('PIANO_SF2=%s\nGAIN=1.0\n' % big)
            env = dict(PIANO_SF2=big, MECH_SF2=mech, BANK_DIR=d, EXXPIANO_MEMINFO=meminfo, EXXPIANO_CONF=conf)
            r = self.run_script([HEADPHONES], **env)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn('does not fit into the memory', r.stderr)
            self.assertIn(small, r.stdout)
            with open(conf) as f:
                self.assertEqual(f.read(), 'PIANO_SF2=%s\nGAIN=1.0\n' % small)         # remembered
            with open(meminfo, 'w') as f:
                f.write('MemTotal:       %d kB\n' % (900 * 1024))        # a big board: nothing changes
            with open(conf, 'w') as f:
                f.write('PIANO_SF2=%s\n' % big)
            r = self.run_script([HEADPHONES], **env)
            self.assertNotIn('does not fit', r.stderr)
            self.assertIn(big, r.stdout)

    def timed(self, cards, sysfs_interfaces, uptime, **env):
        """Run the script against a fake /sys/bus/usb/devices (interface name -> driver bound or not) and a fake uptime; -> (result, seconds)."""
        with tempfile.TemporaryDirectory() as d:
            sysfs = os.path.join(d, 'usb')
            os.makedirs(sysfs)
            for name, bound in sysfs_interfaces.items():
                os.makedirs(os.path.join(sysfs, name))
                with open(os.path.join(sysfs, name, 'bInterfaceClass'), 'w') as f:
                    f.write('01\n')
                if bound:
                    os.symlink(d, os.path.join(sysfs, name, 'driver'))
            up = os.path.join(d, 'uptime')
            with open(up, 'w') as f:
                f.write('%s 100.00\n' % uptime)
            start = time.monotonic()
            r = self.run_script(cards, USB_SYSFS=sysfs, PROC_UPTIME=up, **env)
            return r, time.monotonic() - start

    def test_nothing_is_waited_for_when_no_usb_audio_device_is_coming(self):
        r, seconds = self.timed([HEADPHONES], {}, 500, USB_WAIT=4)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('plughw:CARD=Headphones', r.stdout)
        self.assertLess(seconds, 2.5)
        self.assertNotIn('waited', r.stderr)

    def test_a_usb_keyboard_that_is_a_capture_only_audio_card_does_not_hold_the_start_back(self):
        r, seconds = self.timed([HEADPHONES, keyboard(1)], {'1-1:1.0': True}, 500, USB_WAIT=4)      # its driver is bound already
        self.assertIn('plughw:CARD=Headphones', r.stdout)
        self.assertLess(seconds, 2.5)

    def test_it_waits_for_a_usb_audio_device_that_is_still_waiting_for_its_driver(self):
        r, seconds = self.timed([HEADPHONES], {'1-1:1.0': False}, 500, USB_WAIT=2)
        self.assertIn('plughw:CARD=Headphones', r.stdout)                              # no card came: the jack
        self.assertGreaterEqual(seconds, 1.9)
        self.assertIn('waited 2 s', r.stderr)

    def test_right_after_the_boot_it_still_gives_the_usb_devices_a_moment(self):
        r, seconds = self.timed([HEADPHONES], {}, 1, USB_WAIT=2, USB_SETTLE=12)
        self.assertGreaterEqual(seconds, 1.9)
        self.assertIn('waited 2 s', r.stderr)

    def test_only_onboard_uses_its_own_buffer_settings(self):
        r = self.run_script([HEADPHONES])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('audio.alsa.device=plughw:CARD=Headphones', r.stdout)
        self.assertIn('-z 64 -c 3', r.stdout)

    def test_usb_card_is_preferred_and_uses_small_buffer(self):
        r = self.run_script([HEADPHONES, usb(1, 'U24')])
        self.assertIn('audio.alsa.device=plughw:CARD=U24', r.stdout)
        self.assertIn('-z 64 -c 3', r.stdout)

    def test_hdmi_is_never_chosen_over_onboard(self):
        r = self.run_script([HDMI, HEADPHONES])
        self.assertIn('plughw:CARD=Headphones', r.stdout)

    def test_hdmi_only_is_not_used_either(self):
        r = self.run_script([HDMI])
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('no sound card', r.stderr)

    def test_explicit_device_wins(self):
        r = self.run_script([HEADPHONES, usb(1, 'U24')], ALSA_DEVICE='plughw:Headphones', PERIOD_SIZE=512)
        self.assertIn('audio.alsa.device=plughw:Headphones', r.stdout)
        self.assertIn('-z 512 -c 3', r.stdout)

    def test_usb_buffer_is_configurable(self):
        r = self.run_script([usb(2, 'Interface')], USB_PERIOD_SIZE=128, USB_PERIODS=4)
        self.assertIn('plughw:CARD=Interface', r.stdout)
        self.assertIn('-z 128 -c 4', r.stdout)

    def test_banks_and_options_reach_fluidsynth(self):
        r = self.run_script([usb(1, 'U24')], GAIN='2.0', POLYPHONY=64, REVERB=1)
        self.assertNotIn(' -s ', r.stdout)               # no server mode: its control port listens on every interface
        self.assertNotIn(' -is ', r.stdout)
        self.assertNotIn('shell.', r.stdout)
        self.assertIn('-g 2.0', r.stdout)
        self.assertIn('synth.polyphony=64', r.stdout)
        self.assertIn('-R 1', r.stdout)
        self.assertTrue(r.stdout.strip().endswith('/p.sf2 /m.sf2'))

    def test_the_output_goes_through_the_level_meter_only_when_it_can(self):
        with tempfile.TemporaryDirectory() as d:
            conf = os.path.join(d, 'tap.conf'); prog = os.path.join(d, 'meter.py')
            open(conf, 'w').close(); open(prog, 'w').close()
            on = self.run_script([HEADPHONES], ALSA_TAP_CONF=conf, METER_PROGRAM=prog)
            self.assertIn("audio.alsa.device=exxpiano_tap:'plughw:CARD=Headphones", on.stdout)
            for why, env in (('switched off', dict(ALSA_TAP_CONF=conf, METER_PROGRAM=prog, METER=0)), ('no ALSA file', dict(ALSA_TAP_CONF=os.path.join(d, 'none'), METER_PROGRAM=prog)),
                             ('no program', dict(ALSA_TAP_CONF=conf, METER_PROGRAM=os.path.join(d, 'none')))):
                with self.subTest(why):
                    off = self.run_script([HEADPHONES], **env)
                    self.assertIn('audio.alsa.device=plughw:CARD=Headphones', off.stdout)
                    self.assertNotIn('exxpiano_tap', off.stdout)

    def test_the_defaults_are_192_voices_on_3_threads(self):
        r = self.run_script([HEADPHONES], CPU_COUNT=4)
        self.assertIn('synth.polyphony=192', r.stdout)
        self.assertIn('synth.cpu-cores=3', r.stdout)

    def test_the_synthesis_threads_are_shared_between_cores_and_one_core_stays_free(self):
        for count, asked, want in ((4, None, 3), (4, 2, 2), (4, 4, 3), (4, 1, 1), (2, None, 1), (1, None, 1), (8, 3, 3), (3, None, 2)):
            with self.subTest(cores=count, asked=asked):
                env = dict(CPU_COUNT=count)
                if asked is not None:
                    env['CPU_CORES'] = asked
                r = self.run_script([usb(1, 'U24')], **env)
                self.assertIn('synth.cpu-cores=%d' % want, r.stdout)

    def test_usb_card_without_playback_is_ignored(self):
        # regression: the keyboard's own USB audio card (capture only) was chosen instead of the jack
        r = self.run_script([HEADPHONES, keyboard(1)])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('plughw:CARD=Headphones', r.stdout)
        self.assertIn('-z 64 -c 3', r.stdout)

    def test_interface_wins_over_keyboard_audio(self):
        r = self.run_script([HEADPHONES, keyboard(1), usb(2, 'U24')])
        self.assertIn('plughw:CARD=U24', r.stdout)

    def test_period_below_fluidsynth_minimum_is_clamped(self):
        # regression: USB_PERIOD_SIZE=32 made FluidSynth exit ("audio.period-size out of range"), so no sound at all
        r = self.run_script([usb(1, 'U24')], USB_PERIOD_SIZE=32)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('-z 64 -c 3', r.stdout)
        self.assertIn('using 64', r.stderr)

    def test_period_above_maximum_is_clamped(self):
        r = self.run_script([HEADPHONES], PERIOD_SIZE=100000)
        self.assertIn('-z 8192 ', r.stdout)

    def test_first_usb_card_wins(self):
        r = self.run_script([usb(1, 'First'), usb(2, 'Second')])
        self.assertIn('plughw:CARD=First', r.stdout)


class LastGoodBankTests(unittest.TestCase):
    """A missing or damaged piano bank must fall back to the last good one instead of leaving the module silent."""

    def setUp(self):
        import sys
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from sf2fixture import make_sf2
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.good = make_sf2(os.path.join(self.tmp.name, 'good.sf2'))
        self.other = make_sf2(os.path.join(self.tmp.name, 'other.sf2'))
        self.broken = os.path.join(self.tmp.name, 'broken.sf2')
        with open(self.broken, 'wb') as f:
            f.write(b'half a copy')
        self.asound = os.path.join(self.tmp.name, 'asound')
        os.makedirs(os.path.join(self.asound, 'card0', 'pcm0p'))
        with open(os.path.join(self.asound, 'cards'), 'w') as f:
            f.write(' 0 [Headphones     ]: bcm2835_headpho - bcm2835 Headphones\n')

    def run_script(self, piano, last_good=''):
        env = dict(os.environ, ASOUND_DIR=self.asound, DRY_RUN='1', USB_WAIT='0', PIANO_SF2=piano, MECH_SF2='/m.sf2',
                   PIANO_SF2_LAST_GOOD=last_good, FLUIDSYNTH='/usr/bin/fluidsynth')
        return subprocess.run(['bash', RUN], env=env, capture_output=True, text=True)

    def test_a_good_bank_is_used_as_it_is(self):
        r = self.run_script(self.other, self.good)
        self.assertTrue(r.stdout.strip().endswith('%s /m.sf2' % self.other), r.stdout + r.stderr)

    def test_a_damaged_bank_falls_back_to_the_last_good_one(self):
        r = self.run_script(self.broken, self.good)
        self.assertTrue(r.stdout.strip().endswith('%s /m.sf2' % self.good), r.stdout + r.stderr)
        self.assertIn('using the last good bank', r.stderr)

    def test_a_missing_bank_falls_back_too(self):
        r = self.run_script(os.path.join(self.tmp.name, 'gone.sf2'), self.good)
        self.assertTrue(r.stdout.strip().endswith('%s /m.sf2' % self.good), r.stdout + r.stderr)

    def test_without_a_good_fallback_the_configured_bank_is_kept(self):
        r = self.run_script(self.broken, '')
        self.assertTrue(r.stdout.strip().endswith('%s /m.sf2' % self.broken))
        r = self.run_script(self.broken, self.broken)                  # the "last good" one is damaged as well
        self.assertTrue(r.stdout.strip().endswith('%s /m.sf2' % self.broken))


class PipePlumbingTests(unittest.TestCase):
    """FluidSynth must keep running on a root-only pipe, and commands written into the pipe must reach it."""

    def test_fluidsynth_reads_its_stdin_from_the_pipe(self):
        import stat
        import time
        with tempfile.TemporaryDirectory() as tmp:
            fake = os.path.join(tmp, 'fake-fluidsynth')
            got = os.path.join(tmp, 'got.txt')
            with open(fake, 'w') as f:
                f.write('#!/bin/bash\nread -r line\necho "$line" > "%s"\n' % got)
            os.chmod(fake, 0o755)
            asound = os.path.join(tmp, 'asound')
            os.makedirs(os.path.join(asound, 'card0', 'pcm0p'))
            with open(os.path.join(asound, 'cards'), 'w') as f:
                f.write(' 0 [Headphones     ]: bcm2835_headpho - bcm2835 Headphones\n')
            fifo = os.path.join(tmp, 'cmd')
            env = dict(os.environ, ASOUND_DIR=asound, USB_WAIT='0', PIANO_SF2='/p', MECH_SF2='/m',
                       FLUIDSYNTH=fake, CMD_FIFO=fifo)
            proc = subprocess.Popen(['bash', RUN], env=env, stderr=subprocess.DEVNULL)
            try:
                for _ in range(50):                              # wait for the pipe to appear
                    if os.path.exists(fifo):
                        break
                    time.sleep(0.1)
                self.assertTrue(stat.S_ISFIFO(os.stat(fifo).st_mode))
                self.assertEqual(oct(os.stat(fifo).st_mode & 0o777), oct(0o600))       # root only
                fd = os.open(fifo, os.O_WRONLY | os.O_NONBLOCK)
                os.write(fd, b'set synth.gain 1.2\n')
                os.close(fd)
                proc.wait(timeout=5)
                with open(got) as f:
                    self.assertEqual(f.read().strip(), 'set synth.gain 1.2')
            finally:
                if proc.poll() is None:
                    proc.kill()



class MeterLoopTests(unittest.TestCase):
    def test_a_crashed_meter_is_started_again_and_the_loop_ends_with_the_synthesizer(self):
        """The loop is run as exxpiano-run runs it (set -euo pipefail): a meter that fails is started again, and the loop stops when the
        synthesizer is gone. A loop that ended would leave the pipe unread and the synthesizer waiting on it."""
        import re
        import subprocess
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'pi', 'exxpiano-run')) as f:
            text = f.read()
        loop = re.search(r'^  \( exec 6<&-; while .*\) &$', text, re.M).group(0).strip()
        with tempfile.TemporaryDirectory() as d:
            prog, count = os.path.join(d, 'meter.sh'), os.path.join(d, 'starts')
            with open(prog, 'w') as f:
                f.write('echo x >> "%s"; exit 1\n' % count)          # a meter that fails at once, every time
            # the stand-in synthesizer is a child of the same shell, which reaps it (a zombie would still answer kill -0)
            script = 'set -euo pipefail\nsleep 3 &\nSYNTH_PID=$!\n%s\nwait\n' % loop.replace('python3 "$METER_PROGRAM"', 'bash "$METER_PROGRAM"')
            env = dict(os.environ, METER_PROGRAM=prog, METER_DIR=d)
            r = subprocess.run(['bash', '-c', script], env=env, timeout=20)
            with open(count) as f:
                starts = len(f.read().split())
        self.assertEqual(r.returncode, 0)
        self.assertGreater(starts, 3)                                    # started again and again while the synthesizer lived, then ended


if __name__ == '__main__':
    unittest.main()
