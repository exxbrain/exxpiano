#!/usr/bin/env python3
"""exxpiano-fx: MIDI bridge between USB keyboards and FluidSynth that adds piano mechanics.

* passes every message from hardware MIDI inputs to FluidSynth on channel 0 (the piano);
* on key release plays hammer/damper noise (channel 1) and string resonance (channels 2-4);
  with the sustain pedal down the resonance is deferred until the pedal is released;
* on sustain pedal down/up plays the pedal noise (channels 5 / 6).

Levels follow the SFZ definition of Salamander Grand Piano V3: base volume, amp_veltrack
(note-on velocity) and rt_decay (dB per second the key was held).  The mechanics presets in
Salamander-mech.sf2 map velocity linearly to attenuation (60 dB over 0..127), so this program
requests an attenuation in dB through the note velocity.

Needs: python3-mido python3-rtmidi (apt).  Run:  exxpiano-fx.py [--hammer-boost DB] [--res-boost DB]
[--pedal-boost DB]
"""

import argparse
import math
import os
import random
import re
import sys
import threading
import time

CH_HAMMER, CH_RES_L, CH_RES_S, CH_RES_V3, CH_PEDAL_DN, CH_PEDAL_UP = 1, 2, 3, 4, 5, 6
PROGRAMS = {CH_HAMMER: 1, CH_RES_L: 2, CH_RES_S: 3, CH_RES_V3: 4, CH_PEDAL_DN: 5, CH_PEDAL_UP: 6}
ATT_RANGE_DB = 60.0  # attenuation covered by velocity 1..127 in Salamander-mech.sf2
# A sustain value of a MIDI file at or above this is "down". Recordings of real pianists (the demonstration pieces) half-pedal: the value dips
# to 40-63 for a few tens of milliseconds and comes back. FluidSynth takes 64 as the limit and stopped every sounding note in such a dip;
# on a piano dampers that barely touch the strings do not stop them. A keyboard's switch pedal sends only 0 and 127 and is not changed.
FILE_PEDAL_DOWN = 40  # the same as midiplayer.FILE_PEDAL_DOWN: the player must know the pedal is down to let it go when it stops

# Loudness of ONE string-resonance voice at velocity 127 (loudest 100 ms, RMS, dBFS at FluidSynth gain 1.0), measured on mech.sf2 for the keys
# 20, 22 ... 88 (index (key-20)//2). A voice of velocity v is ATT_RANGE_DB*(1-v/127) dB quieter. The resonance is about 35 dB below the piano in
# energy, and most single voices (all of group V3, the soft ones of S) are at or below -60 dBFS: they only take voices of the synthesizer.
RES_LEVEL = {
    CH_RES_L: (
        -39.5,
        -40.5,
        -34.6,
        -44.8,
        -44.6,
        -39.0,
        -38.3,
        -39.1,
        -46.8,
        -43.8,
        -44.5,
        -37.7,
        -41.1,
        -41.0,
        -45.0,
        -41.8,
        -42.4,
        -42.8,
        -45.4,
        -46.0,
        -44.6,
        -51.0,
        -50.8,
        -48.2,
        -53.2,
        -53.2,
        -53.8,
        -51.5,
        -51.8,
        -54.7,
        -61.8,
        -62.4,
        -58.5,
        -55.8,
        -56.5,
    ),
    CH_RES_S: (
        -46.5,
        -46.9,
        -46.3,
        -55.7,
        -56.6,
        -53.0,
        -51.9,
        -52.0,
        -55.9,
        -54.0,
        -54.0,
        -53.9,
        -51.6,
        -52.3,
        -53.1,
        -49.6,
        -49.5,
        -52.5,
        -54.9,
        -54.7,
        -56.0,
        -58.1,
        -58.8,
        -57.2,
        -61.2,
        -61.3,
        -61.6,
        -61.2,
        -61.1,
        -61.5,
        -62.6,
        -62.3,
        -63.1,
        -62.3,
        -62.6,
    ),
    CH_RES_V3: (
        -56.4,
        -56.6,
        -58.8,
        -59.2,
        -59.1,
        -59.1,
        -59.1,
        -59.5,
        -59.1,
        -57.5,
        -57.7,
        -62.4,
        -60.6,
        -61.3,
        -61.3,
        -59.9,
        -60.6,
        -59.7,
        -63.8,
        -64.2,
        -72.2,
        -68.2,
        -68.5,
        -66.1,
        -67.6,
        -67.3,
        -69.1,
        -64.8,
        -65.3,
        -63.4,
        -67.2,
        -67.6,
        -69.0,
        -67.2,
        -67.1,
    ),
}


