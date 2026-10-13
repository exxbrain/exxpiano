#!/bin/bash
# Builds the package and upgrades exxpiano on a running Pi over SSH. Nothing is ever asked on the device itself (it has no monitor): everything goes over SSH.
# With your SSH key there is no password at all. A device that does not have your key yet gets it with ssh-copy-id (the SSH password is asked that one time);
# if the key cannot be installed the script falls back to a plain password login (asked once) instead of giving up.
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
AUTH=(-o BatchMode=yes)                                       # key login: no prompt is possible
if ! ssh "${SSH_OPTS[@]}" "${AUTH[@]}" "$TARGET" true </dev/null 2>/dev/null; then
  echo "No key login to $TARGET yet: installing your SSH key (the SSH password is asked this one time)."
  if command -v ssh-copy-id >/dev/null 2>&1 && ssh-copy-id "${SSH_OPTS[@]}" "$TARGET" && ssh "${SSH_OPTS[@]}" "${AUTH[@]}" "$TARGET" true </dev/null; then
    :
  else
    echo "The key could not be installed: logging in with the password instead (asked once; DietPi's default is dietpi unless the image was built with --password)." >&2
    AUTH=()                                                   # plain SSH: the password is asked once by the single ssh below
  fi
fi

VERSION="$(tr -d '[:space:]' < VERSION)"
python3 -I scripts/build-deb.py --out dist >/dev/null
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
cp deploy/*.sh "$STAGE/"
cp "dist/exxpiano_${VERSION}_all.deb" "$STAGE/"
echo "Deploying exxpiano $VERSION to $TARGET ..."
COPYFILE_DISABLE=1 tar -cz -C "$STAGE" . | ssh "${SSH_OPTS[@]}" "${AUTH[@]}" "$TARGET" \
  "rm -rf /root/exxpiano-deploy && mkdir -p /root/exxpiano-deploy && tar -xz -C /root/exxpiano-deploy && bash /root/exxpiano-deploy/upgrade-on-pi.sh"
