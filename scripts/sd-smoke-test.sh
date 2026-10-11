#!/bin/bash
# Looks into a finished image (read only) and runs the programs in it: the Python modules import, the web service answers, the supervisor starts.
# Runs as root on Linux (the Docker builder).   sd-smoke-test.sh IMAGE.img
set -euo pipefail

check_required_layout() {
  local root="$1" boot="$2" fail=0
  required() { if eval "$2"; then echo "ok   $1"; else echo "FAIL $1"; fail=1; fi; }
  required "DietPi boot configuration" "[ -s '$boot/dietpi.txt' ] && [ -s '$boot/dietpi/.version' ] && [ -x '$boot/Automation_Custom_Script.sh' ]"
  required "the installed application" "[ -x '$root/opt/exxpiano/exxpiano-web.py' ] && [ -x '$root/opt/exxpiano/exxpiano-wifi.py' ] && [ -f '$root/lib/systemd/system/exxpiano.service' ]"
  required "both mandatory banks" "[ -s '$root/opt/exxpiano/soundfonts/piano.sf2' ] && [ -s '$root/opt/exxpiano/soundfonts/mech.sf2' ]"
  required "the mandatory bank configuration" "grep -qx 'PIANO_SF2=/opt/exxpiano/soundfonts/piano.sf2' '$root/etc/exxpiano.conf' && grep -qx 'MECH_SF2=/opt/exxpiano/soundfonts/mech.sf2' '$root/etc/exxpiano.conf'"
  required "the locked board serial" "grep -Eq '^DEVICE_SERIAL=[0-9a-f]{16}$' '$root/etc/exxpiano.conf' && grep -qx 'DEVICE_LOCK=1' '$root/etc/exxpiano.conf'"
  [ "$fail" = 0 ]
}

if [ "${1:-}" = "--check-tree" ]; then
  [ "$#" = 3 ] || { echo "usage: sd-smoke-test.sh --check-tree ROOT BOOT" >&2; exit 2; }
  check_required_layout "$2" "$3"
  exit
fi

IMG="${1:?usage: sd-smoke-test.sh IMAGE.img}"; W="$(mktemp -d)"; L=(); BOOT_MOUNT=""
cleanup() { set +e; pkill -f "exxpiano-web.py" 2>/dev/null; [ -n "$BOOT_MOUNT" ] && mountpoint -q "$BOOT_MOUNT" && umount -l "$BOOT_MOUNT"; for m in tmp dev/pts dev proc sys ""; do mountpoint -q "$W/mnt/$m" && umount -l "$W/mnt/$m"; done; for l in "${L[@]:-}"; do [ -n "$l" ] && losetup -d "$l"; done; for l in $(losetup -j "$IMG" -O NAME --noheadings 2>/dev/null); do losetup -d "$l"; done; rm -rf "$W"; }
trap cleanup EXIT
read -r BO BS RO RS < <(parted -s -m "$IMG" unit B print | awk -F: '$1=="1"{b=$2; bs=$4} $1=="2"{r=$2; rs=$4} END{gsub("B","",b); gsub("B","",bs); gsub("B","",r); gsub("B","",rs); print b, bs, r, rs}')
loop() { local d; d="$(losetup --find --show --read-only --offset "$1" --sizelimit "$2" "$IMG")"; L+=("$d"); [ -b "$d" ] || mknod "$d" b 7 "${d#/dev/loop}"; LOOP_DEV="$d"; }
loop "$BO" "$BS"; BOOT="$LOOP_DEV"; loop "$RO" "$RS"; ROOT="$LOOP_DEV"
[ -n "$ROOT" ] && [ -n "$BOOT" ] || { echo "could not read the partition table of $IMG"; exit 1; }
mkdir -p "$W/mnt"; mount -o ro "$ROOT" "$W/mnt"
BOOTDIR="$(awk '$3=="vfat"{print $2; exit}' "$W/mnt/etc/fstab" 2>/dev/null || true)"; BOOTDIR="${BOOTDIR:-/boot}"
BOOT_MOUNT="$W/mnt$BOOTDIR"; mkdir -p "$BOOT_MOUNT"; mount -o ro "$BOOT" "$BOOT_MOUNT"
mount --bind /dev "$W/mnt/dev"; mount -t proc proc "$W/mnt/proc"
fail=0; ok() { echo "ok   $1"; }; bad() { echo "FAIL $1"; fail=1; }
check_required_layout "$W/mnt" "$BOOT_MOUNT" || fail=1
chroot "$W/mnt" dpkg -s exxpiano 2>/dev/null | grep -q '^Status: install ok installed' && ok "the package is installed" || bad "the package is not installed"
cp /etc/resolv.conf "$W/resolv" 2>/dev/null || true
chroot "$W/mnt" python3 -c "
import sys; sys.path.insert(0, '/opt/exxpiano')
import wifilib, qrlib, exxpianoconf, banklib, profiles, sysmon
print('modules import: ok')
" && ok "the Python modules import in the image" || bad "the Python modules do not import"
# the web service from the image (read only: no configuration file, so its defaults and port 8080; the builder has no curl: Python asks)
get() { python3 -c "import sys, urllib.request; print(urllib.request.urlopen(sys.argv[1], timeout=5).read().decode())" "$1" 2>/dev/null; }
( chroot "$W/mnt" /usr/bin/env EXXPIANO_CONF=/dev/null PROFILE_FILE=/tmp/profiles.json python3 /opt/exxpiano/exxpiano-web.py >"$W/web.log" 2>&1 & ) || true
sleep 3
get http://127.0.0.1:8080/api/ping | grep -q '"ok": true' && ok "the web service answers" || bad "the web service does not answer: $(tail -3 "$W/web.log")"
get "http://127.0.0.1:8080/api/qr?kind=join" | grep -q 'WIFI:T:WPA;S:exxpiano-Setup-' && ok "the QR code for the setup network is made" || bad "no QR code"
get http://127.0.0.1:8080/ | grep -q 'id="drawer"' && ok "the page with the menu is served" || bad "the page is not served"
for unit in exxpiano exxpiano-fx exxpiano-web exxpiano-wifi; do
  grep -q '^ExecStart=' "$W/mnt/lib/systemd/system/$unit.service" && ok "$unit.service has an ExecStart" || bad "$unit.service is broken"