def res_level(ch, note, vel):
    """Expected loudness (dBFS, RMS) of the resonance voice (channel, key, velocity)."""
    idx = min(34, max(0, round((note - 20) / 2.0)))
    return RES_LEVEL[ch][idx] - ATT_RANGE_DB * (1.0 - vel / 127.0)


def res_decay(key):
    """rt_decay (dB/s) of the long (L) resonance groups, by key."""
    if key <= 28:
        return 6
    if key <= 37:
        return 7
    if key <= 49:
        return 8
    return 9


class FXCore:
    """Pure logic: feed it note/pedal events, it returns FX note-ons as (channel, note, velocity)."""

    def __init__(
        self,
        hammer_boost=10.0,
        res_boost=0.0,
        pedal_boost=0.0,
        rng=None,
        hammer_exp=2.0,
        randomness=1.0,
        velocity_curve=1.0,
        piano_bank=0,
        piano_prog=0,
        res_floor=None,
        res_burst=0,
        res_makeup=6.0,
    ):
        self.res_floor = res_floor  # dBFS: a resonance voice quieter than this is not started (None = every voice is started)
        self.res_burst = res_burst  # at most this many resonance voices when the pedal lifts (0 = no limit)
        self.res_makeup = res_makeup  # dB: the most that the loudest voices of a limited burst get back for the ones that were dropped
        self.boost = {'hammer': hammer_boost, 'res': res_boost, 'pedal': pedal_boost}
        self.hammer_exp = hammer_exp
        self.randomness = randomness
        self.velocity_curve = velocity_curve
        self.piano_bank = piano_bank  # the preset that plays the piano on channel 0
        self.piano_prog = piano_prog
        self.rng = rng or random.Random()
        self.held = {}  # note -> (t_on, vel)
        self.sustained = {}  # note -> (t_on, vel) released while the pedal was down
        self.pedal = False

    def configure(
        self, hammer_boost=None, res_boost=None, pedal_boost=None, hammer_exp=None, randomness=None, velocity_curve=None, piano_bank=None, piano_prog=None
    ):
        """Change settings while running (None = keep)."""
        if hammer_boost is not None:
            self.boost['hammer'] = hammer_boost
        if res_boost is not None:
            self.boost['res'] = res_boost
        if pedal_boost is not None:
            self.boost['pedal'] = pedal_boost
        if hammer_exp is not None:
            self.hammer_exp = hammer_exp
        if randomness is not None:
            self.randomness = randomness
        if velocity_curve is not None:
            self.velocity_curve = velocity_curve
        if piano_bank is not None:
            self.piano_bank = piano_bank
        if piano_prog is not None:
            self.piano_prog = piano_prog

    def map_velocity(self, vel):
        """Touch curve: out = 127 * (in/127) ** curve. 1.0 = unchanged, < 1 = more sensitive (soft touches get louder),
        > 1 = harder (needs a firmer touch)."""
        g = self.velocity_curve
        if g == 1.0:
            return vel
        return max(1, min(127, round(127.0 * (vel / 127.0) ** g)))

    @staticmethod
    def _vel(att_db):
        if att_db < 0:
            att_db = 0.0
        v = round(127 * (1.0 - att_db / ATT_RANGE_DB))
        return v if v >= 1 else 0

    @staticmethod
    def _track(vel, pct):
        amp = 1.0 - pct / 100.0 * (1.0 - vel / 127.0)
        return -20.0 * math.log10(max(amp, 1e-3))

    def _emit(self, out, ch, note, att_db):
        v = self._vel(att_db)
        if v:
            out.append((ch, note, v))

    def _jitter_db(self, sigma):
        """Gaussian level variation in dB, clamped to +-2 sigma; 0 when randomness is off."""
        s = sigma * self.randomness
        if s <= 0:
            return 0.0
        return max(-2 * s, min(2 * s, self.rng.gauss(0.0, s)))

    def hammer(self, out, note, vel, hold):
        if 21 <= note <= 108:
            # amplitude ~ (vel/127)**hammer_exp: a soft touch is much quieter than a hard one
            att = 37.0 - 20.0 * self.hammer_exp * math.log10(max(vel, 1) / 127.0) + 2.0 * hold - self.boost['hammer'] + self._jitter_db(3.0)
            key = note
            # every key has its own recorded sample, so a neighbour gives a different knock
            if self.randomness > 0 and self.rng.random() < 0.7 * min(self.randomness, 1.0):
                cand = note + self.rng.choice((-3, -2, -1, 1, 2, 3))
                if 21 <= cand <= 108:
                    key = cand
            self._emit(out, CH_HAMMER, key, att)

    def _emit_res(self, out, ch, note, att_db):
        """A resonance voice, unless it would be too quiet to hear: it would only take one of the synthesizer's voices."""
        v = self._vel(att_db)
        if v and (self.res_floor is None or res_level(ch, note, v) >= self.res_floor):
            out.append((ch, note, v))

    def _limit_burst(self, res):
        """Pedal up releases every sustained note at once: keep the `res_burst` loudest resonance voices and give the power of the others back to
        them as level (at most `res_makeup` dB), so the sum sounds the same with fewer voices."""
        if not self.res_burst or len(res) <= self.res_burst:
            return res
        ranked = sorted(res, key=lambda e: -res_level(*e))
        kept = ranked[: self.res_burst]
        p_all = sum(10 ** (res_level(*e) / 10.0) for e in ranked)
        p_kept = sum(10 ** (res_level(*e) / 10.0) for e in kept)
        gain = min(self.res_makeup, 10.0 * math.log10(p_all / p_kept))
        return [(ch, n, self._vel(ATT_RANGE_DB * (1.0 - v / 127.0) - gain) or v) for ch, n, v in kept]

    def resonance(self, out, note, vel, hold):
        if not 20 <= note <= 88:
            return
        b = self.boost['res']
        track_l = 94 if note <= 37 else 90
        if vel >= 45:  # long layer, only for velocities from 45
            self._emit_res(out, CH_RES_L, note, 4.0 + self._track(vel, track_l) + res_decay(note) * hold - b)
        else:  # short layer for velocities up to 44
            track_s = 95 if note <= 46 else 90
            self._emit_res(out, CH_RES_S, note, self._track(vel, track_s) + 7.0 * hold - b)
        self._emit_res(out, CH_RES_V3, note, self._track(vel, 96) + 2.0 * hold - b)

    def pedal_sound(self, out, down):
        key = self.rng.choice((60, 61))
        base = 20.0 if down else 19.0
        self._emit(out, CH_PEDAL_DN if down else CH_PEDAL_UP, key, base - self.boost['pedal'] + self._jitter_db(2.0))

    # ---- events ----
    def note_on(self, note, vel, now):
        self.held[note] = (now, vel)
        self.sustained.pop(note, None)
        return []

    def note_off(self, note, now):
        out = []
        item = self.held.pop(note, None)
        if item is None:
            return out
        t_on, vel = item
        hold = now - t_on
        self.hammer(out, note, vel, hold)
        if self.pedal:
            self.sustained[note] = item
        else:
            self.resonance(out, note, vel, hold)
        return out

    def sustain(self, value, now):
        out = []
        down = value >= 64
        if down == self.pedal:
            return out
        self.pedal = down
        self.pedal_sound(out, down)
        if not down:
            res = []
            for note, (t_on, vel) in sorted(self.sustained.items()):
                self.resonance(res, note, vel, now - t_on)
            out.extend(self._limit_burst(res))
            self.sustained.clear()
        return out

    def all_off(self):
        self.held.clear()
        self.sustained.clear()


