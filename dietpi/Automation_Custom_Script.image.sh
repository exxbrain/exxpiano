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

# the services were enabled in the image: start them now that DietPi's setup is finished
systemctl daemon-reload
systemctl enable --now avahi-daemon.service exxpiano.service exxpiano-fx.service exxpiano-web.service exxpiano-wifi.service || true

echo "name: $(hostname), installation ID: $(cat /var/lib/exxpiano/installation-id 2>/dev/null || echo unavailable)"
install -d -m 755 /var/lib/exxpiano
touch "$STAMP"
echo "== done"
