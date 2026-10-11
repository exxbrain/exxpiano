#!/bin/bash
# Turns a DietPi image for the Raspberry Pi into the image of one exxpiano device: the software, the sound banks, the number and the name of the
# device, DietPi's first-boot automation. Runs as root on Linux (build-sd-image.py starts it in Docker on a Mac).
#
#   sd-image-inner.sh --image IN.img[.xz] --out OUT.img --dietpi-version-out VERSION.txt --deb exxpiano_X_all.deb --piano P.sf2 --mech M.sf2 --number 0007 --serial SERIAL --lock [options]
#
# Options: --serial S (the board this card is made for: 8-16 hex digits)  --lock (the synthesizer refuses another board)  --hostname NAME (default exxpiano-<number>)  --password PW (DietPi's global password)  --ap-password PW  --country RU  --timezone Europe/Moscow
#          --locale en_GB.UTF-8  --keyboard us  --extra-mb N (room added to the image)  --workdir DIR
# The image is NOT started here: nothing can be tried without a Pi. It is checked by reading it back (the last step).
set -euo pipefail

IMAGE="" OUT="" DIETPI_VERSION_OUT="" DEB="" PIANO="" MECH="" NUMBER="" HOSTNAME_NEW="" PASSWORD="" AP_PASSWORD="" COUNTRY="GB" TIMEZONE="UTC" LOCALE="en_GB.UTF-8"
KEYBOARD="us" SERIAL="" LOCK=0 EXTRA_MB="" WORKDIR="${TMPDIR:-/tmp}/piano-sd-work" NETWORK_IN_CHROOT=1
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

die() { echo "sd-image: $*" >&2; exit 1; }
say() { echo "== $*"; }

while [ $# -gt 0 ]; do
  case "$1" in
    --image) IMAGE="$2"; shift 2 ;;
    --out) OUT="$2"; shift 2 ;;
    --dietpi-version-out) DIETPI_VERSION_OUT="$2"; shift 2 ;;
    --deb) DEB="$2"; shift 2 ;;
    --piano) PIANO="$2"; shift 2 ;;
    --mech) MECH="$2"; shift 2 ;;
    --number) NUMBER="$2"; shift 2 ;;
    --hostname) HOSTNAME_NEW="$2"; shift 2 ;;
    --password) PASSWORD="$2"; shift 2 ;;
    --ap-password) AP_PASSWORD="$2"; shift 2 ;;
    --country) COUNTRY="$2"; shift 2 ;;
    --timezone) TIMEZONE="$2"; shift 2 ;;
    --locale) LOCALE="$2"; shift 2 ;;
    --keyboard) KEYBOARD="$2"; shift 2 ;;
    --serial) SERIAL="$2"; shift 2 ;;
    --lock) LOCK=1; shift ;;
    --extra-mb) EXTRA_MB="$2"; shift 2 ;;
    --workdir) WORKDIR="$2"; shift 2 ;;
    -h|--help) sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) die "unknown option: $1" ;;
  esac
done