# ---------------------------------------------------------------- MIDI I/O ---
SYNTH_RE = re.compile(r'FLUID Synth', re.I)
SKIP_RE = re.compile(r'Through|DINTHRU|MCU|HUI|ALV|FLUID|Timer|Announce|System|RtMidi|Client', re.I)


# The reverb send at REVERB_SEND = 127, in 0.1 % (the unit of the SoundFont generator reverbEffectsSend).
REVERB_SEND_MAX = 500


class Bridge:
    def __init__(self, core):
        import exxpianoconf
        import mido

        self.mido = mido
        self.exxpianoconf = exxpianoconf
        self.conf_path = exxpianoconf.CONF_PATH
        self.conf_mtime = None
        self.core = core
        self.lock = threading.Lock()
        self.out = None
        self.out_name = None
        self.inputs = {}
        self.reverb_send = exxpianoconf.SETTINGS['reverb_send']['default']

    def send(self, msg):
        out = self.out
        if out is None:
            return
        try:
            out.send(msg)
        except Exception as exc:  # port vanished
            print('send failed:', exc, file=sys.stderr)
            self.out = None
            self.out_name = None

    def play_event(self, kind, a, b):
        """A note or the sustain pedal of a MIDI file, on the road of a keyboard's messages: the touch curve, the piano and the mechanics."""
        mido = self.mido
        if kind == 'pedal':
            msg = mido.Message('control_change', channel=0, control=64, value=127 if a >= FILE_PEDAL_DOWN else 0)
        else:
            msg = mido.Message('note_on' if kind == 'on' else 'note_off', channel=0, note=a, velocity=b)
        self.on_msg(msg)

    def release_all(self, pedal_was_down):
        """Silence after a MIDI file: let the pedal go (when it was down) and switch every note off, in the synthesizer and in the mechanics."""
        mido = self.mido
        if pedal_was_down:
            self.on_msg(mido.Message('control_change', channel=0, control=64, value=0))
        self.on_msg(mido.Message('control_change', channel=0, control=123, value=0))

    def select_piano(self):
        """Bank select + program change for the piano on channel 0 (the caller holds the lock or runs before playing)."""
        bank, prog = self.core.piano_bank, self.core.piano_prog
        self.send(self.mido.Message('control_change', channel=0, control=0, value=bank))
        self.send(self.mido.Message('control_change', channel=0, control=32, value=0))
        self.send(self.mido.Message('program_change', channel=0, program=prog))

    def send_reverb(self):
        """The reverb send of the piano and of the mechanics: they sound in the same room. Not through CC91 alone: FluidSynth's default
        modulator turns even CC91 = 127 into a send of 20 %, too little to hear. The SoundFont NRPN (CC99 = 120, CC98 = 16, the generator
        reverbEffectsSend in 0.1 %, 8192 = zero offset) maps 0..127 -> 0..50 % for banks without their own reverbEffectsSend, including
        the project banks (the whole 100 % drowned the piano on the device; 64 is about a quarter), whichever way FluidSynth runs.
        For imported banks with reverbEffectsSend, FluidSynth may treat the NRPN as an offset: the final send can differ, even at 0.
        CC91 goes to 0 to clear its contribution (a keyboard's reverb knob still adds its 20 % on top), and the RPN null closes the NRPN:
        a later data entry of a keyboard changes nothing."""
        value = 8192 + round(self.reverb_send * REVERB_SEND_MAX / 127)
        for ch in (0,) + tuple(PROGRAMS):
            for control, v in ((91, 0), (99, 120), (98, 16), (38, value & 127), (6, value >> 7), (101, 127), (100, 127)):
                self.send(self.mido.Message('control_change', channel=ch, control=control, value=v))

    def setup_programs(self):
        self.select_piano()
        for ch, prog in PROGRAMS.items():
            self.send(self.mido.Message('control_change', channel=ch, control=0, value=0))
            self.send(self.mido.Message('control_change', channel=ch, control=32, value=0))
            self.send(self.mido.Message('program_change', channel=ch, program=prog))
        self.send_reverb()

    def on_msg(self, msg):
        mido = self.mido
        if msg.type in ('clock', 'active_sensing', 'reset', 'start', 'stop', 'continue'):
            return
        # The presets of the synthesizer belong to this bridge. A keyboard's program change or bank select would put a mechanics preset (or
        # nothing) on the piano channel, and a GM/GS reset (system exclusive) sets every channel back to program 0: the mechanics channels then
        # play the piano itself, on key release, with no note-off.
        if msg.type in ('sysex', 'program_change') or (msg.type == 'control_change' and msg.control in (0, 32)):
            return
        if hasattr(msg, 'channel') and msg.channel != 0:
            msg = msg.copy(channel=0)
        if msg.type == 'note_on' and msg.velocity > 0:
            mapped = self.core.map_velocity(msg.velocity)  # the touch curve: the piano and the mechanics both see it
            if mapped != msg.velocity:
                msg = msg.copy(velocity=mapped)
        now = time.monotonic()
        with self.lock:
            self.send(msg)  # the piano first: lowest latency
            fx = []
            if msg.type == 'note_on' and msg.velocity > 0:
                fx = self.core.note_on(msg.note, msg.velocity, now)
            elif msg.type == 'note_off' or (msg.type == 'note_on' and msg.velocity == 0):
                fx = self.core.note_off(msg.note, now)
            elif msg.type == 'control_change':
                if msg.control == 64:
                    fx = self.core.sustain(msg.value, now)
                elif msg.control in (120, 123):
                    self.core.all_off()
            for ch, note, vel in fx:
                self.send(mido.Message('note_on', channel=ch, note=note, velocity=vel))

    def scan(self):
        mido = self.mido
        try:
            outs = mido.get_output_names()
            ins = mido.get_input_names()
        except Exception as exc:
            print('scan failed:', exc, file=sys.stderr)
            return
        synth = next((n for n in outs if SYNTH_RE.search(n)), None)
        if synth != self.out_name:
            with self.lock:
                if self.out is not None:
                    try:
                        self.out.close()
                    except Exception:
                        pass
                self.out, self.out_name = None, None
                if synth:
                    try:
                        self.out = mido.open_output(synth)
                        self.out_name = synth
                        self.setup_programs()
                        print('synth:', synth, flush=True)
                    except Exception as exc:
                        print('open synth failed:', exc, file=sys.stderr)
        elif self.out is not None:
            # select the presets again on every scan: whatever reset them (a message that reached the synthesizer before the bridge did,
            # a restart that was not seen), the mechanics channels play their own presets again within one scan
            with self.lock:
                self.setup_programs()
        for name in ins:
            if name in self.inputs or SKIP_RE.search(name):
                continue
            try:
                self.inputs[name] = mido.open_input(name, callback=self.on_msg)
                print('input:', name, flush=True)
            except Exception as exc:
                print('open input failed:', name, exc, file=sys.stderr)
        for name in list(self.inputs):
            if name not in ins:
                try:
                    self.inputs.pop(name).close()
                except Exception:
                    pass
                print('input gone:', name, flush=True)

    def reload_settings(self):
        """Re-read /etc/exxpiano.conf when it changed (the web interface writes it)."""
        try:
            mtime = os.stat(self.conf_path).st_mtime_ns
        except OSError:
            return
        if mtime == self.conf_mtime:
            return
        first = self.conf_mtime is None
        self.conf_mtime = mtime
        s = self.exxpianoconf.get_settings(self.conf_path)
        with self.lock:
            preset_changed = (self.core.piano_bank, self.core.piano_prog) != (s['piano_bank'], s['piano_prog'])
            self.core.configure(
                hammer_boost=s['hammer_boost'],
                res_boost=s['res_boost'],
                pedal_boost=s['pedal_boost'],
                hammer_exp=s['hammer_exp'],
                randomness=s['random'],
                velocity_curve=s['velocity_curve'],
                piano_bank=s['piano_bank'],
                piano_prog=s['piano_prog'],
            )
            if preset_changed and not first:
                self.select_piano()
            send_changed = s['reverb_send'] != self.reverb_send
            self.reverb_send = s['reverb_send']
            if send_changed and not first:
                self.send_reverb()
        if not first:
            print(
                'settings reloaded: curve %.2f, hammer %+.1f dB, exp %.2f, random %.1f, res %+.1f, pedal %+.1f'
                % (s['velocity_curve'], s['hammer_boost'], s['hammer_exp'], s['random'], s['res_boost'], s['pedal_boost']),
                flush=True,
            )

    def run(self):
        tick = 0
        while True:
            self.reload_settings()
            if tick % 2 == 0:
                self.scan()
            tick += 1
            time.sleep(1)


