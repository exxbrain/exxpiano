"""The environment pi/exxpiano-run starts with in the tests: nothing in it comes from the machine or from the environment the suite runs in."""

import os

# every variable exxpiano-run (and banklib, which it starts) reads: the tests expect them at their defaults unless a test sets them
VARIABLES = (
    'ALSA_DEVICE',
    'ALSA_TAP_CONF',
    'ASOUND_DIR',
    'BANK_DIR',
    'BANKLIB',
    'CMD_FIFO',
    'CPU_CORES',
    'CPU_COUNT',
    'CPU_GOVERNOR',
    'CPUFREQ_DIR',
    'DRY_RUN',
    'EXXPIANO_BANK_STATE',
    'EXXPIANO_CONF',
    'EXXPIANO_MEMINFO',
    'EXXPIANO_PREVIEW_FAILED',
    'FLUIDSYNTH',
    'FX_ARGS',
    'GAIN',
    'MECH_SF2',
    'METER',
    'METER_DIR',
    'METER_PROGRAM',
    'PERIOD_SIZE',
    'PERIODS',
    'PIANO_SF2',
    'PIANO_SF2_LAST_GOOD',
    'POLYPHONY',
    'PREVIEW_SF2',
    'PROC_UPTIME',
    'REVERB',
    'REVERB_DAMP',
    'REVERB_LEVEL',
    'REVERB_ROOM',
    'REVERB_SEND',
    'REVERB_WIDTH',
    'SAMPLE_RATE',
    'SF2INFO',
    'SYNTH_PROGRAM',
    'USB_PERIOD_SIZE',
    'USB_PERIODS',
    'USB_SETTLE',
    'USB_SYSFS',
    'USB_WAIT',
)

# files bash itself reads before the script when these are set: they could change the settings above or print into the output
SHELL_STARTUP = ('BASH_ENV', 'ENV')


def clean_environ(tmp):
    """The caller's environment without those variables, and the host paths whose files change what exxpiano-run does moved into `tmp`, where
    nothing exists unless a test makes it. On a Pi the defaults are real files: the ALSA file of the level meter (the output would go through
    exxpiano_tap), the preview bank (exxpiano-synth would start instead of fluidsynth), /proc/uptime and the USB devices (a wait at boot)."""
    env = {k: v for k, v in os.environ.items() if k not in VARIABLES and k not in SHELL_STARTUP}
    env.update(
        ALSA_TAP_CONF=os.path.join(tmp, 'no-tap.conf'),
        METER_PROGRAM=os.path.join(tmp, 'no-meter.py'),
        METER_DIR=os.path.join(tmp, 'meter'),
        PREVIEW_SF2='',
        EXXPIANO_PREVIEW_FAILED=os.path.join(tmp, 'preview.failed'),
        EXXPIANO_BANK_STATE=os.path.join(tmp, 'bank.json'),
        EXXPIANO_CONF=os.path.join(tmp, 'no-exxpiano.conf'),
        BANK_DIR=os.path.join(tmp, 'no-banks'),
        PROC_UPTIME=os.path.join(tmp, 'no-uptime'),
        USB_SYSFS=os.path.join(tmp, 'no-usb'),
        CMD_FIFO=os.path.join(tmp, 'cmd'),
    )
    return env
