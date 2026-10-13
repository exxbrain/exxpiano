#!/bin/bash
# Turns a DietPi image for the Raspberry Pi into the reusable exxpiano image: the software, sound banks and DietPi's first-boot automation.
# Each installed copy creates its own persistent installation ID at first boot. Runs as root on Linux (build-sd-image.py starts it in Docker on a Mac).
#
#   sd-image-inner.sh --image IN.img[.xz] --out OUT.img --dietpi-version-out VERSION.txt --deb exxpiano_X_all.deb --piano P.sf2 --mech M.sf2 [options]
#
# Options: --hostname NAME (generic image hostname; default exxpiano)  --password PW (DietPi's global password)  --ap-password PW  --country RU  --timezone Europe/Moscow
#          --locale en_GB.UTF-8  --keyboard us  --extra-mb N (room added to the image)  --workdir DIR
# The image is NOT started here: nothing can be tried without a Pi. It is checked by reading it back (the last step).
set -euo pipefail

IMAGE="" OUT="" DIETPI_VERSION_OUT="" DEB="" PIANO="" PIANO_LITE="" MECH="" HOSTNAME_NEW="" PASSWORD="" AP_PASSWORD="" COUNTRY="GB" TIMEZONE="UTC" LOCALE="en_GB.UTF-8"
KEYBOARD="us" EXTRA_MB="" WORKDIR="${TMPDIR:-/tmp}/piano-sd-work" NETWORK_IN_CHROOT=1
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
    --piano-lite) PIANO_LITE="$2"; shift 2 ;;
    --mech) MECH="$2"; shift 2 ;;
    --hostname) HOSTNAME_NEW="$2"; shift 2 ;;
    --password) PASSWORD="$2"; shift 2 ;;
    --ap-password) AP_PASSWORD="$2"; shift 2 ;;
    --country) COUNTRY="$2"; shift 2 ;;
    --timezone) TIMEZONE="$2"; shift 2 ;;
    --locale) LOCALE="$2"; shift 2 ;;
    --keyboard) KEYBOARD="$2"; shift 2 ;;
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
[ -n "$HOSTNAME_NEW" ] || HOSTNAME_NEW="exxpiano"
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

read_dietpi_version() {   # mounted root, the boot partition's mount point inside it
  local root="$1" bootdir="$2" version_file="" candidate core sub rc
  for candidate in "$root/boot/dietpi/.version" "$root$bootdir/dietpi/.version"; do
    if [ -r "$candidate" ]; then version_file="$candidate"; break; fi
  done
  [ -n "$version_file" ] || die "cannot find DietPi version metadata at /boot/dietpi/.version or $bootdir/dietpi/.version"
  core="$(sed -n 's/^G_DIETPI_VERSION_CORE=//p' "$version_file" 2>/dev/null | head -1)"
  sub="$(sed -n 's/^G_DIETPI_VERSION_SUB=//p' "$version_file" 2>/dev/null | head -1)"
  rc="$(sed -n 's/^G_DIETPI_VERSION_RC=//p' "$version_file" 2>/dev/null | head -1)"
  [[ "$core" =~ ^[0-9]+$ && "$sub" =~ ^[0-9]+$ && "$rc" =~ ^[0-9]+$ ]] || die "cannot read a complete DietPi version from dietpi/.version"
  printf '%s.%s.%s\n' "$core" "$sub" "$rc"
}

prepare_chroot_tmp() {   # DietPi mounts a 1777 tmpfs here only at boot; offline apt needs the underlying directory
  install -d -m 1777 "$1/tmp"
}

