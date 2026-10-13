#!/bin/bash
# Install the piano on a Raspberry Pi (DietPi / Raspberry Pi OS Lite / Debian). Run as root.
#
#   sudo ./install.sh --piano /path/piano-full.sf2 --mech /path/mech.sf2 [--device plughw:Headphones] [--gain 1.0]
#
# Safe to run again: /etc/exxpiano.conf is never overwritten,
# only the values you pass as flags are changed.
set -euo pipefail

PREFIX=/opt/exxpiano
CONF=/etc/exxpiano.conf
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PIANO="" MECH="" DEVICE="" GAIN=""

usage() { sed -n '2,8p' "$0" | sed 's/^# \{0,1\}//'; exit "${1:-0}"; }

while [ $# -gt 0 ]; do
  case "$1" in
    --piano)  PIANO="$2"; shift 2 ;;
    --mech)   MECH="$2";  shift 2 ;;
    --device) DEVICE="$2"; shift 2 ;;
    --gain)   GAIN="$2";  shift 2 ;;
    -h|--help) usage 0 ;;
    *) echo "Unknown option: $1"; usage 1 ;;
  esac
done

[ "$(id -u)" = 0 ] || { echo "Run as root: sudo $0 ..."; exit 1; }
if [ -n "$PIANO" ] && [ ! -f "$PIANO" ]; then echo "No such file: $PIANO"; exit 1; fi
if [ -n "$MECH" ] && [ ! -f "$MECH" ]; then echo "No such file: $MECH"; exit 1; fi
if [ ! -f "$CONF" ] && { [ -z "$PIANO" ] || [ -z "$MECH" ]; }; then
  echo "The first installation needs both --piano and --mech"; usage 1
fi

echo "==> Packages"
apt-get update -qq
apt-get install -y fluidsynth alsa-utils python3-mido python3-rtmidi hostapd dnsmasq-base iw wpasupplicant
apt-get install -y avahi-daemon || true      # optional: http://<hostname>.local:8080/
for dep in hostapd dnsmasq; do systemctl disable --now "$dep" 2>/dev/null || true; done   # exxpiano-wifi starts them itself

echo "==> Files in $PREFIX"
install -d "$PREFIX/soundfonts"
install -m 755 "$HERE/exxpiano-fx.py" "$PREFIX/exxpiano-fx.py"
[ -f "$HERE/../VERSION" ] && install -m 644 "$HERE/../VERSION" "$PREFIX/VERSION"
install -m 755 "$HERE/exxpiano-run" "$PREFIX/exxpiano-run"
install -m 644 "$HERE/exxpianoconf.py" "$PREFIX/exxpianoconf.py"
install -m 755 "$HERE/sf2info.py" "$PREFIX/sf2info.py"
install -m 644 "$HERE/banklib.py" "$PREFIX/banklib.py"
install -m 644 "$HERE/profiles.py" "$PREFIX/profiles.py"
install -m 644 "$HERE/sysmon.py" "$PREFIX/sysmon.py"
install -m 644 "$HERE/wifilib.py" "$PREFIX/wifilib.py"
install -m 644 "$HERE/qrlib.py" "$PREFIX/qrlib.py"
install -m 755 "$HERE/deviceid.py" "$PREFIX/deviceid.py"
install -m 755 "$HERE/exxpiano-wifi.py" "$PREFIX/exxpiano-wifi.py"
install -d /var/lib/exxpiano
install -m 755 "$HERE/exxpiano-web.py" "$PREFIX/exxpiano-web.py"
install -d "$PREFIX/web"
install -m 644 "$HERE/web/index.html" "$PREFIX/web/index.html"
[ -n "$PIANO" ] && install -m 644 "$PIANO" "$PREFIX/soundfonts/piano.sf2"
[ -n "$MECH" ]  && install -m 644 "$MECH"  "$PREFIX/soundfonts/mech.sf2"

echo "==> Configuration $CONF"
[ -f "$CONF" ] || install -m 644 "$HERE/exxpiano.conf" "$CONF"
set_conf() {  # KEY VALUE
  if grep -q "^$1=" "$CONF"; then sed -i "s|^$1=.*|$1=$2|" "$CONF"; else echo "$1=$2" >> "$CONF"; fi
}
[ -n "$PIANO" ]  && set_conf PIANO_SF2 "$PREFIX/soundfonts/piano.sf2"
[ -n "$MECH" ]   && set_conf MECH_SF2  "$PREFIX/soundfonts/mech.sf2"
[ -n "$DEVICE" ] && set_conf ALSA_DEVICE "$DEVICE"
[ -n "$GAIN" ]   && set_conf GAIN "$GAIN"

echo "==> systemd services"
# services of earlier manual setups: free the sound card and the MIDI ports
for old in fluidsynth-live midi-autoconnect; do
  if systemctl list-unit-files "$old.service" 2>/dev/null | grep -q "$old"; then
    systemctl disable --now "$old" 2>/dev/null || true
  fi
done
pkill -x fluidsynth 2>/dev/null || true
install -m 644 "$HERE/exxpiano.service" /etc/systemd/system/exxpiano.service
install -m 644 "$HERE/exxpiano-fx.service"    /etc/systemd/system/exxpiano-fx.service
install -m 644 "$HERE/exxpiano-web.service"   /etc/systemd/system/exxpiano-web.service
install -m 644 "$HERE/exxpiano-wifi.service"  /etc/systemd/system/exxpiano-wifi.service
install -m 644 "$HERE/90-exxpiano.rules" /etc/udev/rules.d/90-exxpiano.rules
udevadm control --reload 2>/dev/null || true
systemctl daemon-reload
systemctl enable exxpiano exxpiano-fx exxpiano-web exxpiano-wifi
systemctl restart exxpiano
sleep 2
systemctl restart exxpiano-fx
systemctl restart exxpiano-web
systemctl restart exxpiano-wifi || true

echo "==> Checks"
if grep -q '^ALSA_DEVICE=.*Headphones' "$CONF" && ! aplay -l 2>/dev/null | grep -qi headphones; then
  echo "WARNING: sound card 'Headphones' not found. Enable the onboard audio:"
  echo "  dtparam=audio=on in /boot/config.txt (DietPi: dietpi-config -> Audio Options), then reboot."
fi
if command -v vcgencmd >/dev/null; then
  thr="$(vcgencmd get_throttled | cut -d= -f2)"
  echo "vcgencmd get_throttled = $thr"
  [ "$thr" = "0x0" ] || echo "WARNING: insufficient power supply (should be 0x0). See the README, section 'Power supply'."
fi
echo
systemctl --no-pager --lines=0 status exxpiano exxpiano-fx || true
echo
echo "Done. Loading the sound banks takes tens of seconds, then the keyboard connects by itself."
echo "Log:  journalctl -u exxpiano -u exxpiano-fx -u exxpiano-web -f"
echo "Web interface: http://$(hostname -I | awk '{print $1}'):8080/"
