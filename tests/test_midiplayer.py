"""The player of imported MIDI files: the schedule, stopping, repeat, the requests, and that a file's notes take a keyboard's road.

Time is a fake clock and the synthesizer a recorder: nothing sleeps, nothing is heard. How exact the timing is on a real Pi is a separate check.
Run from the repository root:   python3 -m unittest discover -s tests -v
"""
import importlib.util
import json
import os
import random
import sys
import tempfile
import threading
import unittest
from types import SimpleNamespace

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'pi'))
sys.path.insert(0, HERE)
import midilib  # noqa: E402
import midiplayer  # noqa: E402
from midifixture import make_midi  # noqa: E402

spec = importlib.util.spec_from_file_location('piano_fx_for_midi', os.path.join(HERE, '..', 'pi', 'exxpiano-fx.py'))
fx = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fx)


class Rig:
    """A player with a library of one piece, a fake clock and a recorder."""

    def __init__(self, test, notes=((0, 60, 80, 1), (1, 64, 100, 1)), pedal=((0, 127), (2, 0)), **kw):
        self.tmp = tempfile.TemporaryDirectory()
        test.addCleanup(self.tmp.cleanup)
        self.lib, self.run_dir = os.path.join(self.tmp.name, 'midi'), os.path.join(self.tmp.name, 'run')
        os.makedirs(self.run_dir)
        src = make_midi(os.path.join(self.tmp.name, 'a.mid'), list(notes), pedal=list(pedal), **kw)
        self.id, _ = midilib.import_midi(src, 'a.mid', self.lib)
        self.now = 1000.0
        self.port = 'FLUID Synth 1'
        self.events, self.released = [], []
        self.player = midiplayer.Player(self.lib, self.run_dir, lambda *e: self.events.append((round(self.now - self.t0, 2),) + e),
                                        lambda pedal: self.released.append(pedal), lambda: self.port, clock=lambda: self.now, wall=lambda: 1.7e9)
        self.t0 = self.now
        self.nonce = 0

    def ask(self, **req):
        self.nonce += 1
        with open(os.path.join(self.run_dir, 'request.json'), 'w') as f:
            json.dump(dict(req, nonce=self.nonce), f)

    def advance(self, seconds, step=0.05):
        end = self.now + seconds
        while self.now < end - 1e-9:
            self.now = min(end, self.now + step)
            self.player.step()

    def status(self):
        with open(os.path.join(self.run_dir, 'status.json')) as f:
            return json.load(f)


