#!/bin/bash
# Builds the complete reusable image with stand-in banks (the packages from dist/apk, Alpine from the pinned mirror) and checks what comes
# out: the ZIP, its README and manifest. The image is read back and its programs are run inside the builder (sd-image-inner.sh does that).
# Needs Docker with arm64 (Docker Desktop on a Mac; an arm64 runner in CI) and network access for the Alpine packages.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/exxpiano-sd-e2e.XXXXXX")"
cleanup() { rm -rf "$WORK"; }
trap cleanup EXIT

VERSION="$(tr -d '[:space:]' < "$ROOT/VERSION")"
[ -s "$ROOT/dist/apk/exxpiano/aarch64/exxpiano-${VERSION//-/_}-r0.apk" ] || python3 -I "$ROOT/scripts/build-apks.py" --out "$ROOT/dist/apk"

printf 'stand-in piano bank\n' > "$WORK/piano.sf2"
printf 'stand-in smaller piano bank\n' > "$WORK/piano-lite.sf2"
printf 'stand-in mechanics bank\n' > "$WORK/mech.sf2"
printf 'stand-in preview bank\n' > "$WORK/piano-preview.sf2"
SD_AP_PASSWORD='setup-pass-1' python3 -I "$ROOT/scripts/build-sd-image.py" --apk-repo "$ROOT/dist/apk" \
  --piano "$WORK/piano.sf2" --piano-lite "$WORK/piano-lite.sf2" --mech "$WORK/mech.sf2" --piano-preview "$WORK/piano-preview.sf2" --country RU --timezone Europe/Moscow --out "$WORK/out"

ZIP="$(ls "$WORK"/out/exxpiano-rpi-v"$VERSION"-alpine-3.24.*-sd.zip)"
test -s "$ZIP"
test -s "$WORK/out/manifest.json"
test -s "$WORK/out/README.txt"
python3 - "$WORK/out/manifest.json" "$ZIP" <<'PY'
import hashlib, json, sys
m = json.load(open(sys.argv[1]))
assert m['product'] == 'exxpiano' and m['target_family'] == 'rpi', m
assert m['alpine_version'].startswith('3.24.'), m['alpine_version']
assert any(p.startswith('exxpiano-') for p in m['packages']) and any(p.startswith('linux-rpi-') for p in m['packages'])
h = hashlib.sha256(open(sys.argv[2], 'rb').read()).hexdigest()
assert m['image_sha256'] == h, 'the manifest names another ZIP'
print('manifest: ok (%d packages, Alpine %s)' % (len(m['packages']), m['alpine_version']))
PY
if grep -q 'setup-pass-1' "$WORK/out/manifest.json"; then echo "the setup password leaked into the manifest"; exit 1; fi
echo "image pipeline: ok ($(( $(wc -c < "$ZIP") / 1048576 )) MB ZIP)"   # wc: the same on Linux and a Mac (stat -f means another thing on Linux)
