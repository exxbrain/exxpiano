#!/bin/bash
# Builds and verifies a complete personalized image using the small DietPi stand-in.
# Needs Docker with privileged containers and network access for debootstrap/apt.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/piano-sd-e2e.XXXXXX")"
cleanup() { rm -rf "$WORK"; }
trap cleanup EXIT

docker build -q -t exxpiano-sd-builder -f "$ROOT/scripts/sd-builder.Dockerfile" "$ROOT/scripts" >/dev/null
docker run --rm --privileged -v "$ROOT:/repo:ro" -v "$WORK:/out" exxpiano-sd-builder \
  bash /repo/scripts/sd-fake-dietpi.sh /out/DietPi_RPi234-ARMv8-test.img 900

printf 'stand-in piano bank\n' > "$WORK/piano.sf2"
printf 'stand-in mechanics bank\n' > "$WORK/mech.sf2"
EXXPIANO_SD_WORK="$WORK/build-work" python3 "$ROOT/scripts/build-sd-image.py" \
  --dietpi "$WORK/DietPi_RPi234-ARMv8-test.img" \
  --piano "$WORK/piano.sf2" --mech "$WORK/mech.sf2" \
  --serial 00000000d2f2f1de --number 1 \
  --registry "$WORK/registry.csv" --out "$WORK/out"

VERSION="$(tr -d '\n' < "$ROOT/VERSION")"
OUT="$WORK/out/0001"
ZIP="$OUT/exxpiano-rpi234-v${VERSION}-dietpi-10.7.0-0001-sn-00000000d2f2f1de-sd.zip"
test -s "$ZIP"
test -s "$OUT/manifest-0001.json"
test -s "$OUT/README-0001.txt"
python3 -c 'import json,sys; m=json.load(open(sys.argv[1])); assert m["dietpi_version"]=="10.7.0"; assert m["serial"]=="00000000d2f2f1de"; assert m["image"]==sys.argv[2]' \
  "$OUT/manifest-0001.json" "$(basename "$ZIP")"
echo "full SD-image pipeline passed: $(basename "$ZIP")"
