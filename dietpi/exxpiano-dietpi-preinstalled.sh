#!/bin/sh
# DietPi's firstboot applies hardware, password and regional settings, then normally schedules an online update.
# The product image is already provisioned, so finish in DietPi's supported pre-installed stage instead.
set -eu

printf '10\n' > /boot/dietpi/.install_stage

# AUTO_SETUP_AUTOMATED creates temporary console autologin overrides for the online first-run workflow.
# That workflow is intentionally skipped in the product image.
rm -f /etc/systemd/system/getty@tty1.service.d/dietpi-autologin.conf \
      /etc/systemd/system/console-getty.service.d/dietpi-autologin.conf
systemctl daemon-reload
