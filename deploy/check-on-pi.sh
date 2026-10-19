#!/bin/sh
# Runs ON THE PI as root and reports what the hardware checks of the Alpine image need: the system, the first start, the card, the services,
# the sound, MIDI, the network and the messages of this boot. It changes nothing.
#
#   ssh root@<device> sh -s < deploy/check-on-pi.sh
#
# "ok" and "FAIL" lines are the checks; everything else is information to read (the sound itself and the latency are judged by ear).
fail=0
ok() { echo "ok   $1"; }
bad() { echo "FAIL $1"; fail=1; }
check() { if eval "$2" >/dev/null 2>&1; then ok "$1"; else bad "$1"; fi; }
section() { printf '\n== %s\n' "$1"; }

section "System"
echo "Alpine $(cat /etc/alpine-release 2>/dev/null), kernel $(uname -r), $(uname -m)"
echo "board: $(tr -d '\0' < /proc/device-tree/model 2>/dev/null)"
echo "memory: $(awk '/^MemTotal:/ {printf "%d MB", $2/1024}' /proc/meminfo), uptime $(cut -d' ' -f1 /proc/uptime) s"
echo "exxpiano $(cat /opt/exxpiano/VERSION 2>/dev/null), packages: $(apk list -I 2>/dev/null | grep -E '^(exxpiano|py3-mido|py3-python-rtmidi|fluidsynth)-[0-9]' | cut -d' ' -f1 | tr '\n' ' ')"

section "First start"
check "the first start finished" "[ -e /var/lib/exxpiano/image-firstboot.done ]"
sed 's/^/  | /' /var/log/exxpiano-firstboot.log 2>/dev/null | tail -12
root_dev="$(awk '$2=="/" {print $1}' /proc/mounts | tail -1)"
disk="/sys/class/block/$(basename "$root_dev")/.."
echo "root $root_dev: $(df -h / | awk 'NR==2 {print $2 " total, " $4 " free"}'), card $(( $(cat "$disk/size" 2>/dev/null || echo 0) / 2048 / 1024 )) GB"
part_mb=$(( $(cat "/sys/class/block/$(basename "$root_dev")/size" 2>/dev/null || echo 0) / 2048 ))
disk_mb=$(( $(cat "$disk/size" 2>/dev/null || echo 0) / 2048 ))
check "the root partition fills the card (within 300 MB)" "[ $disk_mb -gt 0 ] && [ $(( disk_mb - part_mb )) -lt 300 ]"
check "an installation ID exists" "[ -s /var/lib/exxpiano/installation-id ]"
id="$(cat /var/lib/exxpiano/installation-id 2>/dev/null)"
echo "installation ID $id, host name $(hostname)"
check "the host name follows the ID" "[ \"$(hostname)\" = \"exxpiano-$(echo "$id" | tr 'A-F' 'a-f')\" ]"
echo "piano bank: $(sed -n 's/^PIANO_SF2=//p' /etc/exxpiano.conf | tail -1), graphics memory $(vcgencmd get_mem gpu 2>/dev/null)"
check "the full piano plays on a board with 1 GB" "[ \"\$(awk '/^MemTotal:/ {print \$2}' /proc/meminfo)\" -lt 800000 ] || grep -Eqx 'PIANO_SF2=\\\"?/opt/exxpiano/soundfonts/piano.sf2\\\"?' /etc/exxpiano.conf"

section "Console"
check "the status screen runs on tty1" "pgrep -f '^/bin/bash /opt/exxpiano/exxpiano-console.sh' || pgrep -f 'exxpiano-console.sh'"
check "no getty asks for a login" "! pgrep -x getty && ! pgrep -x agetty"

section "Services"
for s in exxpiano exxpiano-fx exxpiano-web exxpiano-wifi exxpiano-firstboot udev sshd chronyd avahi-daemon syslog; do
  printf '  %-20s %s\n' "$s" "$(rc-service "$s" status 2>&1 | sed 's/^ \* //')"
