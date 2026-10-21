#!/usr/bin/env python3
"""The level meter of the synthesizer's output: the peak of the left and the right channel and the samples that hit full scale (clipping).

exxpiano-run lets FluidSynth play through the ALSA device `exxpiano_tap` (see 60-exxpiano-tap.conf): a copy of everything that goes to the sound card
(16 bit, 2 channels, 44.1 kHz) is written into a pipe, and this program reads the pipe and publishes a small status file for the web interface.

  exxpiano-meter.py PIPE STATUS_FILE [SYNTH_PID]

With SYNTH_PID the meter ends when that process (the synthesizer exxpiano-run became) is gone. OpenRC's supervise-daemon stops only the process it
started, not the whole group as systemd did: without this every restart of the synthesizer left one more meter reading the same pipe (seen on a
Pi 3 B: three meters split the stream between them).

It must never be slower than the audio: while the pipe is not read, the synthesizer waits. That is why exxpiano-run starts it in a loop and
holds the pipe open itself; a read here takes microseconds (the peaks come from max/min on the whole block, not from a loop over samples).
"""

import json
import math
import os
import sys
import tempfile
import time

RATE = 44100
FULL_SCALE = 32768.0
SILENCE_DB = -90.0
PUBLISH = 0.1  # seconds between status files
HOLD_FALL = 20.0  # dB per second: how fast the held peak falls
READ = 16384
NAP = 0.05  # seconds between reads: the synthesizer writes a few hundred times a second, the pipe holds 0.37 s; fewer, larger reads cost much less (about 3% of one core of a Pi 3 in all)


def to_db(peak):
    return SILENCE_DB if peak <= 0 else max(SILENCE_DB, 20.0 * math.log10(peak / FULL_SCALE))


# magnitude of the high byte of a signed 16 bit sample, as a table: translate() and max() then work at C speed. Looping over the samples in Python costs
# about 10% of a core of a Pi 3 for a stream of 44.1 kHz stereo, this costs a fraction of it.
MAG = bytes(min(255, abs(h if h < 128 else h - 256)) for h in range(256))


class Meter:
    """Feed it the bytes of the stream (S16_LE, interleaved stereo) and it keeps the peaks and the samples at full scale.

    The peak is read from the high byte of the samples (a step of 256 of 32768: 0.07 dB near full scale, 1 dB at -30 dBFS); below -42 dBFS, where the
    high byte says nothing, from a thin sample of the exact values. A sample counts as clipped when it is within 0.06 dB of full scale (high byte 0x7F or
    0x80): a clipped sample is always among them, and a sample that is that close to the limit is as good as clipped."""

    def __init__(self):
        self.peak = [0, 0]  # since the last status
        self.hold = [SILENCE_DB, SILENCE_DB]
        self.clipped = [0, 0]  # since the start
        self.last_clip = None  # time of the last clipped sample
        self.last_status = None
        self.tail = b''

    def feed(self, data, now):
        data = self.tail + data
        usable = len(data) // 4 * 4
        self.tail = data[usable:]
        if not usable:
            return
        block = data[:usable]
        values = memoryview(block).cast('h')  # the exact 16 bit values, without a copy
        for c in (0, 1):
            hi = block[2 * c + 1 :: 4]  # the high byte of every sample of this channel
            top = max(hi.translate(MAG))
            if top >= 2:
                peak = min(32767, top * 256 + 128)
            else:  # quiet: the exact values of every 16th sample are enough for a level
                thin = values[c::32]
                peak = max(max(thin), -min(thin))
            self.peak[c] = max(self.peak[c], peak)
            hit = hi.count(0x7F) + hi.count(0x80)
            if hit:
                self.clipped[c] += hit
                self.last_clip = now

    def status(self, now, wall):
        """The numbers for the web interface; the peaks start again from here."""
        dt = 0.0 if self.last_status is None else now - self.last_status
        self.last_status = now
        out = {}
        for c, name in ((0, 'left'), (1, 'right')):
            level = to_db(self.peak[c])
            self.hold[c] = max(level, self.hold[c] - HOLD_FALL * dt)
            out[name] = dict(peak=round(level, 1), hold=round(self.hold[c], 1), clipped=self.clipped[c])
            self.peak[c] = 0
        return dict(
            t=round(wall, 2), rate=RATE, left=out['left'], right=out['right'], clip_ago=None if self.last_clip is None else round(now - self.last_clip, 1)
        )


def publish(path, status):
    folder = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(dir=folder, prefix='.levels-')
    with os.fdopen(fd, 'w', encoding='ascii') as f:
        json.dump(status, f)
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)


def alive(pid):
    if pid is None:
        return True
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def run(pipe, status_path, parent=None):
    meter = Meter()
    while alive(parent):
        try:
            # not blocking: with no writer left (the synthesizer is gone) a plain open would wait for ever
            fd = os.open(pipe, os.O_RDONLY | os.O_NONBLOCK)
            os.set_blocking(fd, True)
        except OSError:
            time.sleep(0.5)
            continue
        next_status = time.monotonic()
        while True:
            try:
                data = os.read(fd, READ)
            except OSError:
                break
            if not data:  # every writer is gone: wait for the next start of the synthesizer
                break
            if not alive(parent):
                break
            now = time.monotonic()
            meter.feed(data, now)
            if now >= next_status:
                next_status = now + PUBLISH
                try:
                    publish(status_path, meter.status(now, time.time()))
                except OSError:
                    pass
            time.sleep(NAP)
        os.close(fd)
        time.sleep(0.2)


if __name__ == '__main__':
    if len(sys.argv) not in (3, 4) or (len(sys.argv) == 4 and not sys.argv[3].isdigit()):
        print(__doc__)
        sys.exit(2)
    run(sys.argv[1], sys.argv[2], int(sys.argv[3]) if len(sys.argv) == 4 else None)
