#!/bin/bash
# Builds the package and upgrades exxpiano on a running Pi over SSH. It never asks for a password during an upgrade: it logs in with your SSH key.
# The first time for a device (no key installed there yet) it installs your key with ssh-copy-id: the password is asked that one time, never again.
#
#   scripts/deploy-to-pi.sh [user@]host        (user defaults to root)
#
# The upgrade keeps /etc/exxpiano.conf, makes a backup first, and rolls back by itself if the synthesizer does not
# come back (see deploy/upgrade-on-pi.sh). Banks are not touched.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
TARGET="${1:?usage: scripts/deploy-to-pi.sh [user@]host}"
case "$TARGET" in *@*) ;; *) TARGET="root@$TARGET" ;; esac

# No questions: a new host key is accepted the first time (a CHANGED one still stops the script), and a password is never asked for in the middle of an upgrade.
SSH_OPTS=(-o StrictHostKeyChecking=accept-new -o ConnectTimeout=10)
if ! ssh "${SSH_OPTS[@]}" -o BatchMode=yes "$TARGET" true </dev/null 2>/dev/null; then
  echo "No key login to $TARGET yet: installing your SSH key (the password is asked this one time)."
  if ! command -v ssh-copy-id >/dev/null 2>&1; then echo "ssh-copy-id is missing: add your public key to ~/.ssh/authorized_keys on the device by hand" >&2; exit 1; fi
  ssh-copy-id "${SSH_OPTS[@]}" "$TARGET" || { echo "Could not install the key on $TARGET (is the address and the password right? do you have a key: ls ~/.ssh/*.pub; ssh-keygen -t ed25519)" >&2; exit 1; }
  ssh "${SSH_OPTS[@]}" -o BatchMode=yes "$TARGET" true </dev/null || { echo "Key login to $TARGET still does not work" >&2; exit 1; }
fi

VERSION="$(tr -d '[:space:]' < VERSION)"
python3 -I scripts/build-deb.py --out dist >/dev/null
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
cp deploy/*.sh "$STAGE/"
cp "dist/exxpiano_${VERSION}_all.deb" "$STAGE/"
echo "Deploying exxpiano $VERSION to $TARGET ..."
COPYFILE_DISABLE=1 tar -cz -C "$STAGE" . | ssh "${SSH_OPTS[@]}" -o BatchMode=yes "$TARGET" \
  "rm -rf /root/exxpiano-deploy && mkdir -p /root/exxpiano-deploy && tar -xz -C /root/exxpiano-deploy && bash /root/exxpiano-deploy/upgrade-on-pi.sh"
