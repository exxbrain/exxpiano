#!/bin/bash
# Starts the OpenRC services of exxpiano in an Alpine container with stand-in programs (no sound, no radio) and checks what OpenRC makes of
# them: the synthesizer comes back after a crash, the bridge gets FX_ARGS as separate options, the Wi-Fi supervisor that ends with 0 is
# "stopped" (not "crashed", not started again), the crash is in the system log, a stop leaves no process behind.
# Needs Docker with arm64 emulation (Docker Desktop on a Mac has it). Privileged: the services set real-time and memory limits.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# EXXPIANO_ALPINE_MIRROR replaces the default mirror of the container (the same branch and keys); downloads are kept in build/apk-cache
mkdir -p "$ROOT/build/apk-cache"
ALPINE_IMAGE="${ALPINE_IMAGE:-alpine:3.24}"

docker run --rm --privileged --platform linux/arm64 -e EXXPIANO_ALPINE_MIRROR -v "$ROOT/build/apk-cache:/var/cache/apk" -v "$ROOT:/repo:ro" "$ALPINE_IMAGE" sh -euc '
[ -z "${EXXPIANO_ALPINE_MIRROR:-}" ] || sed -i "s#^https://dl-cdn.alpinelinux.org/alpine#${EXXPIANO_ALPINE_MIRROR%/}#" /etc/apk/repositories
ln -sfn /var/cache/apk /etc/apk/cache
apk add -q openrc logger python3 bash util-linux-misc >/dev/null 2>&1
mkdir -p /run/openrc /opt/exxpiano; touch /run/openrc/softlevel
syslogd -O /var/log/messages
cp /repo/pi/exxpiano-env /repo/pi/exxpianoconf.py /repo/pi/exxpiano-respawn /opt/exxpiano/
cp /repo/pi/init.d/* /etc/init.d/
cp /repo/pi/exxpiano.conf /etc/exxpiano.conf
cat > /opt/exxpiano/exxpiano-run <<"S"
#!/bin/sh
echo "exxpiano: audio output plughw:Fake (usb), period 64 x 3"
if [ ! -e /tmp/crashed ]; then touch /tmp/crashed; exit 3; fi
exec sleep 1000
S
chmod 755 /opt/exxpiano/exxpiano-run
printf "import sys, time\nprint(\"exxpiano: fx args\", \" \".join(sys.argv[1:]), flush=True)\ntime.sleep(1000)\n" > /opt/exxpiano/exxpiano-fx.py
printf "import time\ntime.sleep(1000)\n" > /opt/exxpiano/exxpiano-web.py
printf "import sys\nsys.exit(0)\n" > /opt/exxpiano/exxpiano-wifi.py
for s in exxpiano exxpiano-fx exxpiano-web exxpiano-wifi; do rc-service $s start >/dev/null 2>&1; done
sleep 5
fail=0
check() { if eval "$2"; then echo "ok   $1"; else echo "FAIL $1"; fail=1; fi; }
for s in exxpiano exxpiano-fx exxpiano-web; do check "$s is started" "rc-service $s status | grep -q started"; done
check "the Wi-Fi supervisor that ended with 0 is stopped" "rc-service exxpiano-wifi status | grep -q stopped"
check "the synthesizer was started again after its crash" "[ \$(grep -c \"Child command line: .*exxpiano-run\" /var/log/messages) -ge 2 ] && pgrep -x sleep >/dev/null"
check "the crash is in the log" "grep -q \"daemon.warn supervise-daemon.*exited with return code 3\" /var/log/messages"
check "FX_ARGS became separate options" "grep -q \"exxpiano-fx: exxpiano: fx args --velocity-curve 1 --hammer-boost 1.5\" /var/log/messages"
check "the limits applied" "! grep -q \"unable to apply RC_ULIMIT\|cannot set nice\" /var/log/messages"
for s in exxpiano-fx exxpiano-web exxpiano; do rc-service $s stop >/dev/null 2>&1; done
sleep 1
# (by name: pgrep -f would find this very script, whose text names the programs)
check "a stop leaves no process" "! pgrep -x sleep >/dev/null && ! pgrep -x python3 >/dev/null && ! pgrep -x supervise-daemon >/dev/null"
[ "$fail" = 0 ] || { echo "--- log"; cat /var/log/messages; exit 1; }
'