done
chroot "$W/mnt" python3 /opt/exxpiano/exxpiano-wifi.py status >/dev/null 2>&1; ok "exxpiano-wifi.py runs"
# a card bound to a board: the check passes on that board and (with the lock) refuses another
SERIAL="$(sed -n 's/^DEVICE_SERIAL=//p' "$W/mnt/etc/exxpiano.conf" | head -1)"; LOCK="$(sed -n 's/^DEVICE_LOCK=//p' "$W/mnt/etc/exxpiano.conf" | head -1)"
if [ -n "$SERIAL" ]; then
  mount -t tmpfs tmpfs "$W/mnt/tmp"
  printf 'Serial\t\t: %s\n' "$SERIAL" > "$W/mnt/tmp/cpuinfo-same"; printf 'Serial\t\t: 00000000deadbeef\n' > "$W/mnt/tmp/cpuinfo-other"; printf 'Model\t\t: Raspberry Pi\n' > "$W/mnt/tmp/cpuinfo-missing"
  chroot "$W/mnt" env DEVICE_SERIAL="$SERIAL" DEVICE_LOCK="$LOCK" EXXPIANO_CPUINFO=/tmp/cpuinfo-same python3 /opt/exxpiano/deviceid.py check >/dev/null 2>&1 && ok "the card runs on its own board" || bad "the card does not accept its own board"
  rc=0
  chroot "$W/mnt" env DEVICE_SERIAL="$SERIAL" DEVICE_LOCK="$LOCK" EXXPIANO_CPUINFO=/tmp/cpuinfo-other python3 /opt/exxpiano/deviceid.py check >/dev/null 2>&1 || rc=$?
  if [ "$LOCK" = 1 ]; then [ "$rc" = 78 ] && ok "a locked card refuses another board" || bad "a locked card accepts another board (exit $rc)"; else [ "$rc" = 0 ] && ok "an unlocked card still runs on another board" || bad "an unlocked card refuses another board"; fi
  rc=0
  chroot "$W/mnt" env DEVICE_SERIAL="$SERIAL" DEVICE_LOCK="$LOCK" EXXPIANO_CPUINFO=/tmp/cpuinfo-missing python3 /opt/exxpiano/deviceid.py check >/dev/null 2>&1 || rc=$?
  if [ "$LOCK" = 1 ]; then [ "$rc" = 78 ] && ok "a locked card refuses an unreadable board serial" || bad "a locked card runs without a readable board serial (exit $rc)"; else [ "$rc" = 0 ] && ok "an unlocked card runs without a readable board serial" || bad "an unlocked card refuses an unreadable board serial"; fi
  umount "$W/mnt/tmp"
fi
echo "number in the image: $(sed -n 's/^DEVICE_NUMBER=//p' "$W/mnt/etc/exxpiano.conf"), name: $(cat "$W/mnt/etc/hostname")"
[ "$fail" = 0 ] || { echo "smoke test FAILED"; exit 1; }
echo "smoke test passed"