# the passwords may come through the environment (build-sd-image.py does so: arguments show in the list of processes)
PASSWORD="${PASSWORD:-${SD_PASSWORD:-}}"; AP_PASSWORD="${AP_PASSWORD:-${SD_AP_PASSWORD:-}}"
[ "$(id -u)" = 0 ] || die "run as root (the image is mounted)"
[ -f "$IMAGE" ] || die "no such image: $IMAGE"
[ -f "$DEB" ] || die "no such package: $DEB"
[ -n "$OUT" ] || die "--out is required"
[ -n "$DIETPI_VERSION_OUT" ] || die "--dietpi-version-out is required"
[[ "$NUMBER" =~ ^[0-9A-Za-z]{1,8}$ ]] || die "--number: 1 to 8 letters or digits"
[[ "$SERIAL" =~ ^[0-9a-fA-F]{8,16}$ ]] || die "--serial is required: 8 to 16 hex digits (cat /proc/cpuinfo shows the Serial of the board)"
SERIAL="$(printf '%016s' "$(echo "$SERIAL" | tr A-F a-f)" | tr ' ' 0)"
[ "$LOCK" = 1 ] || die "--lock is required for a product image"
NUMBER="$(echo "$NUMBER" | tr a-z A-Z)"
[[ "$NUMBER" =~ ^[0-9]+$ ]] && NUMBER="$(printf '%04d' "$((10#$NUMBER))")"        # 7 -> 0007 (the same rule as sd-registry.py)
[ -n "$HOSTNAME_NEW" ] || HOSTNAME_NEW="exxpiano-$(echo "$NUMBER" | tr A-Z a-z)"
[[ "$HOSTNAME_NEW" =~ ^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$ ]] || die "--hostname: lowercase letters, digits and hyphens"
[ -r "$PIANO" ] || die "--piano is required and must be readable"
[ -r "$MECH" ] || die "--mech is required and must be readable"
for t in losetup mount umount parted e2fsck resize2fs chroot truncate sed grep awk; do command -v "$t" >/dev/null || die "missing tool: $t"; done

mkdir -p "$WORKDIR"; WORKDIR="$(cd "$WORKDIR" && pwd)"
DISK="$WORKDIR/disk.img"; MNT="$WORKDIR/mnt"; LOOPS=()
cleanup() {
  set +e
  for m in "$MNT/boot/firmware" "$MNT/boot" "$MNT/dev/pts" "$MNT/dev" "$MNT/proc" "$MNT/sys" "$MNT/run" "$MNT"; do mountpoint -q "$m" && umount -l "$m"; done
  for l in "${LOOPS[@]:-}"; do [ -n "$l" ] && losetup -d "$l" 2>/dev/null; done
  for l in $(losetup -j "$DISK" -O NAME --noheadings 2>/dev/null); do losetup -d "$l" 2>/dev/null; done      # whatever is still attached to the image
}
trap cleanup EXIT

# a loop device for a byte range of the image (partition nodes of --partscan do not appear without udev, e.g. in Docker)
loop_part() {   # offset size -> the device in $LOOP_DEV (not printed: a $(...) would lose the list that cleanup needs)
  local dev; dev="$(losetup --find --show --offset "$1" --sizelimit "$2" "$DISK")"
  LOOPS+=("$dev")
  if [ ! -b "$dev" ]; then mknod "$dev" b 7 "${dev#/dev/loop}"; fi
  LOOP_DEV="$dev"
}
set_kv() {      # file KEY VALUE: replaces KEY=... (or #KEY=...), appends when absent; the value is taken literally
  local f="$1" k="$2" v="$3" tmp; tmp="$(mktemp)"
  K="$k" V="$v" awk 'BEGIN{done=0; k=ENVIRON["K"]; v=ENVIRON["V"]} index($0, k "=")==1 && !done {print k "=" v; done=1; next} index($0, "#" k "=")==1 && !done {print k "=" v; done=1; next} {print} END{if(!done) print k "=" v}' "$f" > "$tmp"
  cat "$tmp" > "$f"; rm -f "$tmp"
}

quote_conf() {   # a value for /etc/exxpiano.conf: in double quotes, \ and " escaped (systemd and the programs read it back unchanged)
  printf '"%s"' "$(printf '%s' "$1" | sed 's/[\\"]/\\&/g')"
}

say "1/9 The DietPi image"
rm -rf "$MNT" "$DISK"; mkdir -p "$MNT"
case "$IMAGE" in
  *.xz) xz -dc "$IMAGE" > "$DISK" ;;
  *) cp "$IMAGE" "$DISK" ;;
esac
SIZE_BANKS=$(( ( $(stat -c %s "$PIANO") + $(stat -c %s "$MECH") ) / 1048576 ))
[ -n "$EXTRA_MB" ] || EXTRA_MB=$(( SIZE_BANKS + 400 ))
echo "image $(( $(stat -c %s "$DISK") / 1048576 )) MB, banks $SIZE_BANKS MB, room added $EXTRA_MB MB"