configure_offline_product_boot() {   # mounted root, the boot partition's mount point inside it
  local root="$1" bootdir="$2" dt
  dt="$root$bootdir/dietpi.txt"
  # exxpiano-wifi is the sole owner of wlan0 and creates the setup AP when no saved network is available.
  set_kv "$dt" AUTO_SETUP_NET_WIFI_ENABLED 0
  set_kv "$dt" AUTO_SETUP_BOOT_WAIT_FOR_NETWORK 0
  set_kv "$dt" CONFIG_CHECK_DIETPI_UPDATES 0
  set_kv "$dt" CONFIG_CHECK_APT_UPDATES 0

  # The piano plays through the 3.5 mm jack unless a USB sound card is plugged in. DietPi ships with the onboard sound switched OFF in two places:
  # dtparam=audio=off in config.txt AND a modprobe blacklist of snd_bcm2835 (without the module there is no sound card at all and the synthesizer
  # cannot start). Both are undone here, and DietPi's own record of the choice (CONFIG_SOUNDCARD) says the same.
  set_kv "$dt" CONFIG_SOUNDCARD rpi-bcm2835-3.5mm
  [ -f "$root$bootdir/config.txt" ] && set_kv "$root$bootdir/config.txt" dtparam=audio on
  rm -f "$root/etc/modprobe.d/dietpi-disable_rpi_audio.conf"

  # Let DietPi apply first-boot hardware/password settings, then skip its online update/install workflow.
  install -d "$root/usr/local/sbin" "$root/etc/systemd/system/dietpi-firstboot.service.d" "$root/etc/systemd/system/getty@tty1.service.d" "$root/var/lib/dietpi/postboot.d"
  install -m 755 "$HERE/../dietpi/exxpiano-dietpi-preinstalled.sh" "$root/usr/local/sbin/exxpiano-dietpi-preinstalled"
  install -m 644 "$HERE/../dietpi/dietpi-firstboot-exxpiano.conf" "$root/etc/systemd/system/dietpi-firstboot.service.d/exxpiano-preinstalled.conf"
  # the HDMI console shows a status screen: no login prompt that asks for a password on a device nobody logs in to
  install -m 755 "$HERE/../dietpi/exxpiano-console.sh" "$root/usr/local/sbin/exxpiano-console"
  install -m 644 "$HERE/../dietpi/getty-tty1-exxpiano.conf" "$root/etc/systemd/system/getty@tty1.service.d/exxpiano-console.conf"
  install -m 755 "$HERE/../dietpi/Automation_Custom_Script.image.sh" "$root/var/lib/dietpi/postboot.d/10-exxpiano-image-firstboot"
}

say "1/9 The DietPi image"
rm -rf "$MNT" "$DISK"; mkdir -p "$MNT"
case "$IMAGE" in
  *.xz) xz -dc "$IMAGE" > "$DISK" ;;
  *) cp "$IMAGE" "$DISK" ;;
esac
[ -z "$PIANO_LITE" ] || [ -r "$PIANO_LITE" ] || die "--piano-lite must be readable"
SIZE_BANKS=$(( ( $(stat -c %s "$PIANO") + $(stat -c %s "$MECH") + $( [ -n "$PIANO_LITE" ] && stat -c %s "$PIANO_LITE" || echo 0 ) ) / 1048576 ))
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
DIETPI_VERSION="$(read_dietpi_version "$MNT" "$BOOTDIR")"

say "4/9 The software (installed into the image with its dependencies; the first start needs no download of ours)"
prepare_chroot_tmp "$MNT"
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
[ -z "$PIANO_LITE" ] || install -m 644 "$PIANO_LITE" "$MNT/opt/exxpiano/soundfonts/piano-lite.sf2"      # the first start switches to it on a board with little memory
ls -l "$MNT/opt/exxpiano/soundfonts"

say "6/9 Generic image identity; the installation ID is created at first boot"
CONF="$MNT/etc/exxpiano.conf"
[ -z "$AP_PASSWORD" ] || set_kv "$CONF" WIFI_AP_PASSWORD "$(quote_conf "$AP_PASSWORD")"
echo "$HOSTNAME_NEW" > "$MNT/etc/hostname"
if grep -q '^127\.0\.1\.1' "$MNT/etc/hosts" 2>/dev/null; then sed -i "s/^127\.0\.1\.1.*/127.0.1.1\t$HOSTNAME_NEW/" "$MNT/etc/hosts"; else printf '127.0.1.1\t%s\n' "$HOSTNAME_NEW" >> "$MNT/etc/hosts"; fi

