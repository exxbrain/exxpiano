# Notes for reviews of exxpiano

- exxpiano runs on a Raspberry Pi (3/4/5, from 512 MB of memory) under Alpine Linux 3.24 (OpenRC, eudev, busybox syslog, musl): FluidSynth plays SoundFont banks, `pi/exxpiano-fx.py` adds the piano mechanics, `pi/exxpiano-web.py` is the web interface. The services are OpenRC scripts in `pi/init.d/`, the packages are signed `.apk` (`packaging/alpine/`), the image is built by `scripts/build-sd-image.py`. Look for what costs CPU or memory on such a board, and for anything that could stop the sound or lock the user out of the device (Wi-Fi setup, configuration writes).
- The house style is deliberate and checked by `ruff.toml` (see its `ignore` list): `dict(a=1)` records, `%`-formatting, short statements joined with `;`, long lines with a comment at the end. Do not ask to change these.
- Tools in `tools/` and `scripts/` must also run on the python3 of macOS (3.9); code in `pi/` runs on Python 3.11 or newer.
- Claims about sound, timing or hardware must say whether they were tried on a real Raspberry Pi; the README and CHANGELOG mark what was not.
- Tests: `python3 -m unittest discover -s tests`; code checks: `scripts/lint.sh`; packages, services, upgrade and image in Alpine containers: `scripts/test-openrc-services.sh`, `scripts/test-apk-upgrade.sh`, `scripts/test-sd-image-pipeline.sh`.