def start_midi_player(bridge):
    """The player of the imported MIDI files (asked through /run/exxpiano-midi); a problem in it must never stop the keyboard."""
    try:
        import midiplayer

        conf = bridge.exxpianoconf.parse_conf(bridge.conf_path)
        player = midiplayer.Player(
            conf.get('MIDI_DIR') or '/var/lib/exxpiano/midi',
            conf.get('MIDI_RUN_DIR') or midiplayer.RUN_DIR,
            bridge.play_event,
            bridge.release_all,
            lambda: bridge.out_name,
        )

        def run():
            while True:
                try:
                    player.run()
                except Exception as exc:  # noqa: BLE001
                    print('midi player failed:', exc, file=sys.stderr, flush=True)
                    time.sleep(2)

        threading.Thread(target=run, daemon=True).start()
    except Exception as exc:  # noqa: BLE001
        print('midi player not started:', exc, file=sys.stderr, flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--hammer-boost', type=float, default=1.5, help='dB louder than the SFZ level (default 1.5)')
    ap.add_argument('--res-boost', type=float, default=0.0)
    ap.add_argument('--pedal-boost', type=float, default=4.5)
    ap.add_argument('--hammer-exp', type=float, default=0.8, help='hammer noise amplitude ~ (velocity/127)**EXP; larger = stronger dependence (default 0.8)')
    ap.add_argument(
        '--random', type=float, default=1.6, help='variation of knock/pedal level and sample choice: 0 = none, 1 = normal, 2 = strong (default 1.6)'
    )
    ap.add_argument('--piano-bank', type=int, default=0, help='bank of the piano preset on channel 0 (default 0)')
    ap.add_argument('--piano-prog', type=int, default=0, help='program of the piano preset on channel 0 (default 0)')
    ap.add_argument('--velocity-curve', type=float, default=1.0, help='touch curve: out = 127*(in/127)**CURVE; 1 = unchanged, <1 more sensitive, >1 harder')
    ap.add_argument(
        '--res-floor',
        type=float,
        default=-64.0,
        help='dBFS: string-resonance voices quieter than this are not started, they only use up voices of the synthesizer (default -64; -200 = start all)',
    )
    ap.add_argument(
        '--res-burst',
        type=int,
        default=0,
        help='at most this many resonance voices when the pedal lifts, the loudest ones, with the others given back as level (default 0 = no limit)',
    )
    ap.add_argument('--res-makeup', type=float, default=6.0, help='dB: the most that a limited burst gives back as level (default 6)')
    args = ap.parse_args()
    core = FXCore(
        args.hammer_boost,
        args.res_boost,
        args.pedal_boost,
        hammer_exp=args.hammer_exp,
        randomness=args.random,
        velocity_curve=args.velocity_curve,
        piano_bank=args.piano_bank,
        piano_prog=args.piano_prog,
        res_floor=args.res_floor,
        res_burst=args.res_burst,
        res_makeup=args.res_makeup,
    )
    bridge = Bridge(core)
    start_midi_player(bridge)
    bridge.run()


if __name__ == '__main__':
    main()
