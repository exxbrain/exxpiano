#!/bin/bash
# Runs ON THE PI as root: restores the exxpiano files saved by upgrade-on-pi.sh (the version before the upgrade).
BACKUP="${EXXPIANO_BACKUP:-/root/exxpiano-backup}"
[ -d "$BACKUP/opt" ] || { echo "No backup in $BACKUP"; exit 1; }

systemctl stop exxpiano-wifi exxpiano-web exxpiano-fx exxpiano 2>/dev/null     # exxpiano-wifi gives the radio back if it was serving its own network
# program files and units as they were (the banks were never touched)
cp -a "$BACKUP/opt/." /opt/exxpiano/
[ -e "$BACKUP/exxpiano.conf" ] && cp -a "$BACKUP/exxpiano.conf" /etc/exxpiano.conf
for u in exxpiano exxpiano-fx; do
  [ -e "$BACKUP/units/$u.service" ] && cp -a "$BACKUP/units/$u.service" "/lib/systemd/system/$u.service"
done
# an older version had no web interface: take its pieces away again
if [ ! -e "$BACKUP/units/exxpiano-web.service" ]; then
  systemctl disable exxpiano-web 2>/dev/null
  rm -f /lib/systemd/system/exxpiano-web.service /opt/exxpiano/exxpiano-web.py /opt/exxpiano/exxpianoconf.py
  rm -rf /opt/exxpiano/web
fi
# an older version had no Wi-Fi setup either
if [ ! -e "$BACKUP/units/exxpiano-wifi.service" ]; then
  systemctl disable exxpiano-wifi 2>/dev/null
  rm -f /lib/systemd/system/exxpiano-wifi.service /opt/exxpiano/exxpiano-wifi.py /opt/exxpiano/wifilib.py
fi
systemctl daemon-reload
systemctl restart exxpiano
sleep 45
systemctl restart exxpiano-fx
sleep 3
systemctl is-active exxpiano exxpiano-fx
cat "$BACKUP/version.txt" 2>/dev/null
echo "Rolled back the files. (dpkg still lists the newer version; reinstall a package later to make them agree.)"