say "2/9 Making room (the second partition grows; DietPi grows it again to the whole card at the first start)"
truncate -s "+${EXTRA_MB}M" "$DISK"
parted -s "$DISK" -- resizepart 2 100% >/dev/null || die "could not resize the second partition (is this a DietPi image with an MBR partition table?)"
read -r BOOT_OFF BOOT_SIZE ROOT_OFF ROOT_SIZE < <(parted -s -m "$DISK" unit B print | awk -F: '$1=="1"{b=$2; bs=$4} $1=="2"{r=$2; rs=$4} END{gsub("B","",b); gsub("B","",bs); gsub("B","",r); gsub("B","",rs); print b, bs, r, rs}')
[ -n "$ROOT_OFF" ] || die "could not read the partition table"
loop_part "$BOOT_OFF" "$BOOT_SIZE"; BOOT_DEV="$LOOP_DEV"; loop_part "$ROOT_OFF" "$ROOT_SIZE"; ROOT_DEV="$LOOP_DEV"
e2fsck -fy "$ROOT_DEV" >/dev/null || [ $? -le 1 ] || die "the root file system is damaged"
resize2fs "$ROOT_DEV" >/dev/null

say "3/9 Mounting"
mount "$ROOT_DEV" "$MNT"
BOOTDIR="$(awk '$3=="vfat"{print $2; exit}' "$MNT/etc/fstab" 2>/dev/null || true)"; BOOTDIR="${BOOTDIR:-/boot}"
mkdir -p "$MNT$BOOTDIR"; mount "$BOOT_DEV" "$MNT$BOOTDIR"
echo "root: $ROOT_DEV, boot: $BOOT_DEV at $BOOTDIR"
[ -f "$MNT$BOOTDIR/dietpi.txt" ] || die "no dietpi.txt on the boot partition: not a DietPi image"
VERSION_FILE="$MNT$BOOTDIR/dietpi/.version"
DIETPI_CORE="$(sed -n 's/^G_DIETPI_VERSION_CORE=//p' "$VERSION_FILE" 2>/dev/null | head -1)"
DIETPI_SUB="$(sed -n 's/^G_DIETPI_VERSION_SUB=//p' "$VERSION_FILE" 2>/dev/null | head -1)"
DIETPI_RC="$(sed -n 's/^G_DIETPI_VERSION_RC=//p' "$VERSION_FILE" 2>/dev/null | head -1)"
[[ "$DIETPI_CORE" =~ ^[0-9]+$ && "$DIETPI_SUB" =~ ^[0-9]+$ && "$DIETPI_RC" =~ ^[0-9]+$ ]] || die "cannot read a complete DietPi version from dietpi/.version"
DIETPI_VERSION="$DIETPI_CORE.$DIETPI_SUB.$DIETPI_RC"

say "4/9 The software (installed into the image with its dependencies; the first start needs no download of ours)"
mount --bind /dev "$MNT/dev"; mount -t devpts devpts "$MNT/dev/pts" 2>/dev/null || true; mount -t proc proc "$MNT/proc"; mount -t sysfs sys "$MNT/sys"
chroot "$MNT" /bin/true 2>/dev/null || die "cannot run the image's programs: it is arm64 and this machine is $(uname -m). On x86 add qemu-user-static and binfmt (docker run --privileged tonistiigi/binfmt --install arm64)"
cp -a "$MNT/etc/resolv.conf" "$WORKDIR/resolv.conf.orig" 2>/dev/null || true
rm -f "$MNT/etc/resolv.conf"; cp /etc/resolv.conf "$MNT/etc/resolv.conf"
printf '#!/bin/sh\nexit 101\n' > "$MNT/usr/sbin/policy-rc.d"; chmod 755 "$MNT/usr/sbin/policy-rc.d"      # no service starts inside the image
cp "$DEB" "$MNT/tmp/exxpiano.deb"
chroot "$MNT" env DEBIAN_FRONTEND=noninteractive apt-get update -qq
# no recommended packages: without this a plain Debian pulls in a graphical front end for FluidSynth, Qt and Mesa (hundreds of MB)
chroot "$MNT" env DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends /tmp/exxpiano.deb avahi-daemon
chroot "$MNT" systemctl enable exxpiano exxpiano-fx exxpiano-web exxpiano-wifi avahi-daemon >/dev/null 2>&1 || chroot "$MNT" systemctl enable exxpiano exxpiano-fx exxpiano-web exxpiano-wifi
chroot "$MNT" systemctl disable hostapd >/dev/null 2>&1 || true
chroot "$MNT" apt-get clean
rm -f "$MNT/tmp/exxpiano.deb" "$MNT/usr/sbin/policy-rc.d" "$MNT/etc/resolv.conf"
[ -e "$WORKDIR/resolv.conf.orig" ] && cp -a "$WORKDIR/resolv.conf.orig" "$MNT/etc/resolv.conf"