done
check "the synthesizer runs" "pgrep -f '^[^ ]*fluidsynth -a '"
check "the synthesizer was not restarted in a loop" "[ \"\$(grep -c 'exited with return code' /var/log/messages)\" -lt 3 ]"
check "the bridge runs" "pgrep -f '^[^ ]*python3 /opt/exxpiano/exxpiano-fx.py'"
check "the web interface answers" "wget -q -T 5 -O - http://127.0.0.1:8080/api/ping | grep -q '\"ok\": true'"
check "no control port 9800 is open" "! netstat -ltn 2>/dev/null | grep -q ':9800 '"

section "Sound"
aplay -l 2>/dev/null | grep '^card' | sed 's/^/  /'
check "a playback card exists" "aplay -l 2>/dev/null | grep -q '^card'"
grep 'audio output' /var/log/messages 2>/dev/null | tail -1 | sed 's/^/  /'
check "the 3.5 mm jack is there (Pi 3/4)" "grep -qi headphones /proc/asound/cards"
synth="$(pgrep -f -o '^[^ ]*fluidsynth -a ')"
[ -z "$synth" ] || echo "  FluidSynth $synth: $(awk '/^VmRSS:/ {printf "%d MB", $2/1024}' /proc/$synth/status), highest real-time priority of its threads $(cat /proc/$synth/task/*/stat 2>/dev/null | awk '{if ($40 > m) m = $40} END {print m + 0}')"
for f in /proc/asound/card*/pcm0p/sub0/hw_params; do [ -s "$f" ] && { echo "  $f:"; sed 's/^/    /' "$f"; }; done 2>/dev/null

section "MIDI"
aconnect -l 2>/dev/null | sed 's/^/  /' | head -20
check "the ALSA sequencer exists" "[ -e /dev/snd/seq ]"
check "FluidSynth has its MIDI port" "aconnect -l 2>/dev/null | grep -q 'FLUID Synth'"
check "the bridge found the synthesizer" "grep ' exxpiano-fx: ' /var/log/messages | grep -q 'synth: FLUID Synth'"
grep ' exxpiano-fx: ' /var/log/messages 2>/dev/null | grep -E 'synth:|input:' | tail -4 | sed 's/^/  /'

section "Power and load"
echo "  throttled: $(vcgencmd get_throttled 2>/dev/null || cat /sys/devices/platform/soc/soc:firmware/get_throttled 2>/dev/null)"
echo "  temperature: $(awk '{printf "%.1f C", $1/1000}' /sys/class/thermal/thermal_zone0/temp 2>/dev/null)"
echo "  load: $(cut -d' ' -f1-3 /proc/loadavg); free memory $(awk '/^MemAvailable:/ {printf "%d MB", $2/1024}' /proc/meminfo)"
check "no xruns or underruns were logged" "! grep -qiE 'xrun|underrun' /var/log/messages"
check "the kernel killed nothing for memory" "! dmesg | grep -qi 'out of memory'"

section "Network"
ip -4 -o addr show scope global 2>/dev/null | awk '{print "  " $2 " " $4}'
ip -4 route show default 2>/dev/null | sed 's/^/  /'
python3 /opt/exxpiano/exxpiano-wifi.py status 2>/dev/null | head -12 | sed 's/^/  /'
check "the device answers as <name>.local (avahi)" "rc-service avahi-daemon status | grep -q started"

section "Warnings and errors of this boot"
awk '/syslogd started/ {buf=""} {buf = buf $0 "\n"} END {printf "%s", buf}' /var/log/messages 2>/dev/null \
  | grep -E '\.(err|warn|crit|alert|emerg) ' | grep -v 'kern.warn kernel: .*(Bluetooth|brcmfmac: .*clm_blob)' | tail -25 | sed 's/^/  /'

echo
[ "$fail" = 0 ] && echo "all checks passed" || echo "some checks FAILED (see above)"
exit "$fail"
