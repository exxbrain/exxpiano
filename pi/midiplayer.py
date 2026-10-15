"""The player of imported MIDI files for the bridge (standard library only: the bridge gives it the way to the synthesizer).

The web service asks for things through /run/exxpiano-midi/request.json (play an id of the library, with repeat, move to a position of the piece that plays, or stop; every request has a new
`nonce`) and reads /run/exxpiano-midi/status.json. The player never sees a path from outside: it takes an id and looks it up in the library.
The notes go through the same `on_event` as a keyboard's notes, so the touch curve, the piano and the mechanics treat them alike; nothing in the
file (programs, banks, volume, drums) reaches the synthesizer, because midilib keeps only notes and the sustain pedal.

`step()` does one small piece of work using the clock it is given, so tests drive it with a fake clock; `run()` is the loop of the real thing.
"""

import bisect
import json
import os
import tempfile
import time

import midilib

RUN_DIR = '/run/exxpiano-midi'
POLL = 0.25  # seconds between looks at the request file
STATUS_PLAYING = 0.5  # seconds between status files while playing
STATUS_IDLE = 2.0  # and while idle: the web interface counts a status older than 5 s as "the player is not running"
LOOP_GAP = 0.8  # a pause before a repeated piece starts again
FILE_PEDAL_DOWN = 40  # a sustain value of a file at or above this is "down": the bridge plays the half pedal of 40-63 as down (exxpiano-fx.py)


class Player:
    def __init__(self, library_dir, run_dir, on_event, release_all, port_id, clock=time.monotonic, wall=time.time):
        """on_event(kind, a, b): 'on' (note, velocity), 'off' (note, 0), 'pedal' (value, 0). release_all(pedal_was_down): silence everything.
        port_id(): something that changes when the synthesizer is another one (None while there is none)."""
        self.library_dir, self.run_dir = library_dir, run_dir
        self.request_path = os.path.join(run_dir, 'request.json')
        self.status_path = os.path.join(run_dir, 'status.json')
        self.on_event, self.release_all, self.port_id, self.clock, self.wall = on_event, release_all, port_id, clock, wall
        self.playing = False
        self.midi_id = self.title = self.reason = None
        self.loop = False
        self.length = 0.0
        self._events, self._i, self._t0 = [], 0, 0.0
        self._pedal_down = False
        self._port = None
        self._nonce = None
        self._next_poll = self._next_status = 0.0

    # ---- one step of work
    def step(self):
        now = self.clock()
        if now >= self._next_poll:
            self._next_poll = now + POLL
            self._poll(now)
        if self.playing:
            self._advance(now)
        if now >= self._next_status:
            self._publish(now)

    def run(self):
        while True:
            self.step()
            time.sleep(self._pause())

    def _pause(self):
        if not self.playing or self._i >= len(self._events):
            return 0.1 if not self.playing else 0.02
        wait = self._t0 + self._events[self._i][0] - self.clock()
        return min(0.02, max(0.002, wait))

    # ---- requests
    def _poll(self, now):
        try:
            with open(self.request_path, encoding='utf-8') as f:
                req = json.load(f)
            nonce, op = req.get('nonce'), req.get('op')
        except (OSError, ValueError, AttributeError):
            return
        if nonce is None or nonce == self._nonce:
            return
        self._nonce = nonce
        if op == 'stop':
            self.stop('stopped')
        elif op == 'play':
            self._start(req.get('id'), bool(req.get('loop')), now)
        elif op == 'seek':
            self.seek(req.get('id'), req.get('position'), now)
        else:
            self._publish(now)

    def _start(self, midi_id, loop, now):
        if self.playing:
            self.stop('another file was started', publish=False)
        try:
            path = midilib.resolve(self.library_dir, midi_id)
            parsed = midilib.inspect(path)
        except (ValueError, midilib.MidiError) as exc:
            self.reason = 'cannot play: %s' % exc
            self.midi_id = None
            self._publish(now)
            return
        port = self.port_id()
        if port is None:
            self.reason = 'the synthesizer is not running'
            self.midi_id = None
            self._publish(now)
            return
        _, meta = midilib._entry(self.library_dir, midi_id)  # the name the library shows, not the title inside the file (often empty)
        title = (meta or {}).get('name') or parsed['title']
        self.midi_id, self.title, self.loop, self.length = midi_id, title, loop, parsed['length']
        self._events, self._i, self._t0, self._port, self._pedal_down = parsed['events'], 0, now, port, False
        self.playing, self.reason = True, None
        self._publish(now)

    def seek(self, midi_id, position, now):
        """Move to `position` seconds of the piece that plays. A late request for a piece that has ended or been replaced is ignored.
        Notes that sound at that moment are let go (they began before the new position); the sustain pedal gets the state the file has there."""
        if not self.playing or midi_id != self.midi_id or isinstance(position, bool) or not isinstance(position, (int, float)) or position != position:
            return
        position = min(max(float(position), 0.0), self.length)
        self.release_all(self._pedal_down)
        self._pedal_down = False
        self._i = bisect.bisect_left(self._events, (position,))  # the first event at or after the position
        for _, kind, a, b in reversed(self._events[: self._i]):
            if kind == 'pedal':
                if a >= FILE_PEDAL_DOWN:
                    self._pedal_down = True
                    self.on_event(kind, a, b)
                break
        self._t0 = now - position
        self._publish(now)

    def stop(self, reason, publish=True):
        was_playing = self.playing
        self.playing = False
        self.reason = reason
        if was_playing:
            self.release_all(self._pedal_down)
        self._pedal_down = False
        if publish:
            self._publish(self.clock())

    # ---- playing
    def _advance(self, now):
        if self.port_id() != self._port:
            self.stop('the synthesizer was restarted')
            return
        elapsed = now - self._t0
        events = self._events
        while self._i < len(events) and events[self._i][0] <= elapsed:
            _, kind, a, b = events[self._i]
            self._i += 1
            if kind == 'pedal':
                self._pedal_down = a >= FILE_PEDAL_DOWN
            self.on_event(kind, a, b)
        if self._i >= len(events) and elapsed >= self.length:
            if self.loop:
                self.release_all(self._pedal_down)
                self._pedal_down = False
                self._i, self._t0 = 0, now + LOOP_GAP
            else:
                self.stop('finished')

    # ---- status
    def position(self):
        return max(0.0, min(self.length, self.clock() - self._t0)) if self.playing else 0.0

    def _publish(self, now):
        status = dict(
            state='playing' if self.playing else 'idle',
            id=self.midi_id,
            title=self.title,
            position=round(self.position(), 1),
            length=round(self.length, 1),
            loop=self.loop,
            reason=self.reason,
            nonce=self._nonce,
            updated=int(self.wall()),
        )
        try:
            os.makedirs(self.run_dir, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=self.run_dir, prefix='.status-')
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                json.dump(status, f, ensure_ascii=False, sort_keys=True)
            os.chmod(tmp, 0o644)
            os.replace(tmp, self.status_path)
        except OSError:
            pass  # the next try is half a second away
        self._next_status = now + (STATUS_PLAYING if self.playing else STATUS_IDLE)
