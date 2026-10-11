#!/bin/bash
# DietPi runs this file once, at the first start, after its own first-run setup (dietpi.txt: AUTO_SETUP_CUSTOM_SCRIPT_EXEC=0).
# In an image made by build-sd-image.py everything is already installed: this only finishes what needs the real device.
LOG=/var/log/piano-synth-firstboot.log
exec >>"$LOG" 2>&1
echo "== piano-synth first start $(date -u +%FT%TZ)"

# the name: with a number given when the image was built the host name is already piano-<number> (dietpi.txt); a generic name
# (dietpi, raspberrypi ...) becomes piano-<last digits of the Wi-Fi address>; a chosen name is kept
python3 /opt/piano-synth/piano-wifi.py ensure-name || true

# the services were enabled in the image: start them now that DietPi's setup is finished
systemctl daemon-reload
systemctl enable --now avahi-daemon.service piano-synth.service piano-fx.service piano-web.service piano-wifi.service || true

echo "name: $(hostname), number: $(sed -n 's/^DEVICE_NUMBER=//p' /etc/piano-synth.conf | head -1)"
echo "== done"
