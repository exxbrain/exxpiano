#!/bin/bash
# For testing sd-image-inner.sh without a real DietPi image: makes a small image that has the same layout (MBR, a FAT boot partition with dietpi.txt
# and config.txt, an ext4 root with Debian trixie and /etc/fstab). Runs as root on Linux (the Docker builder); needs network for debootstrap.
#   sd-fake-dietpi.sh OUT.img [root-size-MB]
set -euo pipefail
OUT="${1:?usage: sd-fake-dietpi.sh OUT.img [root-size-MB]}"; ROOT_MB="${2:-900}"
BOOT_MB=128
command -v debootstrap >/dev/null || { apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends debootstrap >/dev/null; }
W="$(mktemp -d)"; LOOPS=()
cleanup() { set +e; mountpoint -q "$W/root/boot" && umount "$W/root/boot"; mountpoint -q "$W/root" && umount "$W/root"; for l in "${LOOPS[@]:-}"; do [ -n "$l" ] && losetup -d "$l"; done; for l in $(losetup -j "$W/fake.img" -O NAME --noheadings 2>/dev/null); do losetup -d "$l"; done; rm -rf "$W"; }
trap cleanup EXIT
IMG="$W/fake.img"; truncate -s "$(( BOOT_MB + ROOT_MB + 4 ))M" "$IMG"
parted -s "$IMG" -- mklabel msdos mkpart primary fat32 4MiB "$(( BOOT_MB + 4 ))MiB" mkpart primary ext4 "$(( BOOT_MB + 4 ))MiB" 100%
BOOT_OFF=$(( 4 * 1048576 )); BOOT_SIZE=$(( BOOT_MB * 1048576 )); ROOT_OFF=$(( (BOOT_MB + 4) * 1048576 )); ROOT_SIZE=$(( IMG_SIZE=$(stat -c %s "$IMG") - ROOT_OFF ))
loop() { local d; d="$(losetup --find --show --offset "$1" --sizelimit "$2" "$IMG")"; LOOPS+=("$d"); [ -b "$d" ] || mknod "$d" b 7 "${d#/dev/loop}"; LOOP_DEV="$d"; }
loop "$BOOT_OFF" "$BOOT_SIZE"; BOOT="$LOOP_DEV"; loop "$ROOT_OFF" "$ROOT_SIZE"; ROOT="$LOOP_DEV"
mkfs.vfat -F 32 -n DIETPISETUP "$BOOT" >/dev/null; mkfs.ext4 -q -L DietPi-ROOT "$ROOT"
mkdir -p "$W/root"; mount "$ROOT" "$W/root"
debootstrap --variant=minbase --include=ca-certificates,systemd,systemd-sysv,sudo trixie "$W/root" http://deb.debian.org/debian >/dev/null
echo "dietpi" > "$W/root/etc/hostname"; printf '127.0.0.1\tlocalhost\n127.0.1.1\tdietpi\n' > "$W/root/etc/hosts"
printf 'LABEL=DIETPISETUP /boot vfat defaults 0 2\nLABEL=DietPi-ROOT / ext4 defaults 0 1\n' > "$W/root/etc/fstab"
mkdir -p "$W/root/boot"; mount "$BOOT" "$W/root/boot"
mkdir -p "$W/root/boot/dietpi"; printf 'G_DIETPI_VERSION_CORE=10\nG_DIETPI_VERSION_SUB=7\n' > "$W/root/boot/dietpi/.version"
cat > "$W/root/boot/dietpi.txt" <<'TXT'
##### a stand-in for DietPi's dietpi.txt
AUTO_SETUP_AUTOMATED=0
AUTO_SETUP_NET_ETHERNET_ENABLED=1
#AUTO_SETUP_NET_WIFI_ENABLED=0
AUTO_SETUP_NET_HOSTNAME=DietPi
AUTO_SETUP_BOOT_WAIT_FOR_NETWORK=1
AUTO_SETUP_GLOBAL_PASSWORD=dietpi
AUTO_SETUP_CUSTOM_SCRIPT_EXEC=0
SURVEY_OPTED_IN=1
TXT
printf '# a stand-in for config.txt\ndtparam=audio=off\narm_64bit=1\n' > "$W/root/boot/config.txt"
sync; umount "$W/root/boot"; umount "$W/root"
for l in "${LOOPS[@]}"; do losetup -d "$l"; done; LOOPS=()
mkdir -p "$(dirname "$OUT")"; mv "$IMG" "$OUT"; echo "fake DietPi image: $OUT"