class PlayerTests(unittest.TestCase):
    def test_a_request_starts_the_piece_and_every_event_comes_at_its_time_in_order(self):
        r = Rig(self)
        r.player.step()
        self.assertEqual(r.status()['state'], 'idle')
        r.ask(op='play', id=r.id)
        r.t0 = r.now
        r.advance(0.3)                                                    # the request is seen within a quarter of a second
        self.assertEqual(r.status()['state'], 'playing')
        r.advance(1.5)
        times = [(round(t, 1), k, a) for t, k, a, _ in r.events]
        self.assertEqual([(k, a) for _, k, a in times], [('pedal', 127), ('on', 60), ('off', 60), ('on', 64), ('off', 64), ('pedal', 0)])
        self.assertEqual([t for t, _, _ in times], sorted(t for t, _, _ in times))
        for (t, k, a), want in zip(times, (0.0, 0.0, 0.5, 0.5, 1.0, 1.0)):
            self.assertAlmostEqual(t, want + (times[0][0]), delta=0.2)    # the player starts when it saw the request, then keeps the time of the file
        self.assertEqual(r.status()['state'], 'idle')
        self.assertEqual(r.status()['reason'], 'finished')
        self.assertEqual(r.released, [False])                             # the pedal was up at the end

    def test_the_position_grows_and_the_status_names_the_piece(self):
        r = Rig(self, notes=[(0, 60, 80, 8)], pedal=[], title='Etude')
        r.ask(op='play', id=r.id)
        r.advance(1.2)
        s1 = r.status()
        r.advance(1.2)
        s2 = r.status()
        self.assertEqual((s1['state'], s1['title'], s1['id'], s1['length']), ('playing', 'Etude', r.id, 4.0))
        self.assertGreater(s2['position'], s1['position'])

    def test_seek_jumps_to_the_position_keeps_the_pedal_of_the_file_and_plays_on_from_there(self):
        r = Rig(self, notes=[(0, 60, 80, 1), (2, 62, 80, 1), (4, 64, 80, 1), (6, 66, 80, 1)], pedal=[(0, 127), (10, 0)])
        r.ask(op='play', id=r.id)
        r.advance(0.6)
        r.events.clear()
        r.ask(op='seek', id=r.id, position=2.5)                           # the notes at 0 s, 1 s and 2 s are behind
        r.advance(0.4)
        self.assertEqual(r.released, [True])                              # whatever sounded is let go, the pedal included
        self.assertEqual(r.events[0][1:], ('pedal', 127, 0))              # the file has the pedal down there: it is pressed again
        s = r.status()
        self.assertEqual(s['state'], 'playing')
        self.assertAlmostEqual(s['position'], 2.7, delta=0.45)
        r.events.clear()
        r.advance(1.0)
        self.assertEqual([(k, a) for _, k, a, _ in r.events], [('on', 66), ('off', 66)])          # the note at 3 s comes at its time
        r.ask(op='seek', id=r.id, position=0)                             # back to the beginning: the first note sounds again
        r.events.clear()
        r.advance(0.4)
        self.assertIn(('on', 60), [(k, a) for _, k, a, _ in r.events])

    def test_seek_after_the_last_note_and_to_the_end_finishes_the_piece(self):
        r = Rig(self)
        r.ask(op='play', id=r.id)
        r.advance(0.3)
        r.ask(op='seek', id=r.id, position=999)
        r.advance(0.6)
        self.assertEqual((r.status()['state'], r.status()['reason']), ('idle', 'finished'))

    def test_a_seek_for_nothing_that_plays_or_with_nonsense_is_ignored(self):
        r = Rig(self, notes=[(0, 60, 80, 10)], pedal=[])
        r.ask(op='seek', id=r.id, position=1)                             # nothing plays
        r.advance(0.5)
        self.assertEqual(r.status()['state'], 'idle')
        r.ask(op='play', id=r.id)
        r.advance(0.3)
        for bad in ('1', None, True, float('nan'), [1]):
            r.ask(op='seek', id=r.id, position=bad)
            r.advance(0.3)
        r.ask(op='seek', id='m-0000000000000000', position=1.0)           # another piece than the one that plays
        r.advance(0.3)
        self.assertEqual(r.status()['state'], 'playing')
        self.assertLess(r.status()['position'], 1.8)

    def test_the_status_names_the_piece_as_the_library_does(self):
        r = Rig(self, notes=[(0, 60, 80, 8)], pedal=[], title='control track')                 # a title nobody wants to read
        r.ask(op='play', id=r.id)
        r.advance(0.6)
        self.assertEqual(r.status()['title'], 'a')                                              # the library names it after the file

    def test_stop_in_the_middle_silences_everything_and_lets_the_pedal_go(self):
        r = Rig(self, pedal=[(0, 127), (4, 0)])
        r.ask(op='play', id=r.id)
        r.advance(0.6)
        r.ask(op='stop')
        r.advance(0.4)
        self.assertEqual(r.status()['state'], 'idle')
        self.assertEqual(r.status()['reason'], 'stopped')
        self.assertEqual(r.released, [True])                              # the pedal was down when it stopped
        count = len(r.events)
        r.advance(3)
        self.assertEqual(len(r.events), count)                            # nothing more is sent

    def test_repeat_starts_again_after_a_short_gap_until_stopped(self):
        r = Rig(self)
        r.ask(op='play', id=r.id, loop=True)
        r.advance(6)
        self.assertEqual(r.status()['state'], 'playing')
        self.assertGreater(sum(1 for e in r.events if e[1] == 'on' and e[2] == 60), 2)
        self.assertTrue(r.status()['loop'])
        r.ask(op='stop')
        r.advance(0.5)
        self.assertEqual(r.status()['state'], 'idle')

    def test_a_restarted_synthesizer_stops_the_piece_with_the_reason(self):
        r = Rig(self, pedal=[(0, 127), (4, 0)])
        r.ask(op='play', id=r.id)
        r.advance(0.6)
        r.port = None
        r.advance(0.3)
        r.port = 'FLUID Synth 2'
        r.advance(0.5)
        s = r.status()
        self.assertEqual((s['state'], s['reason']), ('idle', 'the synthesizer was restarted'))
        self.assertEqual(r.released, [True])

    def test_requests_that_cannot_be_served_are_explained_and_a_request_is_handled_once(self):
        r = Rig(self)
        r.ask(op='play', id='m-0000000000000000')
        r.advance(0.4)
        self.assertEqual(r.status()['state'], 'idle')
        self.assertIn('cannot play', r.status()['reason'])
        r.port = None
        r.ask(op='play', id=r.id)
        r.advance(0.4)
        self.assertEqual(r.status()['reason'], 'the synthesizer is not running')
        r.port = 'FLUID Synth 1'
        r.advance(2)
        self.assertEqual(r.status()['state'], 'idle')                     # the same request is not served again
        with open(os.path.join(r.run_dir, 'request.json'), 'w') as f:
            f.write('{broken')
        r.advance(1)
        self.assertEqual(r.status()['state'], 'idle')                     # a damaged request file is ignored
        r.ask(op='play', id=r.id)                                         # a new nonce plays it
        r.advance(0.4)
        self.assertEqual(r.status()['state'], 'playing')

    def test_another_piece_replaces_the_one_that_plays(self):
        r = Rig(self, pedal=[(0, 127), (8, 0)])
        other = make_midi(os.path.join(r.tmp.name, 'b.mid'), [(0, 72, 90, 1)])
        other_id, _ = midilib.import_midi(other, 'b.mid', r.lib)
        r.ask(op='play', id=r.id)
        r.advance(0.6)
        r.ask(op='play', id=other_id)
        r.advance(0.4)
        self.assertEqual(r.status()['id'], other_id)
        self.assertEqual(r.released, [True])                              # the first piece was silenced before the next began

    def test_the_status_is_a_valid_file_with_a_fresh_stamp(self):
        r = Rig(self)
        r.player.step()
        s = r.status()
        self.assertEqual((s['state'], s['updated']), ('idle', 1700000000))


