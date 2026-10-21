#!/bin/bash
# Runs ON THE PI as root: installs again the exxpiano package that upgrade-on-pi.sh found before the upgrade, and the configuration it saved.
# apk then lists the version that really runs. Without a saved package nothing is removed: the current version stays.
set -uo pipefail
BACKUP="${EXXPIANO_BACKUP:-/root/exxpiano-backup}"
WAIT="${EXXPIANO_WAIT:-45}"
TRUST=()
[ "${EXXPIANO_DEV_UNTRUSTED:-0}" = 1 ] && TRUST=(--allow-untrusted)
previous="$(cat "$BACKUP/previous" 2>/dev/null || true)"
if [ -z "$previous" ] || [ ! -s "$previous" ]; then
  echo "No package of the earlier version is kept ($BACKUP/previous): nothing is rolled back, the current version stays."
  exit 1
fi

rc-service exxpiano-wifi stop >/dev/null 2>&1     # exxpiano-wifi gives the radio back if it was serving its own network
apk add "${TRUST[@]}" "$previous" || {
  echo "apk could not install $previous: the current version stays"
  rc-service exxpiano-wifi start >/dev/null 2>&1   # back as it was: its setup network may be the only way in
  exit 1
}
[ -e "$BACKUP/exxpiano.conf" ] && cp -a "$BACKUP/exxpiano.conf" /etc/exxpiano.conf
for s in exxpiano exxpiano-fx exxpiano-web exxpiano-wifi; do rc-service "$s" restart >/dev/null 2>&1; done
sleep "$WAIT"
for s in exxpiano exxpiano-fx; do printf '%-14s ' "$s"; rc-service "$s" status 2>&1 | sed 's/^ \* //'; done
echo "Rolled back to $(apk list -I exxpiano 2>/dev/null | head -1)"
