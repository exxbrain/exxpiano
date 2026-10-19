#!/bin/bash
# Installs, upgrades and rolls back the real exxpiano packages in an Alpine container with OpenRC (no sound card: a stand-in FluidSynth that
# refuses to run under the version 9.9.9 plays the "upgrade that breaks the synthesizer"). Checks:
#   - the package installs from the signed repository and its services start;
#   - an upgrade keeps the configuration the owner changed and keeps the package for a rollback;
#   - an upgrade whose synthesizer does not come back is rolled back by apk to the version before (apk and the files agree);
#   - a package with a signature the device does not trust is refused before anything changes;
#   - without a kept package a rollback says so and changes nothing;
#   - removing the package stops its services and keeps the installation ID.
#
#   scripts/test-apk-upgrade.sh        (builds dist/apk if it is not there, and two test versions in build/apk-upgrade-test)
# Needs Docker with arm64 (Docker Desktop on a Mac). Privileged: the services set real-time and memory limits.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# EXXPIANO_ALPINE_MIRROR replaces the default mirror of the container (the same branch and keys); downloads are kept in build/apk-cache
mkdir -p "$ROOT/build/apk-cache"
VERSION="$(tr -d '[:space:]' < "$ROOT/VERSION")"; VERSION="${VERSION//-/_}"
BASE="$ROOT/dist/apk"
TEST="$ROOT/build/apk-upgrade-test"

[ -s "$BASE/exxpiano/aarch64/exxpiano-$VERSION-r0.apk" ] || python3 -I "$ROOT/scripts/build-apks.py" --out "$BASE"
for v in 9.9.8 9.9.9; do
  if [ ! -s "$TEST/$v/exxpiano/aarch64/exxpiano-$v-r0.apk" ]; then
    # the libraries of the base repository: abuild finds the dependencies of the test version there
    rm -rf "${TEST:?}/${v:?}"; mkdir -p "$TEST"; cp -R "$BASE" "$TEST/$v"; rm -f "$TEST/$v"/exxpiano/aarch64/exxpiano-[0-9]*.apk
    python3 -I "$ROOT/scripts/build-apks.py" --out "$TEST/$v" --version "$v" --packages exxpiano
  fi
done

docker run --rm --privileged --platform linux/arm64 -e EXXPIANO_ALPINE_MIRROR -v "$ROOT/build/apk-cache:/var/cache/apk" -e VERSION="$VERSION" \
  -v "$ROOT:/repo:ro" -v "$BASE:/base:ro" -v "$TEST:/test:ro" alpine:3.24 sh -c '
set -uo pipefail
[ -z "${EXXPIANO_ALPINE_MIRROR:-}" ] || sed -i "s#^https://dl-cdn.alpinelinux.org/alpine#${EXXPIANO_ALPINE_MIRROR%/}#" /etc/apk/repositories
ln -sfn /var/cache/apk /etc/apk/cache
apk add -q bash openrc logger procps >/dev/null 2>&1 || { apk add -q openrc logger procps; }
mkdir -p /run/openrc; touch /run/openrc/softlevel
syslogd -O /var/log/messages
cp /base/*.rsa.pub /etc/apk/keys/
echo /base/exxpiano >> /etc/apk/repositories
fail=0
check() { if eval "$2"; then echo "ok   $1"; else echo "FAIL $1"; fail=1; fi; }
installed() { apk list -I exxpiano 2>/dev/null | sed -n "s/^exxpiano-\([^ ]*\)-r0 .*/\1/p" | head -1; }

echo "== install $VERSION"
apk add -q exxpiano >/tmp/install.log 2>&1 || { cat /tmp/install.log; exit 1; }
check "the package is installed" "[ \"\$(installed)\" = \"$VERSION\" ]"
check "its services are in the default runlevel" "rc-update show default | grep -q exxpiano-fx"
check "the MIDI libraries import" "python3 -c \"import mido, rtmidi\""
mkdir -p /var/lib/exxpiano/packages; cp /base/exxpiano/aarch64/exxpiano-$VERSION-r0.apk /var/lib/exxpiano/packages/exxpiano-$VERSION-r0.apk    # what the image builder keeps

