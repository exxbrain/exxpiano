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
cat > "$WORK/boards.csv" <<'EOF'
serial,number,name,note
00000000d2f2f1de,0001,,stand-in one
00000000aaaabbbb,0002,,stand-in two
EOF
EXXPIANO_SD_WORK="$WORK/build-work" python3 "$ROOT/scripts/build-sd-image.py" \
  --dietpi "$WORK/DietPi_RPi234-ARMv8-test.img" \
  --piano "$WORK/piano.sf2" --mech "$WORK/mech.sf2" \
  --serials "$WORK/boards.csv" \
  --registry "$WORK/registry.csv" --out "$WORK/out"

VERSION="$(tr -d '\n' < "$ROOT/VERSION")"
for IDENTITY in "0001:00000000d2f2f1de" "0002:00000000aaaabbbb"; do
  NUMBER="${IDENTITY%%:*}"
  SERIAL="${IDENTITY#*:}"
  OUT="$WORK/out/$NUMBER"
  ZIP="$OUT/exxpiano-rpi234-v${VERSION}-dietpi-10.7.0-${NUMBER}-sn-${SERIAL}-sd.zip"
  test -s "$ZIP"
  test -s "$OUT/manifest-${NUMBER}.json"
  test -s "$OUT/README-${NUMBER}.txt"
  python3 -c 'import json,sys; m=json.load(open(sys.argv[1])); assert m["dietpi_version"]=="10.7.0"; assert m["device_number"]==sys.argv[2]; assert m["serial"]==sys.argv[3]; assert m["image"]==sys.argv[4]' \
    "$OUT/manifest-${NUMBER}.json" "$NUMBER" "$SERIAL" "$(basename "$ZIP")"
done
test "$(($(wc -l < "$WORK/registry.csv") - 1))" -eq 2
echo "full two-device SD-image pipeline passed with exact versioned filenames"