say "7/9 DietPi's first-start automation (dietpi.txt, the custom script, onboard audio)"
DT="$MNT$BOOTDIR/dietpi.txt"
set_kv "$DT" AUTO_SETUP_AUTOMATED 1
set_kv "$DT" AUTO_SETUP_ACCEPT_LICENSE 1
set_kv "$DT" AUTO_SETUP_NET_HOSTNAME "$HOSTNAME_NEW"
set_kv "$DT" AUTO_SETUP_NET_ETHERNET_ENABLED 1
set_kv "$DT" AUTO_SETUP_NET_WIFI_COUNTRY_CODE "$COUNTRY"
set_kv "$DT" AUTO_SETUP_LOCALE "$LOCALE"
set_kv "$DT" AUTO_SETUP_KEYBOARD_LAYOUT "$KEYBOARD"
set_kv "$DT" AUTO_SETUP_TIMEZONE "$TIMEZONE"
set_kv "$DT" AUTO_SETUP_CUSTOM_SCRIPT_EXEC 0
set_kv "$DT" SURVEY_OPTED_IN 0
[ -z "$PASSWORD" ] || set_kv "$DT" AUTO_SETUP_GLOBAL_PASSWORD "$PASSWORD"
install -m 755 "$HERE/../dietpi/Automation_Custom_Script.image.sh" "$MNT$BOOTDIR/Automation_Custom_Script.sh"
configure_offline_product_boot "$MNT" "$BOOTDIR"

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
[ -z "$PIANO_LITE" ] || check "the smaller bank for 512 MB boards is in place" "[ -s '$MNT/opt/exxpiano/soundfonts/piano-lite.sf2' ]"
check "the configured banks are the installed defaults" "grep -qx 'PIANO_SF2=/opt/exxpiano/soundfonts/piano.sf2' '$CONF' && grep -qx 'MECH_SF2=/opt/exxpiano/soundfonts/mech.sf2' '$CONF'"
check "no installation ID is baked into the reusable image" "[ ! -e '$MNT/var/lib/exxpiano/installation-id' ]"
check "the host name is set" "[ \"\$(cat '$MNT/etc/hostname')\" = '$HOSTNAME_NEW' ] && grep -q '$HOSTNAME_NEW' '$MNT/etc/hosts'"
check "dietpi.txt: automated, host name, offline boot owned by exxpiano" "grep -qx 'AUTO_SETUP_AUTOMATED=1' '$DT' && grep -qx 'AUTO_SETUP_NET_HOSTNAME=$HOSTNAME_NEW' '$DT' && grep -qx 'AUTO_SETUP_NET_WIFI_ENABLED=0' '$DT' && grep -qx 'AUTO_SETUP_BOOT_WAIT_FOR_NETWORK=0' '$DT' && grep -qx 'CONFIG_CHECK_DIETPI_UPDATES=0' '$DT' && grep -qx 'CONFIG_CHECK_APT_UPDATES=0' '$DT'"
check "the first-start script is on the boot partition" "[ -x '$MNT$BOOTDIR/Automation_Custom_Script.sh' ]"
check "the pre-installed DietPi handoff is configured" "[ -x '$MNT/usr/local/sbin/exxpiano-dietpi-preinstalled' ] && grep -qx 'ExecStartPost=/usr/local/sbin/exxpiano-dietpi-preinstalled' '$MNT/etc/systemd/system/dietpi-firstboot.service.d/exxpiano-preinstalled.conf' && [ -x '$MNT/var/lib/dietpi/postboot.d/10-exxpiano-image-firstboot' ]"
check "the console shows a status screen, not a login prompt" "[ -x '$MNT/usr/local/sbin/exxpiano-console' ] && grep -qx 'ExecStart=/usr/local/sbin/exxpiano-console' '$MNT/etc/systemd/system/getty@tty1.service.d/exxpiano-console.conf' && [ ! -e '$MNT/etc/systemd/system/getty@tty1.service.d/dietpi-autologin.conf' ]"
check "the onboard sound is on: no module blacklist, dtparam=audio=on, DietPi says 3.5 mm" "[ ! -e '$MNT/etc/modprobe.d/dietpi-disable_rpi_audio.conf' ] && grep -qx 'dtparam=audio=on' '$MNT$BOOTDIR/config.txt' && grep -qx 'CONFIG_SOUNDCARD=rpi-bcm2835-3.5mm' '$DT'"
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
echo "Image: $OUT ($(( $(stat -c %s "$OUT") / 1048576 )) MB), generic name $HOSTNAME_NEW, DietPi $DIETPI_VERSION"