# the owner changes the configuration: the stand-in synthesizer, an explicit output, no meter
cat >> /etc/exxpiano.conf <<CONF
FLUIDSYNTH=/usr/local/bin/fluidsynth
ALSA_DEVICE=plughw:Fake
METER=0
CONF
cat > /usr/local/bin/fluidsynth <<"FAKE"
#!/bin/sh
[ "$(cat /opt/exxpiano/VERSION)" = 9.9.9 ] && { echo "fake fluidsynth: this version cannot start" >&2; exit 1; }
while :; do sleep 1; done
FAKE
chmod 755 /usr/local/bin/fluidsynth
for s in exxpiano exxpiano-fx exxpiano-web; do rc-service $s start >/dev/null 2>&1; done
sleep 3
# (patterns from the start of the command line: the text of this very script names the programs too)
synth_runs() { pgrep -f "^/bin/sh /usr/local/bin/fluidsynth -a " >/dev/null; }
check "the synthesizer runs" "synth_runs"

run_upgrade() {      # the folder the deploy script would send: the package, the libraries, the two scripts
  rm -rf /deploy; mkdir /deploy; cp /repo/deploy/*.sh /deploy/; cp "$1" /deploy/; cp /base/exxpiano/aarch64/py3-*.apk /deploy/
  EXXPIANO_WAIT=4 EXXPIANO_STEADY=3 bash /deploy/upgrade-on-pi.sh > /tmp/upgrade.log 2>&1
}

echo "== upgrade to 9.9.8"
run_upgrade /test/9.9.8/exxpiano/aarch64/exxpiano-9.9.8-r0.apk; rc=$?
check "the upgrade succeeds" "[ $rc = 0 ]" || true
[ $rc = 0 ] || cat /tmp/upgrade.log
check "9.9.8 is installed" "[ \"\$(installed)\" = 9.9.8 ]"
check "the configuration of the owner is kept" "grep -qx \"FLUIDSYNTH=/usr/local/bin/fluidsynth\" /etc/exxpiano.conf"
check "the new package is kept for the next rollback" "[ -s /var/lib/exxpiano/packages/exxpiano-9.9.8-r0.apk ]"

echo "== upgrade to 9.9.9, whose synthesizer does not start"
run_upgrade /test/9.9.9/exxpiano/aarch64/exxpiano-9.9.9-r0.apk; rc=$?
check "the upgrade reports a failure" "[ $rc != 0 ] && grep -q \"rolling back\" /tmp/upgrade.log"
check "apk rolled back to 9.9.8" "[ \"\$(installed)\" = 9.9.8 ] && [ \"\$(cat /opt/exxpiano/VERSION)\" = 9.9.8 ]"
sleep 3
check "the synthesizer runs again" "synth_runs"
check "the configuration is the one from before" "grep -qx \"ALSA_DEVICE=plughw:Fake\" /etc/exxpiano.conf"

echo "== a package this device does not trust"
mv /etc/apk/keys/exxpiano*.rsa.pub /tmp/
run_upgrade /test/9.9.9/exxpiano/aarch64/exxpiano-9.9.9-r0.apk; rc=$?
check "it is refused before anything changes" "[ $rc != 0 ] && grep -q \"not one this device trusts\" /tmp/upgrade.log && [ \"\$(installed)\" = 9.9.8 ]"
mv /tmp/exxpiano*.rsa.pub /etc/apk/keys/

echo "== a rollback without a kept package"
rm -f /root/exxpiano-backup/previous
EXXPIANO_WAIT=1 bash /repo/deploy/rollback-on-pi.sh > /tmp/rollback.log 2>&1; rc=$?
check "it says so and keeps the current version" "[ $rc != 0 ] && grep -q \"nothing is rolled back\" /tmp/rollback.log && [ \"\$(installed)\" = 9.9.8 ]"

echo "== removing the package"
python3 /opt/exxpiano/exxpiano-wifi.py ensure-name >/dev/null 2>&1 || true
check "an installation ID exists" "[ -s /var/lib/exxpiano/installation-id ]"
apk del -q exxpiano >/dev/null 2>&1
sleep 1
check "its services are stopped and gone from the runlevel" "! synth_runs && ! pgrep -f \"^/usr/bin/python3 /opt/exxpiano/\" >/dev/null && ! rc-update show default | grep -q exxpiano"
check "the installation ID stays" "[ -s /var/lib/exxpiano/installation-id ]"
[ "$fail" = 0 ] || { echo "--- last upgrade log"; cat /tmp/upgrade.log; echo "--- system log"; tail -40 /var/log/messages; exit 1; }
'
