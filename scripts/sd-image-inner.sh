#!/bin/bash
# Makes the reusable exxpiano SD card image from Alpine packages: a FAT boot partition and an ext4 root, Alpine Linux in sys mode with OpenRC,
# the exxpiano packages and the sound banks. Each installed copy creates its own persistent installation ID at first boot.
# Runs as root on Alpine aarch64 (build-sd-image.py starts it in the sd-builder container; Docker Desktop on a Mac runs arm64 natively).
#
#   sd-image-inner.sh --apk-repo DIR --out OUT.img --info-out DIR --piano P.sf2 --mech M.sf2 [--piano-lite L.sf2] [options]
#
# --apk-repo: what scripts/build-apks.py made (DIR/exxpiano/aarch64/*.apk with its APKINDEX, DIR/<key>.rsa.pub)
# --info-out: gets alpine-version.txt and packages.txt (every installed package with its version) for the manifest
# Options: --hostname NAME (generic image hostname; default exxpiano)  --country GB  --timezone UTC  --alpine DIR (the pinned repositories
#          and keys; default the alpine/ folder of this repository)  --mirror URL (instead of the pinned mirror: same branch, same keys)
#          --cache-dir DIR (downloaded packages, kept for the next builds)  --ssh-key FILE (a public key root accepts)  --workdir DIR
# The passwords come through the environment, never the arguments: SD_PASSWORD (root; without it root logs in by key only) and SD_AP_PASSWORD.
# Nothing is started from the image here except its own programs in a chroot (the smoke check); the image is checked by reading it back.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APK_REPO="" OUT="" INFO_OUT="" PIANO="" PIANO_LITE="" MECH="" HOSTNAME_NEW="exxpiano" COUNTRY="GB" TIMEZONE="UTC" ALPINE="$HERE/../alpine"
SSH_KEY="" WORKDIR="${TMPDIR:-/tmp}/exxpiano-sd-work" MIRROR="" CACHE_DIR=""
BOOT_MB=256

die() { echo "sd-image: $*" >&2; exit 1; }
say() { echo "== $*"; }

while [ $# -gt 0 ]; do
  case "$1" in
    --apk-repo) APK_REPO="$2"; shift 2 ;;
    --out) OUT="$2"; shift 2 ;;
    --info-out) INFO_OUT="$2"; shift 2 ;;
    --piano) PIANO="$2"; shift 2 ;;
    --piano-lite) PIANO_LITE="$2"; shift 2 ;;
    --mech) MECH="$2"; shift 2 ;;
    --hostname) HOSTNAME_NEW="$2"; shift 2 ;;
    --country) COUNTRY="$2"; shift 2 ;;
    --timezone) TIMEZONE="$2"; shift 2 ;;
    --alpine) ALPINE="$2"; shift 2 ;;
    --ssh-key) SSH_KEY="$2"; shift 2 ;;
    --mirror) MIRROR="$2"; shift 2 ;;
    --cache-dir) CACHE_DIR="$2"; shift 2 ;;
    --workdir) WORKDIR="$2"; shift 2 ;;
    -h|--help) sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) die "unknown option: $1" ;;
  esac
done

