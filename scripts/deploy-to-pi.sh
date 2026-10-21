#!/bin/bash
# Builds the packages and upgrades exxpiano on a running Pi over SSH. Nothing is ever asked on the device itself (it has no monitor): everything goes over SSH.
# With your SSH key there is no password at all. A device that does not have your key yet gets it with ssh-copy-id (the SSH password is asked that one time);
# if the key cannot be installed the script falls back to a plain password login (asked once) instead of giving up.
#
#   scripts/deploy-to-pi.sh [user@]host        (user defaults to root)
#
# The upgrade keeps /etc/exxpiano.conf, makes a backup first, and rolls back by itself if the synthesizer does not
# come back (see deploy/upgrade-on-pi.sh). Banks are not touched.
# The packages are built by scripts/build-apks.py (Docker, a few minutes); EXXPIANO_SKIP_BUILD=1 sends the ones already in dist/apk.
# A device trusts the key its image was made with. Packages signed with your development key: EXXPIANO_DEV_UNTRUSTED=1.
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
    echo "The key could not be installed: logging in with the password instead (asked once: the root password the image was built with, --password)." >&2
    AUTH=()                                                   # plain SSH: the password is asked once by the single ssh below
  fi
fi

VERSION="$(tr -d '[:space:]' < VERSION)"
APK_VERSION="${VERSION//-/_}"
[ "${EXXPIANO_SKIP_BUILD:-0}" = 1 ] || python3 -I scripts/build-apks.py --out dist/apk >/dev/null
REPO_DIR="${EXXPIANO_APK_DIR:-dist/apk/exxpiano/aarch64}"
[ -s "$REPO_DIR/exxpiano-${APK_VERSION}-r0.apk" ] || { echo "No $REPO_DIR/exxpiano-${APK_VERSION}-r0.apk: build it with scripts/build-apks.py" >&2; exit 1; }
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
cp deploy/*.sh "$STAGE/"
cp "$REPO_DIR/exxpiano-${APK_VERSION}-r0.apk" "$REPO_DIR"/py3-*.apk "$STAGE/"
echo "Deploying exxpiano $VERSION to $TARGET ..."
COPYFILE_DISABLE=1 tar -cz -C "$STAGE" . | ssh "${SSH_OPTS[@]}" "${AUTH[@]}" "$TARGET" \
  "rm -rf /root/exxpiano-deploy && mkdir -p /root/exxpiano-deploy && tar -xz -C /root/exxpiano-deploy && EXXPIANO_DEV_UNTRUSTED=${EXXPIANO_DEV_UNTRUSTED:-0} bash /root/exxpiano-deploy/upgrade-on-pi.sh"
