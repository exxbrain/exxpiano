#!/bin/bash
# Builds the package and upgrades exxpiano on a running Pi over SSH (you type the password once).
#
#   scripts/deploy-to-pi.sh [user@]host        (user defaults to root)
#
# The upgrade keeps /etc/exxpiano.conf, makes a backup first, and rolls back by itself if the synthesizer does not
# come back (see deploy/upgrade-on-pi.sh). Banks are not touched.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
TARGET="${1:?usage: scripts/deploy-to-pi.sh [user@]host}"
case "$TARGET" in *@*) ;; *) TARGET="root@$TARGET" ;; esac

VERSION="$(tr -d '[:space:]' < VERSION)"
python3 -I scripts/build-deb.py --out dist >/dev/null
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
cp deploy/*.sh "$STAGE/"
cp "dist/exxpiano_${VERSION}_all.deb" "$STAGE/"
echo "Deploying exxpiano $VERSION to $TARGET ..."
COPYFILE_DISABLE=1 tar -cz -C "$STAGE" . | ssh "$TARGET" \
  "rm -rf /root/exxpiano-deploy && mkdir -p /root/exxpiano-deploy && tar -xz -C /root/exxpiano-deploy && bash /root/exxpiano-deploy/upgrade-on-pi.sh"
