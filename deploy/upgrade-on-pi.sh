#!/bin/bash
# Runs ON THE PI as root (started by scripts/deploy-to-pi.sh): upgrades exxpiano from the .apk packages in this folder.
# Your /etc/exxpiano.conf is kept (apk puts a newer default next to it as exxpiano.conf.apk-new). The configuration is saved first, and the
# package that was installed before is kept in /var/lib/exxpiano/packages: if the synthesizer or the bridge does not come back, that version is
# installed again by apk (rollback-on-pi.sh does the same by hand).
#
# EXXPIANO_DEV_UNTRUSTED=1 accepts packages signed with a key the device does not trust (your development key): for your own devices only.
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
PKG="$(ls "$HERE"/exxpiano-[0-9]*.apk 2>/dev/null | sort -V | tail -1 || true)"
LIBS=("$HERE"/py3-*.apk)
[ -e "${LIBS[0]}" ] || LIBS=()
STORE="${EXXPIANO_PACKAGES:-/var/lib/exxpiano/packages}"
BACKUP="${EXXPIANO_BACKUP:-/root/exxpiano-backup}"
WAIT="${EXXPIANO_WAIT:-45}"
TRUST=()
[ "${EXXPIANO_DEV_UNTRUSTED:-0}" = 1 ] && TRUST=(--allow-untrusted)

rollback() {
  echo; echo "!! $1 -> rolling back"
  bash "$HERE/rollback-on-pi.sh"
  exit 1
}
started() { rc-service "$1" status 2>/dev/null | grep -q 'status: started'; }
# OpenRC says "started" while supervise-daemon starts a crashing program again and again: a working program keeps the same process.
steady() {     # steady PATTERN (from the start of the command line): the same process runs now and a few seconds later
  local a b; a="$(pgrep -f -o "$1" || true)"; sleep "${EXXPIANO_STEADY:-4}"; b="$(pgrep -f -o "$1" || true)"
  [ -n "$a" ] && [ "$a" = "$b" ]
}

[ -n "$PKG" ] || { echo "No exxpiano-<version>.apk next to this script"; exit 1; }

echo "== 1/6 Checking the packages (signature, dependencies)"
if [ "${#TRUST[@]}" = 0 ]; then
  apk verify "$PKG" "${LIBS[@]}" || { echo "The signature is not one this device trusts: nothing was changed"; exit 1; }
else
  echo "(EXXPIANO_DEV_UNTRUSTED=1: the signature is not checked)"
fi
apk add --simulate "${TRUST[@]}" "${LIBS[@]}" "$PKG" >/dev/null || { echo "apk cannot install it (a missing dependency? check the internet connection): nothing was changed"; exit 1; }

echo "== 2/6 Backup into $BACKUP"
rm -rf "$BACKUP"; mkdir -p "$BACKUP"
cp -a /etc/exxpiano.conf "$BACKUP/" 2>/dev/null || true
old="$(apk info -e exxpiano >/dev/null 2>&1 && apk list -I exxpiano 2>/dev/null | sed -n 's/^exxpiano-\([^ ]*\) .*/\1/p' | head -1)"
if [ -n "$old" ] && [ -s "$STORE/exxpiano-$old.apk" ]; then
  echo "$STORE/exxpiano-$old.apk" > "$BACKUP/previous"
  echo "installed now: $old (kept for a rollback)"
else
  echo "installed now: ${old:-nothing}; no package file of it is kept: a failed upgrade cannot be rolled back automatically"
fi

echo "== 3/6 Installing (keeping your configuration)"
apk add "${TRUST[@]}" "${LIBS[@]}" "$PKG" || rollback "apk failed"
# An older configuration has no BANK_DIR: use the folder of the bank that is playing now, so the web page lists your banks.
if ! grep -q '^BANK_DIR=' /etc/exxpiano.conf; then
  cur="$(sed -n 's/^PIANO_SF2="\{0,1\}\([^"]*\)"\{0,1\}$/\1/p' /etc/exxpiano.conf | head -1)"
  [ -n "$cur" ] && echo "BANK_DIR=$(dirname "$cur")" >> /etc/exxpiano.conf && echo "BANK_DIR set to $(dirname "$cur")"
fi

echo "== 4/6 Starting (the banks load for ~40 s)"
for s in exxpiano exxpiano-fx exxpiano-web exxpiano-wifi; do rc-update add "$s" default >/dev/null 2>&1; done
started exxpiano || rc-service exxpiano start >/dev/null 2>&1
sleep "$WAIT"
started exxpiano && steady '^(/bin/sh )?[^ ]*(fluidsynth -a |python3 /opt/exxpiano/exxpiano-synth.py )' || rollback "the synthesizer is not running"
started exxpiano-fx || rc-service exxpiano-fx restart >/dev/null 2>&1
started exxpiano-web || rc-service exxpiano-web restart >/dev/null 2>&1
started exxpiano-fx && steady '^[^ ]*python3 /opt/exxpiano/exxpiano-fx\.py' || rollback "the bridge is not running"

echo "== 5/6 Keeping this package for the next rollback"
mkdir -p "$STORE"
new="$(basename "$PKG" .apk)"; new="${new#exxpiano-}"
cp "$PKG" "$STORE/exxpiano-$new.apk"
ls -t "$STORE"/exxpiano-*.apk 2>/dev/null | tail -n +3 | xargs -r rm -f          # the newest two are enough
cp "${LIBS[@]}" "$STORE/" 2>/dev/null || true

echo "== 6/6 Checks"
for s in exxpiano exxpiano-fx exxpiano-web exxpiano-wifi; do printf '%-14s ' "$s"; rc-service "$s" status 2>&1 | sed 's/^ \* //'; done
echo "-- audio output:"; grep 'audio output' /var/log/messages 2>/dev/null | tail -1
echo "-- network ports (9800 must be ABSENT, 8080 present):"; netstat -ltn 2>/dev/null | grep -E ':(9800|8080)\b' || echo "(none listed)"
echo "-- control pipe:"; ls -l /run/exxpiano.cmd
echo "-- power:"; vcgencmd get_throttled 2>/dev/null || cat /sys/devices/platform/soc/soc:firmware/get_throttled 2>/dev/null || true
IP="$(ip -4 -o addr show scope global 2>/dev/null | awk '{print $4}' | cut -d/ -f1 | head -1)"
wget -q -T 5 -O - "http://127.0.0.1:8080/api/ping" && echo
echo "Open  http://${IP:-<address>}:8080/  on your phone or computer."
echo "If anything is wrong: bash $HERE/rollback-on-pi.sh"
