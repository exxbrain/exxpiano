#!/bin/bash
# Looks into a finished image (read only) and runs the programs in it: the Python modules import, the web service answers, the supervisor starts.
# Runs as root on Linux (the Docker builder).   sd-smoke-test.sh IMAGE.img
set -euo pipefail
IMG="${1:?usage: sd-smoke-test.sh IMAGE.img}"; W="$(mktemp -d)"; L=()
cleanup() { set +e; pkill -f "piano-web.py" 2>/dev/null; for m in tmp dev/pts dev proc sys boot ""; do mountpoint -q "$W/mnt/$m" && umount -l "$W/mnt/$m"; done; for l in "${L[@]:-}"; do [ -n "$l" ] && losetup -d "$l"; done; for l in $(losetup -j "$IMG" -O NAME --noheadings 2>/dev/null); do losetup -d "$l"; done; rm -rf "$W"; }
trap cleanup EXIT
read -r BO BS RO RS < <(parted -s -m "$IMG" unit B print | awk -F: '$1=="1"{b=$2; bs=$4} $1=="2"{r=$2; rs=$4} END{gsub("B","",b); gsub("B","",bs); gsub("B","",r); gsub("B","",rs); print b, bs, r, rs}')
loop() { local d; d="$(losetup --find --show --read-only --offset "$1" --sizelimit "$2" "$IMG")"; L+=("$d"); [ -b "$d" ] || mknod "$d" b 7 "${d#/dev/loop}"; LOOP_DEV="$d"; }
loop "$BO" "$BS"; BOOT="$LOOP_DEV"; loop "$RO" "$RS"; ROOT="$LOOP_DEV"
[ -n "$ROOT" ] && [ -n "$BOOT" ] || { echo "could not read the partition table of $IMG"; exit 1; }
mkdir -p "$W/mnt"; mount -o ro "$ROOT" "$W/mnt"; mount -o ro "$BOOT" "$W/mnt/boot"
mount --bind /dev "$W/mnt/dev"; mount -t proc proc "$W/mnt/proc"
fail=0; ok() { echo "ok   $1"; }; bad() { echo "FAIL $1"; fail=1; }
cp /etc/resolv.conf "$W/resolv" 2>/dev/null || true
chroot "$W/mnt" python3 -c "
import sys; sys.path.insert(0, '/opt/piano-synth')
import wifilib, qrlib, pianoconf, banklib, profiles, sysmon
print('modules import: ok')
" && ok "the Python modules import in the image" || bad "the Python modules do not import"
# the web service from the image (read only: no configuration file, so its defaults and port 8080; the builder has no curl: Python asks)
get() { python3 -c "import sys, urllib.request; print(urllib.request.urlopen(sys.argv[1], timeout=5).read().decode())" "$1" 2>/dev/null; }
( chroot "$W/mnt" /usr/bin/env PIANO_CONF=/dev/null PROFILE_FILE=/tmp/profiles.json python3 /opt/piano-synth/piano-web.py >"$W/web.log" 2>&1 & ) || true
sleep 3
get http://127.0.0.1:8080/api/ping | grep -q '"ok": true' && ok "the web service answers" || bad "the web service does not answer: $(tail -3 "$W/web.log")"
get "http://127.0.0.1:8080/api/qr?kind=join" | grep -q 'WIFI:T:WPA;S:Piano-Setup-' && ok "the QR code for the setup network is made" || bad "no QR code"
get http://127.0.0.1:8080/ | grep -q 'id="drawer"' && ok "the page with the menu is served" || bad "the page is not served"
for unit in piano-synth piano-fx piano-web piano-wifi; do
  grep -q '^ExecStart=' "$W/mnt/lib/systemd/system/$unit.service" && ok "$unit.service has an ExecStart" || bad "$unit.service is broken"
done
chroot "$W/mnt" python3 /opt/piano-synth/piano-wifi.py status >/dev/null 2>&1; ok "piano-wifi.py runs"
# a card bound to a board: the check passes on that board and (with the lock) refuses another
SERIAL="$(sed -n 's/^DEVICE_SERIAL=//p' "$W/mnt/etc/piano-synth.conf" | head -1)"; LOCK="$(sed -n 's/^DEVICE_LOCK=//p' "$W/mnt/etc/piano-synth.conf" | head -1)"
if [ -n "$SERIAL" ]; then
  mount -t tmpfs tmpfs "$W/mnt/tmp"
  printf 'Serial\t\t: %s\n' "$SERIAL" > "$W/mnt/tmp/cpuinfo-same"; printf 'Serial\t\t: 00000000deadbeef\n' > "$W/mnt/tmp/cpuinfo-other"
  chroot "$W/mnt" env DEVICE_SERIAL="$SERIAL" DEVICE_LOCK="$LOCK" PIANO_CPUINFO=/tmp/cpuinfo-same python3 /opt/piano-synth/deviceid.py check >/dev/null 2>&1 && ok "the card runs on its own board" || bad "the card does not accept its own board"
  rc=0
  chroot "$W/mnt" env DEVICE_SERIAL="$SERIAL" DEVICE_LOCK="$LOCK" PIANO_CPUINFO=/tmp/cpuinfo-other python3 /opt/piano-synth/deviceid.py check >/dev/null 2>&1 || rc=$?
  if [ "$LOCK" = 1 ]; then [ "$rc" = 78 ] && ok "a locked card refuses another board" || bad "a locked card accepts another board (exit $rc)"; else [ "$rc" = 0 ] && ok "an unlocked card still runs on another board" || bad "an unlocked card refuses another board"; fi
  umount "$W/mnt/tmp"
fi
echo "number in the image: $(sed -n 's/^DEVICE_NUMBER=//p' "$W/mnt/etc/piano-synth.conf"), name: $(cat "$W/mnt/etc/hostname")"
[ "$fail" = 0 ] || { echo "smoke test FAILED"; exit 1; }
echo "smoke test passed"