PASSWORD="${SD_PASSWORD:-}"; AP_PASSWORD="${SD_AP_PASSWORD:-}"
[ "$(id -u)" = 0 ] || die "run as root"
[ "$(uname -m)" = aarch64 ] || die "run on aarch64 (the packages' install scripts run in the image): this machine is $(uname -m)"
[ -n "$OUT" ] && [ -n "$INFO_OUT" ] || die "--out and --info-out are required"
REPO_DIR="$APK_REPO/exxpiano"
[ -s "$REPO_DIR/aarch64/APKINDEX.tar.gz" ] || die "no package repository in $APK_REPO (make it with scripts/build-apks.py)"
PUBKEY="$(ls "$APK_REPO"/*.rsa.pub 2>/dev/null | head -1)"
[ -s "$PUBKEY" ] || die "no public key (*.rsa.pub) in $APK_REPO"
[[ "$HOSTNAME_NEW" =~ ^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$ ]] || die "--hostname: lowercase letters, digits and hyphens"
[[ "$COUNTRY" =~ ^[A-Z]{2}$ ]] || die "--country: two capital letters (GB, RU ...)"
[ -r "$PIANO" ] || die "--piano is required and must be readable"
[ -r "$MECH" ] || die "--mech is required and must be readable"
[ -z "$PIANO_LITE" ] || [ -r "$PIANO_LITE" ] || die "--piano-lite must be readable"
[ -z "$SSH_KEY" ] || [ -r "$SSH_KEY" ] || die "--ssh-key must be readable"
[ -z "$MIRROR" ] || [[ "$MIRROR" =~ ^https://[A-Za-z0-9.-]+(:[0-9]+)?(/[A-Za-z0-9._~/-]*)?$ ]] || die "--mirror: an https:// address"
for t in apk mkfs.ext4 mkfs.vfat mcopy mdir debugfs sfdisk truncate dd awk sed; do command -v "$t" >/dev/null || die "missing tool: $t"; done

rm -rf "$WORKDIR"; mkdir -p "$WORKDIR"; WORKDIR="$(cd "$WORKDIR" && pwd)"
ROOTFS="$WORKDIR/rootfs"; KEYS="$WORKDIR/keys"
mkdir -p "$ROOTFS" "$KEYS"
cp "$ALPINE"/keys/*.rsa.pub "$PUBKEY" "$KEYS/"
# the pinned branch, from the pinned mirror or the one given (the packages are checked with the same keys either way)
if [ -n "$MIRROR" ]; then sed "s#^https://dl-cdn.alpinelinux.org/alpine#${MIRROR%/}#" "$ALPINE/repositories"; else cat "$ALPINE/repositories"; fi > "$WORKDIR/alpine-repositories"
{ cat "$WORKDIR/alpine-repositories"; echo "$REPO_DIR"; } > "$WORKDIR/repositories"
APK=(apk --root "$ROOTFS" --arch aarch64 --keys-dir "$KEYS" --repositories-file "$WORKDIR/repositories" --no-progress)
[ -z "$CACHE_DIR" ] || { mkdir -p "$CACHE_DIR"; APK+=(--cache-dir "$CACHE_DIR"); }

say "1/8 Alpine base (the pinned branch, checked with the pinned keys)"
"${APK[@]}" --initdb add alpine-base >/dev/null
for n in "null c 1 3" "zero c 1 5" "random c 1 8" "urandom c 1 9" "tty c 5 0" "console c 5 1"; do    # what the install scripts write to
  set -- $n; [ -e "$ROOTFS/dev/$1" ] || mknod -m 666 "$ROOTFS/dev/$1" "$2" "$3" "$4"
done
# the initramfs must find an ext4 root on the SD card (mmc) or a USB drive
mkdir -p "$ROOTFS/etc/mkinitfs"
echo 'features="base ext4 kms mmc usb"' > "$ROOTFS/etc/mkinitfs/mkinitfs.conf"

say "2/8 The kernel, the firmware and the software (no download at the first start)"
"${APK[@]}" add linux-rpi raspberrypi-bootloader linux-firmware-brcm linux-firmware-cypress \
  raspberrypi-bootloader-cutdown eudev openssh openssh-sftp-server chrony ifupdown-ng tzdata e2fsprogs dosfstools raspberrypi-utils-vcgencmd exxpiano >"$WORKDIR/apk-add.log" 2>&1 \
  || { tail -30 "$WORKDIR/apk-add.log"; die "the packages could not be installed"; }
grep -i "warning\|error" "$WORKDIR/apk-add.log" | grep -v "^OK" || true
cat "$ROOTFS/etc/alpine-release" > "$WORKDIR/alpine-version"
[[ "$(cat "$WORKDIR/alpine-version")" =~ ^3\.24\.[0-9]+$ ]] || die "not Alpine 3.24: $(cat "$WORKDIR/alpine-version")"
"${APK[@]}" info -v 2>/dev/null | sort > "$WORKDIR/packages"

say "3/8 Services (OpenRC runlevels; eudev instead of mdev)"
runlevel() { local level="$1"; shift; mkdir -p "$ROOTFS/etc/runlevels/$level"; for s in "$@"; do
  [ -e "$ROOTFS/etc/init.d/$s" ] || die "no service $s in the image"; ln -sf "/etc/init.d/$s" "$ROOTFS/etc/runlevels/$level/$s"; done; }
rm -f "$ROOTFS"/etc/runlevels/*/mdev "$ROOTFS"/etc/runlevels/*/hwdrivers
runlevel sysinit devfs dmesg udev udev-trigger udev-settle
runlevel boot modules sysctl hostname bootmisc syslog klogd swclock seedrng networking root fsck localmount
runlevel default chronyd sshd avahi-daemon alsa exxpiano-firstboot exxpiano exxpiano-fx exxpiano-web exxpiano-wifi local
runlevel shutdown mount-ro killprocs savecache
for s in hostapd dnsmasq; do rm -f "$ROOTFS"/etc/runlevels/*/"$s"; done        # exxpiano-wifi starts them itself

say "4/8 The system settings"
echo "$HOSTNAME_NEW" > "$ROOTFS/etc/hostname"
printf '127.0.0.1\tlocalhost\n127.0.1.1\t%s\n::1\tlocalhost\n' "$HOSTNAME_NEW" > "$ROOTFS/etc/hosts"
[ -e "$ROOTFS/usr/share/zoneinfo/$TIMEZONE" ] || die "--timezone: no such zone: $TIMEZONE"
ln -sf "/usr/share/zoneinfo/$TIMEZONE" "$ROOTFS/etc/localtime"; echo "$TIMEZONE" > "$ROOTFS/etc/timezone"
cat > "$ROOTFS/etc/fstab" <<'EOF'
LABEL=exxroot	/	ext4	defaults,noatime	0 1
LABEL=EXXBOOT	/boot	vfat	defaults,noatime,umask=0022	0 2
EOF
# the wired port of the board; wlan0 belongs to exxpiano-wifi alone (no stanza here)
cat > "$ROOTFS/etc/network/interfaces" <<'EOF'
auto lo
iface lo inet loopback

auto eth0
iface eth0 inet dhcp
EOF
install -d -m 755 "$ROOTFS/etc/wpa_supplicant"
printf 'ctrl_interface=/run/wpa_supplicant\ncountry=%s\nupdate_config=1\n' "$COUNTRY" > "$ROOTFS/etc/wpa_supplicant/wpa_supplicant.conf"
chmod 600 "$ROOTFS/etc/wpa_supplicant/wpa_supplicant.conf"
quote_conf() { printf '"%s"' "$(printf '%s' "$1" | sed 's/[\\"]/\\&/g')"; }
set_conf() {   # KEY VALUE in /etc/exxpiano.conf: replaces KEY= or #KEY=, appends when absent
  local f="$ROOTFS/etc/exxpiano.conf" tmp; tmp="$(mktemp)"
  K="$1" V="$2" awk 'BEGIN{d=0; k=ENVIRON["K"]; v=ENVIRON["V"]} !d && (index($0, k "=")==1 || index($0, "# " k "=")==1) {print k "=" v; d=1; next} {print} END{if(!d) print k "=" v}' "$f" > "$tmp"
  cat "$tmp" > "$f"; rm -f "$tmp"
}
set_conf WIFI_COUNTRY "$COUNTRY"
[ -z "$AP_PASSWORD" ] || set_conf WIFI_AP_PASSWORD "$(quote_conf "$AP_PASSWORD")"
# the HDMI console shows the status screen of exxpiano; no getty asks for a login on any console
sed -i -E 's@^(tty[1-6]::respawn:.*getty.*)$@#\1@' "$ROOTFS/etc/inittab"
echo 'tty1::respawn:/opt/exxpiano/exxpiano-console.sh' >> "$ROOTFS/etc/inittab"
# root: the password given to the build, or none at all (key login only; a key can also be put on the boot partition, see the README)
if [ -n "$PASSWORD" ]; then
  printf 'root:%s\n' "$PASSWORD" | chroot "$ROOTFS" /usr/sbin/chpasswd
  sed -i -E 's/^#?PermitRootLogin .*/PermitRootLogin yes/' "$ROOTFS/etc/ssh/sshd_config"
else
  sed -i -E 's/^root:[^:]*:/root:*:/' "$ROOTFS/etc/shadow"
  sed -i -E 's/^#?PermitRootLogin .*/PermitRootLogin prohibit-password/' "$ROOTFS/etc/ssh/sshd_config"
fi
grep -q '^PermitRootLogin' "$ROOTFS/etc/ssh/sshd_config" || echo "PermitRootLogin $([ -n "$PASSWORD" ] && echo yes || echo prohibit-password)" >> "$ROOTFS/etc/ssh/sshd_config"
if [ -n "$SSH_KEY" ]; then install -d -m 700 "$ROOTFS/root/.ssh"; install -m 600 "$SSH_KEY" "$ROOTFS/root/.ssh/authorized_keys"; fi
echo 'SYSLOGD_OPTS="-t -s 1024 -b 2"' > "$ROOTFS/etc/conf.d/syslog"
# the ALSA sequencer: FluidSynth's MIDI port and the bridge need it, and USB keyboards reach it through snd-seq-midi. Debian loads them by
# itself, Alpine does not (without them FluidSynth says "no MIDI input" and the keyboard plays nothing; seen on a Pi 3 B)
for m in snd-seq snd-seq-midi; do grep -qx "$m" "$ROOTFS/etc/modules" 2>/dev/null || echo "$m" >> "$ROOTFS/etc/modules"; done        # /var/log/messages is rotated: it does not fill the card
# later upgrades of Alpine's packages come from the same branch; the project key verifies the exxpiano packages
cp "$WORKDIR/alpine-repositories" "$ROOTFS/etc/apk/repositories"
cp "$PUBKEY" "$ROOTFS/etc/apk/keys/"
# the installed packages are kept: upgrade-on-pi.sh rolls back to them
install -d "$ROOTFS/var/lib/exxpiano/packages"
cp "$REPO_DIR"/aarch64/exxpiano-[0-9]*.apk "$REPO_DIR"/aarch64/py3-*.apk "$ROOTFS/var/lib/exxpiano/packages/"

say "5/8 Sound banks"
install -d "$ROOTFS/opt/exxpiano/soundfonts"
install -m 644 "$PIANO" "$ROOTFS/opt/exxpiano/soundfonts/piano.sf2"
install -m 644 "$MECH" "$ROOTFS/opt/exxpiano/soundfonts/mech.sf2"
[ -z "$PIANO_LITE" ] || install -m 644 "$PIANO_LITE" "$ROOTFS/opt/exxpiano/soundfonts/piano-lite.sf2"   # the first start picks it with little memory

say "6/8 The programs of the image, run in it"
SMOKE_FAIL=0
smoke() { if eval "$2"; then echo "ok   $1"; else echo "FAIL $1"; SMOKE_FAIL=1; fi; }
mkdir -p "$WORKDIR/smoke"
inroot() { chroot "$ROOTFS" /usr/bin/env PYTHONDONTWRITEBYTECODE=1 EXXPIANO_CONF=/dev/null PROFILE_FILE=/tmp/smoke-profiles.json \
  EXXPIANO_INSTALLATION_ID_PATH=/tmp/smoke-installation-id "$@"; }
smoke "the Python modules import" "inroot python3 -c 'import sys; sys.path.insert(0, \"/opt/exxpiano\"); import wifilib, qrlib, exxpianoconf, banklib, bankimport, usblib, midilib, midiplayer, profiles, sysmon, oslib, mido, rtmidi'"
inroot python3 /opt/exxpiano/exxpiano-web.py >"$WORKDIR/smoke/web.log" 2>&1 &
WEB_PID=$!
get() { python3 -c "import sys, urllib.request; print(urllib.request.urlopen(sys.argv[1], timeout=5).read().decode())" "$1" 2>/dev/null; }
for _ in $(seq 1 20); do get http://127.0.0.1:8080/api/ping >/dev/null && break; sleep 0.5; done
smoke "the web service answers" "get http://127.0.0.1:8080/api/ping | grep -q '\"ok\": true'"
QR="$(get 'http://127.0.0.1:8080/api/qr?kind=join' || true)"; SMOKE_ID="$(cat "$ROOTFS/tmp/smoke-installation-id" 2>/dev/null || true)"
smoke "the page makes the setup QR code from the generated installation ID" "[ -n \"$SMOKE_ID\" ] && echo \"\$QR\" | grep -q \"WIFI:T:WPA;S:exxpiano-Setup-$SMOKE_ID;\""
smoke "the page with the menu is served" "python3 -c \"import sys, urllib.request; sys.exit(0 if b'id=\\\"drawer\\\"' in urllib.request.urlopen('http://127.0.0.1:8080/', timeout=5).read() else 1)\""
kill "$WEB_PID" 2>/dev/null; wait "$WEB_PID" 2>/dev/null || true
smoke "the Wi-Fi supervisor runs" "inroot python3 /opt/exxpiano/exxpiano-wifi.py status >/dev/null 2>&1; true"
smoke "the console screen renders" "chroot '$ROOTFS' /usr/bin/env EXXPIANO_CONSOLE_ONCE=1 EXXPIANO_IPS= /opt/exxpiano/exxpiano-console.sh | grep -q 'There is no login on this screen'"
rm -f "$ROOTFS"/tmp/smoke-*; rm -rf "$ROOTFS"/tmp/* "$ROOTFS"/var/cache/apk/*
find "$ROOTFS/opt/exxpiano" -name __pycache__ -prune -exec rm -rf {} +
[ "$SMOKE_FAIL" = 0 ] || { tail -20 "$WORKDIR/smoke/web.log"; die "the programs of the image fail (see above)"; }

say "7/8 The partitions"
# the boot partition: the firmware, the kernel, the initramfs and the device trees, with the settings of the boards
BOOTFILES="$WORKDIR/bootfiles"; rm -rf "$BOOTFILES"; mkdir -p "$BOOTFILES"
cp -a "$ROOTFS/boot/." "$BOOTFILES/"
find "$BOOTFILES" -type l -delete                          # boot -> . and the like: FAT has no links (and mcopy would loop)
[ -s "$BOOTFILES/vmlinuz-rpi" ] && [ -s "$BOOTFILES/initramfs-rpi" ] || die "the kernel or the initramfs is missing in /boot"
# config.txt belongs to raspberrypi-bootloader (it loads vmlinuz-rpi and initramfs-rpi in 64-bit mode, and is rewritten on its upgrades);
# the settings of exxpiano go into the usercfg.txt it includes
grep -qx 'include usercfg.txt' "$BOOTFILES/config.txt" || die "config.txt of raspberrypi-bootloader does not include usercfg.txt"
# The memory of the graphics processor is the one setting the firmware reads only from config.txt itself (in usercfg.txt it is ignored: measured
# on a Pi 3 B, 76 MB stayed). Without a desktop 16 MB do (with the cut-down firmware start_cd.elf); the 60 MB it gives back let the full piano
# fit on a 1 GB board. update-raspberrypi-bootloader rewrites only the kernel=, initramfs and arm_64bit= lines, so this one stays.
sed -i 's/^arm_64bit=1$/arm_64bit=1\ngpu_mem=16/' "$BOOTFILES/config.txt"
grep -qx 'gpu_mem=16' "$BOOTFILES/config.txt" || die "could not set gpu_mem in config.txt"
cat > "$BOOTFILES/usercfg.txt" <<'CFG'
# exxpiano: Raspberry Pi 3, 4 and 5 (64-bit Alpine Linux). config.txt is rewritten by package upgrades: settings go here.
# the 3.5 mm jack (Pi 3 and 4; the Pi 5 has none and plays through a USB sound card)
dtparam=audio=on
disable_splash=1
CFG
echo "root=LABEL=exxroot rootfstype=ext4 rootwait modules=sd-mod,usb-storage,ext4 console=tty1 quiet" > "$BOOTFILES/cmdline.txt"
rm -rf "$ROOTFS/boot"; mkdir -p "$ROOTFS/boot"            # the boot partition is mounted here on the device
truncate -s "${BOOT_MB}M" "$WORKDIR/boot.img"
mkfs.vfat -F 32 -n EXXBOOT "$WORKDIR/boot.img" >/dev/null
mcopy -s -i "$WORKDIR/boot.img" "$BOOTFILES"/* ::/
# the root partition: what is installed, plus room for the first start (it grows to the whole card then)
USED_MB=$(( $(du -sm "$ROOTFS" | cut -f1) ))
ROOT_MB=$(( USED_MB + USED_MB / 10 + 300 ))
truncate -s "${ROOT_MB}M" "$WORKDIR/root.img"
mkfs.ext4 -q -F -L exxroot -E root_owner=0:0 -d "$ROOTFS" "$WORKDIR/root.img"
# the card: MBR, 4 MiB alignment, FAT32 (LBA) boot then Linux root
DISK="$WORKDIR/disk.img"
truncate -s "$(( 4 + BOOT_MB + ROOT_MB ))M" "$DISK"
sfdisk -q "$DISK" <<EOF
label: dos
unit: sectors
start=8192, size=$(( BOOT_MB * 2048 )), type=c, bootable
start=$(( 8192 + BOOT_MB * 2048 )), size=$(( ROOT_MB * 2048 )), type=83
EOF
dd if="$WORKDIR/boot.img" of="$DISK" bs=1M seek=4 conv=notrunc,fsync status=none
dd if="$WORKDIR/root.img" of="$DISK" bs=1M seek=$(( 4 + BOOT_MB )) conv=notrunc,fsync status=none
echo "boot ${BOOT_MB} MB, root ${ROOT_MB} MB (${USED_MB} MB used)"

say "8/8 Reading the image back"
fail=0
check() { if eval "$2"; then echo "ok   $1"; else echo "FAIL $1"; fail=1; fi; }
rootfile() { debugfs -R "cat $1" "$WORKDIR/root.img" 2>/dev/null; }
rootexists() { debugfs -R "stat $1" "$WORKDIR/root.img" 2>/dev/null | grep -q '^Inode:'; }
bootlist="$(mdir -/ -b -i "$WORKDIR/boot.img" ::/ 2>/dev/null)"
check "the partition table: FAT32 boot, then the root" "sfdisk -d '$DISK' | grep -q 'type=c, bootable' && sfdisk -d '$DISK' | grep -q 'type=83'"
check "the kernel, the initramfs and the firmware are on the boot partition" "echo \"\$bootlist\" | grep -q '/vmlinuz-rpi$' && echo \"\$bootlist\" | grep -q '/initramfs-rpi$' && echo \"\$bootlist\" | grep -qi '/start4.elf$' && echo \"\$bootlist\" | grep -qi '/bootcode.bin$'"
check "device trees for the Pi 3, 4 and 5" "echo \"\$bootlist\" | grep -q 'bcm2710-rpi-3-b.dtb$' && echo \"\$bootlist\" | grep -q 'bcm2711-rpi-4-b.dtb$' && echo \"\$bootlist\" | grep -q 'bcm2712-rpi-5-b.dtb$' && echo \"\$bootlist\" | grep -q '/overlays/'"
check "config.txt: the 64-bit kernel; usercfg.txt: the 3.5 mm jack" "mtype -i '$WORKDIR/boot.img' ::/config.txt | grep -qx 'arm_64bit=1' && mtype -i '$WORKDIR/boot.img' ::/config.txt | grep -qx 'kernel=vmlinuz-rpi' && mtype -i '$WORKDIR/boot.img' ::/usercfg.txt | grep -qx 'dtparam=audio=on'"
check "16 MB for the graphics processor, with the cut-down firmware" "mtype -i '$WORKDIR/boot.img' ::/config.txt | grep -qx 'gpu_mem=16' && echo \"\$bootlist\" | grep -q '/start_cd.elf$' && echo \"\$bootlist\" | grep -q '/fixup_cd.dat$'"
check "the ALSA sequencer modules load at boot" "rootfile /etc/modules | grep -qx snd-seq && rootfile /etc/modules | grep -qx snd-seq-midi"
check "the root is found by its label" "mtype -i '$WORKDIR/boot.img' ::/cmdline.txt | grep -q 'root=LABEL=exxroot' && rootfile /etc/fstab | grep -q '^LABEL=exxroot'"
check "the package is installed" "grep -q '^exxpiano-[0-9]' '$WORKDIR/packages'"
for s in exxpiano exxpiano-fx exxpiano-web exxpiano-wifi exxpiano-firstboot sshd chronyd avahi-daemon; do
  check "$s starts at boot" "rootexists /etc/runlevels/default/$s"
done
check "eudev, not mdev, handles the devices" "rootexists /etc/runlevels/sysinit/udev && ! rootexists /etc/runlevels/sysinit/mdev"
check "hostapd and dnsmasq do not start by themselves" "! rootexists /etc/runlevels/default/hostapd && ! rootexists /etc/runlevels/default/dnsmasq"
check "the console shows a status screen, no login prompt anywhere" "rootfile /etc/inittab | grep -qx 'tty1::respawn:/opt/exxpiano/exxpiano-console.sh' && ! rootfile /etc/inittab | grep -E '^tty[0-9]::' | grep -q getty"
check "no automatic login" "! rootfile /etc/inittab | grep -q -- '-a root\|autologin'"
check "the banks are in place" "rootexists /opt/exxpiano/soundfonts/piano.sf2 && rootexists /opt/exxpiano/soundfonts/mech.sf2"
[ -z "$PIANO_LITE" ] || check "the smaller bank for 512 MB boards is in place" "rootexists /opt/exxpiano/soundfonts/piano-lite.sf2"
check "the configured banks are the installed defaults" "rootfile /etc/exxpiano.conf | grep -qx 'PIANO_SF2=/opt/exxpiano/soundfonts/piano.sf2' && rootfile /etc/exxpiano.conf | grep -qx 'MECH_SF2=/opt/exxpiano/soundfonts/mech.sf2'"
check "the demonstration pieces are in the MIDI library" "[ \"\$(debugfs -R 'ls -p /var/lib/exxpiano/midi' '$WORKDIR/root.img' 2>/dev/null | grep -c '/m-')\" -ge 2 ]"
check "no installation ID is baked into the reusable image" "! rootexists /var/lib/exxpiano/installation-id"
check "the Wi-Fi country is set" "rootfile /etc/wpa_supplicant/wpa_supplicant.conf | grep -qx 'country=$COUNTRY' && rootfile /etc/exxpiano.conf | grep -qx 'WIFI_COUNTRY=$COUNTRY'"
check "the host name is set" "[ \"\$(rootfile /etc/hostname)\" = '$HOSTNAME_NEW' ]"
check "the device trusts the project key and keeps its packages" "rootexists /etc/apk/keys/$(basename "$PUBKEY") && debugfs -R 'ls -p /var/lib/exxpiano/packages' '$WORKDIR/root.img' 2>/dev/null | grep -q '/exxpiano-[0-9]'"
check "root logs in by password only when one was given" "if [ -n '$PASSWORD' ]; then rootfile /etc/ssh/sshd_config | grep -qx 'PermitRootLogin yes'; else rootfile /etc/ssh/sshd_config | grep -qx 'PermitRootLogin prohibit-password' && rootfile /etc/shadow | grep -q '^root:\*:'; fi"
check "nothing of Debian or DietPi" "! rootexists /var/lib/dpkg && ! rootexists /boot/dietpi && ! echo \"\$bootlist\" | grep -qi dietpi"
[ "$fail" = 0 ] || die "the checks above failed: the image is not written"

mkdir -p "$(dirname "$OUT")" "$INFO_OUT"
mv "$DISK" "$OUT"
cp "$WORKDIR/alpine-version" "$INFO_OUT/alpine-version.txt"
cp "$WORKDIR/packages" "$INFO_OUT/packages.txt"
head -1 "$WORKDIR/alpine-repositories" | sed 's#/v[0-9.]*/main$##' > "$INFO_OUT/mirror.txt"
rm -rf "$WORKDIR"
echo "Image: $OUT ($(( $(stat -c %s "$OUT") / 1048576 )) MB), generic name $HOSTNAME_NEW, Alpine $(cat "$INFO_OUT/alpine-version.txt")"
