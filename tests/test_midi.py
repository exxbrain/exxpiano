"""The MIDI file reader (bounded, standard library only) and the library of imported MIDI files.

Run from the repository root:   python3 -m unittest discover -s tests -v
"""
import os
import struct
import sys
import tempfile
import unittest
from types import SimpleNamespace

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'pi'))
sys.path.insert(0, HERE)
import bankimport  # noqa: E402
import midilib  # noqa: E402
from midifixture import make_midi, midi_bytes, track  # noqa: E402


class ReaderTests(unittest.TestCase):
    def test_notes_and_pedal_get_their_times_from_the_tempo(self):
        data = midi_bytes([(0, 60, 80, 1), (1, 64, 100, 1)], pedal=[(0, 127), (2, 0)])         # 120 beats per minute: a beat is 0.5 s
        p = midilib.parse(data)
        self.assertEqual([(round(t, 3), k, a, b) for t, k, a, b in p['events']],
                         [(0.0, 'pedal', 127, 0), (0.0, 'on', 60, 80), (0.5, 'off', 60, 0), (0.5, 'on', 64, 100), (1.0, 'off', 64, 0), (1.0, 'pedal', 0, 0)])
        self.assertEqual((p['notes'], round(p['length'], 3)), (2, 1.0))

    def test_a_change_of_tempo_moves_the_later_notes(self):
        p = midilib.parse(midi_bytes([(0, 60, 80, 1), (4, 62, 80, 1)], tempos=[(2, 1000000)]))       # from beat 2 a beat takes a second
        starts = [round(t, 3) for t, k, _, _ in p['events'] if k == 'on']
        self.assertEqual(starts, [0.0, 3.0])                                                  # beats 0..2 take 1 s, beats 2..4 take 2 s

    def test_only_the_piano_part_is_kept(self):
        extra = [(0, b'\xc0\x28'),                       # program change (a violin)
                 (0, b'\xb0\x00\x05'), (0, b'\xb0\x20\x00'),   # bank select
                 (0, b'\xb0\x07\x10'), (0, b'\xb0\x0a\x00'),   # volume, pan
                 (0, b'\xe0\x00\x40'),                    # pitch bend
                 (0, b'\x99\x24\x7f'), (1, b'\x89\x24\x00'),   # a drum on channel 10
                 (0, b'\xf0\x03\x01\x02\xf7')]            # system exclusive
        p = midilib.parse(midi_bytes([(0, 60, 80, 1)], extra=extra))
        self.assertEqual({k for _, k, _, _ in p['events']}, {'on', 'off'})
        self.assertEqual(p['notes'], 1)

    def test_every_other_channel_plays_on_the_same_piano_and_a_zero_velocity_note_on_is_a_note_off(self):
        extra = [(0, b'\x93\x40\x50'), (1, b'\x93\x40\x00')]                                # channel 4, ended by velocity 0
        p = midilib.parse(midi_bytes([(0, 60, 80, 1)], extra=extra))
        self.assertEqual([(k, a) for _, k, a, _ in p['events']].count(('off', 64)), 1)

    def test_a_note_off_comes_before_a_note_on_at_the_same_moment(self):
        p = midilib.parse(midi_bytes([(0, 60, 80, 1), (1, 60, 80, 1)]))
        at_one = [k for t, k, a, _ in p['events'] if abs(t - 0.5) < 1e-6]
        self.assertEqual(at_one, ['off', 'on'])

    def test_running_status_and_the_title(self):
        events = bytes([0x00, 0x90, 60, 80, 0x60, 62, 80, 0x60, 0x80, 60, 0, 0x00, 62, 0])   # the status byte is left out the second time
        body = b'\x00\xff\x03\x05Piece' + events + b'\x00\xff\x2f\x00'
        data = b'MThd' + struct.pack('>IHHH', 6, 0, 1, 96) + b'MTrk' + struct.pack('>I', len(body)) + body
        p = midilib.parse(data)
        self.assertEqual((p['title'], p['notes']), ('Piece', 2))

    def test_what_is_refused(self):
        cases = {
            'not a standard MIDI file': b'RIFF' + b'x' * 40,
            'only MIDI files of format 0 and 1': midi_bytes([(0, 60, 80, 1)], fmt=2),
            'SMPTE': b'MThd' + struct.pack('>IHHH', 6, 0, 1, 0xE728) + track([(0, b'\x90\x3c\x50')]),
            'no piano notes': midi_bytes([], extra=[(0, b'\xc0\x01')]),
            'too long': midi_bytes([(0, 60, 80, 1), (1000, 60, 80, 1)], tempo=16000000),
            'too big': b'MThd' + struct.pack('>IHHH', 6, 0, 1, 480) + b'\x00' * (midilib.MAX_BYTES + 1),
        }
        for word, data in cases.items():
            with self.subTest(word), self.assertRaisesRegex(midilib.MidiError, word):
                midilib.parse(data)

    def test_a_file_with_too_many_events_is_refused_before_anything_is_built(self):
        many = [(i, b'\xb0\x07\x01') for i in range(midilib.MAX_EVENTS + 5)]                 # volume changes only: dropped, but counted
        with self.assertRaisesRegex(midilib.MidiError, 'too many events'):
            midilib.parse(midi_bytes([(0, 60, 80, 1)], extra=[(i / 480.0, raw) for i, raw in many]))

    def test_a_truncated_file_plays_what_is_there_and_garbage_is_an_error(self):
        data = midi_bytes([(0, 60, 80, 1), (1, 62, 80, 1)])
        p = midilib.parse(data[:-10])                                                       # the end of the track is missing
        self.assertGreaterEqual(p['notes'], 1)
        with self.assertRaises(midilib.MidiError):
            midilib.parse(b'MThd' + struct.pack('>IHHH', 6, 0, 1, 480) + b'MTrk' + struct.pack('>I', 3) + b'\x00\x90\x3c')   # no velocity, no notes


class LibraryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.lib = os.path.join(self.tmp.name, 'midi')
        self.src = make_midi(os.path.join(self.tmp.name, 'Prelude.mid'), [(0, 60, 80, 1), (1, 64, 80, 1)], title='Prelude in C')

    def leftovers(self):
        return [n for n in os.listdir(self.lib) if n.startswith(('.tmp', '.deleting'))] if os.path.isdir(self.lib) else []

    def test_import_publishes_a_complete_entry_and_lists_it(self):
        midi_id, already = midilib.import_midi(self.src, 'Prelude.mid', self.lib)
        self.assertRegex(midi_id, r'^m-[0-9a-f]{16}$')
        self.assertFalse(already)
        listed = midilib.list_library(self.lib)
        self.assertEqual([(e['id'], e['name'], e['original_name'], e['notes']) for e in listed], [(midi_id, 'Prelude in C', 'Prelude.mid', 2)])
        self.assertEqual(listed[0]['length'], 1.0)
        self.assertTrue(midilib.resolve(self.lib, midi_id).endswith('song.mid'))
        self.assertEqual(self.leftovers(), [])

    def test_a_generic_track_name_gives_way_to_the_file_name(self):
        for title in ('control track', 'Track 1', 'Untitled'):
            with self.subTest(title):
                src = make_midi(os.path.join(self.tmp.name, 'liszt-consolation_no3.mid'), [(0, 60, 80, 1)], title=title)
                lib = os.path.join(self.tmp.name, 'lib-' + title.replace(' ', ''))
                midilib.import_midi(src, 'liszt-consolation_no3.mid', lib)
                self.assertEqual(midilib.list_library(lib)[0]['name'], 'liszt consolation no3')

    def test_the_same_file_is_not_copied_twice(self):
        a, _ = midilib.import_midi(self.src, 'Prelude.mid', self.lib)
        b, already = midilib.import_midi(self.src, 'again.mid', self.lib)
        self.assertEqual((a, already), (b, True))
        self.assertEqual(len(midilib.list_library(self.lib)), 1)

    def test_a_bad_file_a_big_file_and_a_full_disk_leave_nothing(self):
        junk = os.path.join(self.tmp.name, 'junk.mid')
        with open(junk, 'wb') as f:
            f.write(b'not midi' * 50)
        with self.assertRaisesRegex(bankimport.ImportFailed, 'not a standard MIDI file'):
            midilib.import_midi(junk, 'junk.mid', self.lib)
        big = os.path.join(self.tmp.name, 'big.mid')
        with open(big, 'wb') as f:
            f.write(b'x' * (midilib.MAX_BYTES + 1))
        with self.assertRaisesRegex(bankimport.ImportFailed, 'too big'):
            midilib.import_midi(big, 'big.mid', self.lib)
        full = lambda path: SimpleNamespace(f_bavail=1, f_frsize=4096)         # noqa: E731
        with self.assertRaisesRegex(bankimport.ImportFailed, 'not enough space'):
            midilib.import_midi(self.src, 'Prelude.mid', self.lib, statvfs=full)
        self.assertEqual(midilib.list_library(self.lib), [])
        self.assertEqual(self.leftovers(), [])

    def test_delete_refuses_the_playing_file_and_removes_others(self):
        a, _ = midilib.import_midi(self.src, 'Prelude.mid', self.lib)
        other = make_midi(os.path.join(self.tmp.name, 'Etude.mid'), [(0, 67, 70, 1)])
        b, _ = midilib.import_midi(other, 'Etude.mid', self.lib)
        with self.assertRaisesRegex(ValueError, 'playing'):
            midilib.delete_file(self.lib, a, keep_ids=[a])
        with self.assertRaisesRegex(ValueError, 'no such'):
            midilib.delete_file(self.lib, 'm-0000000000000000')
        midilib.delete_file(self.lib, b, keep_ids=[a])
        self.assertEqual([e['id'] for e in midilib.list_library(self.lib)], [a])
        self.assertEqual(self.leftovers(), [])

    def test_forged_and_damaged_entries_are_invisible_and_ids_are_checked(self):
        a, _ = midilib.import_midi(self.src, 'Prelude.mid', self.lib)
        os.makedirs(os.path.join(self.lib, 'm-0123456789abcdef'))                        # no meta.json
        with open(os.path.join(self.lib, a, 'song.mid'), 'ab') as f:
            f.write(b'x')                                                             # the size no longer agrees with meta.json
        self.assertEqual(midilib.list_library(self.lib), [])
        for bad in ('../x', 'm-zz', 'song.mid', None, 5, 'i-0123456789abcdef', 'm-0123456789abcdef'):
            with self.subTest(bad), self.assertRaises(ValueError):
                midilib.resolve(self.lib, bad)


if __name__ == '__main__':
    unittest.main()
