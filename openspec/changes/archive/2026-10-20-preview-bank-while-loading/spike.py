"""Spike for preview-bank-while-loading (tasks 1.2, 1.3): how long do note-on calls wait while the main bank loads?

python3 spike.py same    -- load the main bank into the playing synthesizer (fluid_synth_sfload)
python3 spike.py helper  -- load it into a second synthesizer object, then move the SoundFont over (remove_sfont + add_sfont)
"""

import ctypes
import sys
import threading
import time

L = ctypes.CDLL('libfluidsynth.so.3')
P = ctypes.c_void_p
for name, res, args in [
    ('new_fluid_settings', P, []),
    ('fluid_settings_setstr', ctypes.c_int, [P, ctypes.c_char_p, ctypes.c_char_p]),
    ('fluid_settings_setint', ctypes.c_int, [P, ctypes.c_char_p, ctypes.c_int]),
    ('fluid_settings_setnum', ctypes.c_int, [P, ctypes.c_char_p, ctypes.c_double]),
    ('new_fluid_synth', P, [P]),
    ('new_fluid_audio_driver', P, [P, P]),
    ('fluid_synth_sfload', ctypes.c_int, [P, ctypes.c_char_p, ctypes.c_int]),
    ('fluid_synth_get_sfont_by_id', P, [P, ctypes.c_int]),
    ('fluid_synth_remove_sfont', ctypes.c_int, [P, P]),
    ('fluid_synth_add_sfont', ctypes.c_int, [P, P]),
    ('fluid_synth_noteon', ctypes.c_int, [P, ctypes.c_int, ctypes.c_int, ctypes.c_int]),
    ('fluid_synth_noteoff', ctypes.c_int, [P, ctypes.c_int, ctypes.c_int]),
    ('fluid_synth_program_select', ctypes.c_int, [P, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int]),
    ('fluid_synth_sfunload', ctypes.c_int, [P, ctypes.c_int, ctypes.c_int]),
    ('delete_fluid_synth', None, [P]),
    ('fluid_synth_get_active_voice_count', ctypes.c_int, [P]),
]:
    f = getattr(L, name)
    f.restype = res
    f.argtypes = args

D = '/opt/exxpiano/soundfonts/'
mode = sys.argv[1]
s = L.new_fluid_settings()
for k, v in [('audio.driver', 'alsa'), ('audio.alsa.device', 'plughw:CARD=Headphones'), ('audio.sample-format', '16bits')]:
    L.fluid_settings_setstr(s, k.encode(), v.encode())
for k, v in [
    ('audio.period-size', 64),
    ('audio.periods', 3),
    ('synth.polyphony', 192),
    ('synth.cpu-cores', 3),
    ('synth.reverb.active', 0),
    ('synth.chorus.active', 0),
]:
    L.fluid_settings_setint(s, k.encode(), v)
L.fluid_settings_setnum(s, b'synth.sample-rate', 44100.0)
L.fluid_settings_setnum(s, b'synth.gain', 1.7)

t0 = time.monotonic()
synth = L.new_fluid_synth(s)
prev = L.fluid_synth_sfload(synth, (D + 'piano-preview.sf2').encode(), 0)
mech = L.fluid_synth_sfload(synth, (D + 'mech.sf2').encode(), 0)
L.fluid_synth_program_select(synth, 0, prev, 0, 0)
drv = L.new_fluid_audio_driver(s, synth)
print('preview + mech ready, audio on: %.2f s' % (time.monotonic() - t0), flush=True)

done = {}


def load():
    t = time.monotonic()
    if mode == 'same':
        done['id'] = L.fluid_synth_sfload(synth, (D + 'piano.sf2').encode(), 0)
    else:
        helper = L.new_fluid_synth(s)
        hid = L.fluid_synth_sfload(helper, (D + 'piano.sf2').encode(), 0)
        sf = L.fluid_synth_get_sfont_by_id(helper, hid)
        L.fluid_synth_remove_sfont(helper, sf)
        L.delete_fluid_synth(helper)
        a = time.monotonic()
        done['id'] = L.fluid_synth_add_sfont(synth, sf)
        done['add'] = time.monotonic() - a
    done['t'] = time.monotonic() - t


th = threading.Thread(target=load)
th.start()
waits, n, notes = [], 0, [48, 52, 55, 60, 64, 67, 72, 76]
while th.is_alive() or n < 20:
    key = notes[n % len(notes)]
    n += 1
    a = time.monotonic()
    L.fluid_synth_noteon(synth, 0, key, 70)
    waits.append(time.monotonic() - a)
    time.sleep(0.12)
    L.fluid_synth_noteoff(synth, 0, key)
    time.sleep(0.03)
    if not th.is_alive() and 'switched' not in done:
        L.fluid_synth_program_select(synth, 0, done['id'], 0, 0)
        done['switched'] = n
        L.fluid_synth_sfunload(synth, prev, 1)
th.join()
w = sorted(waits)
print(
    'mode %s: main bank %.1f s%s; %d notes, note-on wait median %.2f ms, p99 %.1f ms, max %.1f ms'
    % (
        mode,
        done['t'],
        ', add_sfont %.1f ms' % (done['add'] * 1000) if 'add' in done else '',
        len(w),
        w[len(w) // 2] * 1000,
        w[int(len(w) * 0.99)] * 1000,
        w[-1] * 1000,
    ),
    flush=True,
)
with open('/proc/self/status') as status:
    for line in status:
        if line.startswith(('VmRSS', 'VmHWM')):
            print(line.strip())
time.sleep(1.5)