class Out:
    def __init__(self):
        self.sent = []

    def send(self, msg):
        self.sent.append(msg)


class Msg(SimpleNamespace):
    """What mido.Message offers the bridge: attributes and copy(**changes)."""

    def copy(self, **kw):
        return Msg(**dict(vars(self), **kw))


class FakeMido:
    @staticmethod
    def Message(kind, **kw):
        return Msg(type=kind, **kw)


def bridge():
    b = fx.Bridge.__new__(fx.Bridge)
    b.mido, b.lock, b.out, b.out_name = FakeMido(), threading.Lock(), Out(), 'FLUID Synth 1'
    b.core = fx.FXCore(randomness=0, rng=random.Random(1), velocity_curve=0.7)
    return b


def shape(m):
    return (m.type, getattr(m, 'channel', None), getattr(m, 'note', None), getattr(m, 'velocity', None), getattr(m, 'control', None), getattr(m, 'value', None))


class SameRoadTests(unittest.TestCase):
    """A file's notes reach the synthesizer and the mechanics exactly as a keyboard's notes do."""

    def test_a_played_note_gives_the_same_messages_as_the_same_note_from_a_keyboard(self):
        file_bridge, key_bridge = bridge(), bridge()
        file_bridge.play_event('on', 60, 50)
        file_bridge.play_event('off', 60, 0)
        key_bridge.on_msg(Msg(type='note_on', channel=0, note=60, velocity=50))                 # what a keyboard sends
        key_bridge.on_msg(Msg(type='note_off', channel=0, note=60, velocity=0))
        self.assertEqual([shape(m) for m in file_bridge.out.sent], [shape(m) for m in key_bridge.out.sent])
        self.assertGreater(len(file_bridge.out.sent), 2)                  # the piano's two messages and the mechanics' noise after them
        self.assertTrue(any(getattr(m, 'channel', 0) > 0 for m in file_bridge.out.sent))      # the mechanics did sound

    def test_the_touch_curve_applies_to_the_file(self):
        b = bridge()
        b.play_event('on', 60, 40)
        first = b.out.sent[0]
        self.assertEqual((first.type, first.channel, first.note), ('note_on', 0, 60))
        self.assertEqual(first.velocity, b.core.map_velocity(40))
        self.assertNotEqual(first.velocity, 40)

    def test_the_pedal_and_the_silence_after_a_file(self):
        b = bridge()
        b.play_event('pedal', 127, 0)
        b.play_event('on', 60, 80)
        b.release_all(True)
        controls = [(m.control, m.value) for m in b.out.sent if m.type == 'control_change' and m.channel == 0]
        self.assertEqual(controls, [(64, 127), (64, 0), (123, 0)])
        before = len(b.out.sent)
        b.release_all(False)
        self.assertEqual([(m.control, m.value) for m in b.out.sent[before:] if m.type == 'control_change' and m.channel == 0], [(123, 0)])


if __name__ == '__main__':
    unittest.main()
