"""The MIDI files in the web API: the files of the drive, the import, the library, play and stop requests, deleting.

The player lives in the bridge, another process: here the request file it would read is checked and its status file is written by hand.
Run from the repository root:   python3 -m unittest discover -s tests -v
"""
import json
import os
import sys
import tempfile
import threading
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'pi'))
sys.path.insert(0, HERE)
import midilib  # noqa: E402
import usblib  # noqa: E402
from midifixture import make_midi  # noqa: E402
from sf2fixture import make_sf2  # noqa: E402
from test_web import ServerCase, web  # noqa: E402


class MidiApiTests(ServerCase):
    def setUp(self):
        self.work = tempfile.TemporaryDirectory()
        self.addCleanup(self.work.cleanup)
        w = self.work.name
        self.bank_dir = os.path.join(w, 'installed')
        os.makedirs(self.bank_dir)
        mech = make_sf2(os.path.join(self.bank_dir, 'mech.sf2'), presets=[('Hammer', 0, 1)], name='Mechanics')
        old = make_sf2(os.path.join(self.bank_dir, 'old.sf2'), presets=[('Old', 0, 0)], name='Old piano')
        self.midi_dir, self.run_dir = os.path.join(w, 'midi'), os.path.join(w, 'run')
        self.status = os.path.join(w, 'usb-status.json')
        self.mount = os.path.join(w, 'drive')
        folder = os.path.join(self.mount, 'piano-midi')
        os.makedirs(folder)
        make_midi(os.path.join(folder, 'Prelude.mid'), [(0, 60, 80, 1), (1, 64, 80, 1)], title='Prelude')
        with open(os.path.join(folder, 'broken.midi'), 'wb') as f:
            f.write(b'not a midi file' * 10)
        for name in ('notes.txt', '.hidden.mid'):
            with open(os.path.join(folder, name), 'wb') as f:
                f.write(b'x')
        self.conf_text_base = ('BANK_DIR=%s\nPIANO_SF2="%s"\nMECH_SF2="%s"\nMIDI_DIR=%s\nMIDI_RUN_DIR=%s\nIMPORT_DIR=%s\nUSB_STATUS=%s\n'
                               % (self.bank_dir, old, mech, self.midi_dir, self.run_dir, os.path.join(w, 'banks'), self.status)).replace('\\\n', '\n')
        self.start(self.conf_text_base)

    def plug(self):
        usblib._write_json(self.status, dict(drives=[dict(id='/dev/sda1', label='PIANO', fs='vfat', mounted=True, mountpoint=self.mount, reason=None)]))

    def wait_import(self):
        for _ in range(200):
            s = self.jcall('/api/midi')[1]['importing']
            if s['state'] in ('done', 'failed'):
                return s
            time.sleep(0.02)
        self.fail('the import did not finish')
        return None

    def player_says(self, **st):
        os.makedirs(self.run_dir, exist_ok=True)
        with open(os.path.join(self.run_dir, 'status.json'), 'w') as f:
            json.dump(dict(dict(state='idle', id=None, updated=int(time.time())), **st), f)

    def request(self):
        with open(os.path.join(self.run_dir, 'request.json')) as f:
            return json.load(f)

    def test_the_files_of_the_drive_are_listed_and_nothing_else(self):
        self.plug()
        s = self.jcall('/api/midi')[1]
        self.assertEqual([f['name'] for f in s['drives'][0]['files']], ['broken.midi', 'Prelude.mid'])
        self.assertEqual((s['drives'][0]['has_folder'], s['library'], s['player']['state']), (True, [], 'unavailable'))

    def test_import_play_stop_and_delete_with_the_player_in_another_process(self):
        self.plug()
        self.assertEqual(self.jcall('/api/midi', {'action': 'import', 'name': 'Prelude.mid'})[0], 200)
        done = self.wait_import()
        self.assertEqual((done['state'], done['kind']), ('done', 'midi'))
        lib = self.jcall('/api/midi')[1]['library']
        self.assertEqual([(m['name'], m['notes']) for m in lib], [('Prelude', 2)])
        midi_id = lib[0]['id']
        usblib._write_json(self.status, dict(drives=[]))                                     # the drive is pulled out: the file stays
        self.assertEqual(self.jcall('/api/midi')[1]['library'][0]['id'], midi_id)
        code, r = self.jcall('/api/midi', {'action': 'play', 'id': midi_id, 'loop': True})
        self.assertEqual((code, r['accepted']), (200, True))
        req = self.request()
        self.assertEqual((req['op'], req['id'], req['loop']), ('play', midi_id, True))
        first_nonce = req['nonce']
        self.player_says(state='playing', id=midi_id, position=1.5, length=2.0, loop=True)
        p = self.jcall('/api/midi')[1]['player']
        self.assertEqual((p['state'], p['position']), ('playing', 1.5))
        code, r = self.jcall('/api/midi', {'action': 'delete', 'id': midi_id})
        self.assertEqual(code, 400)
        self.assertIn('playing', r['error'])
        code, r = self.jcall('/api/midi', {'action': 'seek', 'id': midi_id, 'position': 1.25})
        self.assertEqual((code, r['accepted']), (200, True))
        req = self.request()
        self.assertEqual((req['op'], req['id'], req['position']), ('seek', midi_id, 1.25))
        for bad in ('1', None, -1, True, 100000, float('inf')):
            with self.subTest(bad):
                self.assertEqual(self.jcall('/api/midi', {'action': 'seek', 'id': midi_id, 'position': bad})[0], 400)
        self.assertEqual(self.jcall('/api/midi', {'action': 'seek', 'id': 'm-0000000000000000', 'position': 1})[0], 400)
        self.assertEqual(self.jcall('/api/midi', {'action': 'stop'})[0], 200)
        self.assertEqual(self.request()['op'], 'stop')
        self.assertGreater(self.request()['nonce'], first_nonce)                              # every request is a new one
        self.player_says(state='idle', reason='stopped')
        code, r = self.jcall('/api/midi', {'action': 'delete', 'id': midi_id})
        self.assertEqual((code, r['deleted']), (200, True))
        self.assertEqual(self.jcall('/api/midi')[1]['library'], [])

    def test_a_status_that_is_old_means_the_player_is_not_running(self):
        self.player_says(state='playing', updated=int(time.time()) - 60)
        self.assertEqual(self.jcall('/api/midi')[1]['player']['state'], 'unavailable')

    def test_a_broken_file_fails_with_a_safe_message(self):
        self.plug()
        self.jcall('/api/midi', {'action': 'import', 'name': 'broken.midi'})
        s = self.wait_import()
        self.assertEqual(s['state'], 'failed')
        self.assertIn('not a standard MIDI file', s['error'])
        self.assertNotIn(self.work.name, s['error'])
        self.assertEqual(midilib.list_library(self.midi_dir), [])

    def test_refusals(self):
        self.plug()
        for body in ({'action': 'import', 'name': '../Prelude.mid'}, {'action': 'import', 'name': 'notes.txt'}, {'action': 'import', 'name': '.hidden.mid'},
                     {'action': 'import'}, {'action': 'play', 'id': 'm-0000000000000000'}, {'action': 'play', 'id': '../x'}, {'action': 'play'},
                     {'action': 'delete', 'id': 'm-0000000000000000'}, {'action': 'explode'}):
            with self.subTest(body):
                self.assertEqual(self.call('/api/midi', body)[0], 400)
                self.assertFalse(os.path.exists(os.path.join(self.run_dir, 'request.json')))     # nothing was asked of the player
        self.assertEqual(self.call('/api/midi', raw=b'[1]', ctype='application/json')[0], 400)

    def test_nothing_starts_while_an_import_runs(self):
        self.plug()
        self.jcall('/api/midi', {'action': 'import', 'name': 'Prelude.mid'})
        self.wait_import()
        midi_id = self.jcall('/api/midi')[1]['library'][0]['id']
        gate = threading.Event()
        real = web.midilib.import_midi

        def slow(*a, **kw):
            gate.wait(5)
            return real(*a, **kw)
        web.midilib.import_midi = slow
        self.addCleanup(setattr, web.midilib, 'import_midi', real)
        self.addCleanup(gate.set)
        self.jcall('/api/midi', {'action': 'import', 'name': 'Prelude.mid'})
        self.assertEqual(self.call('/api/midi', {'action': 'play', 'id': midi_id})[0], 409)
        self.assertEqual(self.call('/api/midi', {'action': 'delete', 'id': midi_id})[0], 409)
        self.assertEqual(self.call('/api/midi', {'action': 'import', 'name': 'Prelude.mid'})[0], 409)
        gate.set()
        self.wait_import()

    def test_the_drive_state_of_the_banks_still_lists_its_own_files(self):
        self.plug()
        d = self.jcall('/api/usb')[1]['drives'][0]
        self.assertEqual(d['files'], [])                                                    # no banks on this drive
        self.assertEqual([f['name'] for f in d['midi_files']], ['broken.midi', 'Prelude.mid'])

    def test_the_player_has_its_own_section_and_nothing_in_the_page_stops_it(self):
        code, body = self.call('/')
        page = body.decode()
        self.assertEqual(code, 200)
        self.assertIn('id="paneMidi"', page)
        self.assertIn('data-page="midi"', page)
        self.assertIn('midi: "paneMidi"', page)
        # the page only asks and shows: the one place that sends a stop is the Stop button
        self.assertEqual(page.count('action: "stop"'), 1)
        self.assertEqual(page.count('action: "seek"'), 1)                 # the slider and the ±10 s buttons send a seek through one function
        self.assertIn('type = "range"', page)
        # the rights and the links are in their own section (About), a short copyright line is the footer of every section; both in English and Russian
        for needle in ('id="legal"', 'id="legalCopy"', 'id="paneAbout"', 'data-page="about"', 'about: "paneAbout"', 'https://github.com/exxbrain/exxpiano/blob/main/LICENSE',
                       'https://github.com/exxbrain/exxpiano/releases', 'TROUBLESHOOTING.ru.md', 'https://www.fluidsynth.org', 'by Alexander Holm, [CC-BY](https://creativecommons.org/licenses/by/3.0/)', '[CC BY-NC-SA 4.0](https://creativecommons.org/licenses/by-nc-sa/4.0/): non-commercial use only',
                       '[CC BY-NC-SA 4.0](https://creativecommons.org/licenses/by-nc-sa/4.0/): только некоммерческое использование', 'https://magenta.tensorflow.org/datasets/maestro', 'https://freepats.zenvoid.org/Piano/SalamanderGrandPiano/',
                       'liszt-un-sospiro-2011.mid', 'liszt-la-campanella-2013.mid'):
            self.assertIn(needle, page)
        self.assertNotIn('id="bankRights"', page)
        self.assertNotIn('id="midiRights"', page)

    def test_the_token_protects_the_midi_api_too(self):
        self.start(self.conf_text_base + 'WEB_TOKEN=secret\n')
        self.assertEqual(self.call('/api/midi')[0], 401)
        self.assertEqual(self.call('/api/midi', {'action': 'stop'})[0], 401)


if __name__ == '__main__':
    unittest.main()