say "5/9 Sound banks"
install -d "$MNT/opt/exxpiano/soundfonts"
install -m 644 "$PIANO" "$MNT/opt/exxpiano/soundfonts/piano.sf2"
install -m 644 "$MECH" "$MNT/opt/exxpiano/soundfonts/mech.sf2"
ls -l "$MNT/opt/exxpiano/soundfonts"

say "6/9 The number and the name of the device: $NUMBER, $HOSTNAME_NEW"
CONF="$MNT/etc/exxpiano.conf"
set_kv "$CONF" DEVICE_NUMBER "$NUMBER"
[ -z "$AP_PASSWORD" ] || set_kv "$CONF" WIFI_AP_PASSWORD "$(quote_conf "$AP_PASSWORD")"
set_kv "$CONF" DEVICE_SERIAL "$SERIAL"; set_kv "$CONF" DEVICE_LOCK "$LOCK"
echo "$HOSTNAME_NEW" > "$MNT/etc/hostname"
if grep -q '^127\.0\.1\.1' "$MNT/etc/hosts" 2>/dev/null; then sed -i "s/^127\.0\.1\.1.*/127.0.1.1\t$HOSTNAME_NEW/" "$MNT/etc/hosts"; else printf '127.0.1.1\t%s\n' "$HOSTNAME_NEW" >> "$MNT/etc/hosts"; fi

say "7/9 DietPi's first-start automation (dietpi.txt, the custom script, onboard audio)"
DT="$MNT$BOOTDIR/dietpi.txt"
set_kv "$DT" AUTO_SETUP_AUTOMATED 1
set_kv "$DT" AUTO_SETUP_ACCEPT_LICENSE 1
set_kv "$DT" AUTO_SETUP_NET_HOSTNAME "$HOSTNAME_NEW"
set_kv "$DT" AUTO_SETUP_NET_ETHERNET_ENABLED 1
set_kv "$DT" AUTO_SETUP_NET_WIFI_ENABLED 1
set_kv "$DT" AUTO_SETUP_NET_WIFI_COUNTRY_CODE "$COUNTRY"
set_kv "$DT" AUTO_SETUP_BOOT_WAIT_FOR_NETWORK 0          # the device must start without a network: the setup network is its way in
set_kv "$DT" AUTO_SETUP_LOCALE "$LOCALE"
set_kv "$DT" AUTO_SETUP_KEYBOARD_LAYOUT "$KEYBOARD"
set_kv "$DT" AUTO_SETUP_TIMEZONE "$TIMEZONE"
set_kv "$DT" AUTO_SETUP_CUSTOM_SCRIPT_EXEC 0
set_kv "$DT" SURVEY_OPTED_IN 0
[ -z "$PASSWORD" ] || set_kv "$DT" AUTO_SETUP_GLOBAL_PASSWORD "$PASSWORD"
install -m 755 "$HERE/../dietpi/Automation_Custom_Script.image.sh" "$MNT$BOOTDIR/Automation_Custom_Script.sh"
CFG="$MNT$BOOTDIR/config.txt"
[ -f "$CFG" ] && set_kv "$CFG" dtparam=audio on        # the 3.5 mm jack is off by default on DietPi

