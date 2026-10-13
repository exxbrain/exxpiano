#!/bin/bash
# DietPi postboot runs this file on the first start. The stamp below makes it a one-shot operation.
# In an image made by build-sd-image.py everything is already installed: this only finishes what needs the real device.
LOG=/var/log/exxpiano-firstboot.log
STAMP=/var/lib/exxpiano/image-firstboot.done
[ -e "$STAMP" ] && exit 0
exec >>"$LOG" 2>&1
echo "== exxpiano first start $(date -u +%FT%TZ)"

# A generic image name becomes exxpiano-<installation-id>. The ID is created automatically and persists on this installation.
python3 /opt/exxpiano/exxpiano-wifi.py ensure-name || true

# A board with little memory (a Pi 3 A+ has 512 MB) cannot hold the full piano bank: the kernel would kill the synthesizer again and again.
# The image carries a smaller bank next to it; use that one here.
LITE=/opt/exxpiano/soundfonts/piano-lite.sf2
MEM_KB=$(awk '/^MemTotal:/ {print $2}' /proc/meminfo)
if [ -s "$LITE" ] && [ "${MEM_KB:-0}" -gt 0 ] && [ "$MEM_KB" -lt 800000 ] && grep -qx 'PIANO_SF2=/opt/exxpiano/soundfonts/piano.sf2' /etc/exxpiano.conf; then
  sed -i 's#^PIANO_SF2=/opt/exxpiano/soundfonts/piano.sf2$#PIANO_SF2=/opt/exxpiano/soundfonts/piano-lite.sf2#' /etc/exxpiano.conf
  systemctl try-restart exxpiano.service || true      # it may have started already with the large bank
  echo "memory ${MEM_KB} kB: the smaller piano bank is used"
fi

# the services were enabled in the image: start them now that DietPi's setup is finished
systemctl daemon-reload
systemctl enable --now avahi-daemon.service exxpiano.service exxpiano-fx.service exxpiano-web.service exxpiano-wifi.service || true

echo "name: $(hostname), installation ID: $(cat /var/lib/exxpiano/installation-id 2>/dev/null || echo unavailable)"
install -d -m 755 /var/lib/exxpiano
touch "$STAMP"
echo "== done"
