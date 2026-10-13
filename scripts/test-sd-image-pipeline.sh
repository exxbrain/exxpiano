#!/bin/bash
# Builds and verifies the reusable image using the small DietPi stand-in.
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
  --out "$WORK/out"

VERSION="$(tr -d '\n' < "$ROOT/VERSION")"
ZIP="$WORK/out/exxpiano-rpi234-v${VERSION}-dietpi-10.7.0-sd.zip"
test -s "$ZIP"
test -s "$WORK/out/manifest-rpi234.json"
test -s "$WORK/out/README-rpi234.txt"
python3 -c 'import json,sys; m=json.load(open(sys.argv[1])); assert m["dietpi_version"]=="10.7.0"; assert m["target_family"]=="rpi234"; assert m["installation_identity"]=="generated-at-first-boot"; assert m["image"]==sys.argv[2]; assert "serial" not in m and "device_number" not in m' \
  "$WORK/out/manifest-rpi234.json" "$(basename "$ZIP")"
echo "full reusable SD-image pipeline passed for the Raspberry Pi 2/3/4 with the exact versioned filename"

# the Raspberry Pi 5 target on the same stand-in (the layout is the same): its own names, no jack setup, no lite bank
EXXPIANO_SD_WORK="$WORK/build-work" python3 "$ROOT/scripts/build-sd-image.py" --target rpi5 \
  --dietpi "$WORK/DietPi_RPi234-ARMv8-test.img" \
  --piano "$WORK/piano.sf2" --mech "$WORK/mech.sf2" \
  --out "$WORK/out"
ZIP5="$WORK/out/exxpiano-rpi5-v${VERSION}-dietpi-10.7.0-sd.zip"
test -s "$ZIP5"
test -s "$WORK/out/manifest-rpi5.json"
test -s "$WORK/out/README-rpi5.txt"
python3 -c 'import json,sys; m=json.load(open(sys.argv[1])); assert m["target_family"]=="rpi5"; assert m["image"]==sys.argv[2]; assert not any("lite" in b for b in m["banks"])' \
  "$WORK/out/manifest-rpi5.json" "$(basename "$ZIP5")"
echo "full reusable SD-image pipeline passed for the Raspberry Pi 5 too"