say "8/9 Reading the image back"
fail=0
check() { if eval "$2"; then echo "ok   $1"; else echo "FAIL $1"; fail=1; fi; }
check "the package is installed" "chroot '$MNT' dpkg -s exxpiano 2>/dev/null | grep -q '^Status: install ok installed'"
check "the programs are there" "[ -x '$MNT/opt/exxpiano/exxpiano-web.py' ] && [ -x '$MNT/opt/exxpiano/exxpiano-wifi.py' ] && [ -f '$MNT/opt/exxpiano/qrlib.py' ]"
for u in exxpiano exxpiano-fx exxpiano-web exxpiano-wifi; do
  check "$u starts at boot" "[ -L '$MNT/etc/systemd/system/multi-user.target.wants/$u.service' ]"
done
check "hostapd does not start by itself" "! [ -L '$MNT/etc/systemd/system/multi-user.target.wants/hostapd.service' ]"
check "dnsmasq-base and hostapd are installed" "[ -x '$MNT/usr/sbin/dnsmasq' ] && [ -x '$MNT/usr/sbin/hostapd' ]"
check "avahi is installed" "[ -x '$MNT/usr/sbin/avahi-daemon' ]"
check "the banks are in place" "[ -s '$MNT/opt/exxpiano/soundfonts/piano.sf2' ] && [ -s '$MNT/opt/exxpiano/soundfonts/mech.sf2' ]"
check "the configured banks are the installed defaults" "grep -qx 'PIANO_SF2=/opt/exxpiano/soundfonts/piano.sf2' '$CONF' && grep -qx 'MECH_SF2=/opt/exxpiano/soundfonts/mech.sf2' '$CONF'"
check "the number is in the configuration" "grep -qx 'DEVICE_NUMBER=$NUMBER' '$CONF'"
check "the card is bound to the board $SERIAL (lock: $LOCK)" "grep -qx 'DEVICE_SERIAL=$SERIAL' '$CONF' && grep -qx 'DEVICE_LOCK=$LOCK' '$CONF'"
check "the host name is set" "[ \"\$(cat '$MNT/etc/hostname')\" = '$HOSTNAME_NEW' ] && grep -q '$HOSTNAME_NEW' '$MNT/etc/hosts'"
check "dietpi.txt: automated, host name, no wait for network" "grep -qx 'AUTO_SETUP_AUTOMATED=1' '$DT' && grep -qx 'AUTO_SETUP_NET_HOSTNAME=$HOSTNAME_NEW' '$DT' && grep -qx 'AUTO_SETUP_BOOT_WAIT_FOR_NETWORK=0' '$DT'"
check "the first-start script is on the boot partition" "[ -x '$MNT$BOOTDIR/Automation_Custom_Script.sh' ]"
check "no leftovers (resolver copy, package copy, policy)" "[ ! -e '$MNT/tmp/exxpiano.deb' ] && [ ! -e '$MNT/usr/sbin/policy-rc.d' ]"
[ "$fail" = 0 ] || die "the checks above failed: the image is not written"

say "9/9 Closing the image"
sync
umount -l "$MNT/dev/pts" "$MNT/dev" "$MNT/proc" "$MNT/sys" 2>/dev/null || true
umount "$MNT$BOOTDIR"; umount "$MNT"
e2fsck -fy "$ROOT_DEV" >/dev/null || [ $? -le 1 ] || die "the file system check after the changes failed"
for l in "${LOOPS[@]}"; do losetup -d "$l"; done; LOOPS=()
mkdir -p "$(dirname "$OUT")" "$(dirname "$DIETPI_VERSION_OUT")"; mv "$DISK" "$OUT"
printf '%s\n' "$DIETPI_VERSION" > "$DIETPI_VERSION_OUT.tmp"; mv "$DIETPI_VERSION_OUT.tmp" "$DIETPI_VERSION_OUT"
echo "Image: $OUT ($(( $(stat -c %s "$OUT") / 1048576 )) MB), number $NUMBER, name $HOSTNAME_NEW, board $SERIAL, DietPi $DIETPI_VERSION"
