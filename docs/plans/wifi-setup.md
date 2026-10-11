# Wi-Fi setup: connecting a new device to a network

**Goal.** A Pi that has no known network (new device, moved house, new router) starts its own access point; a phone joins it, opens the page, picks the home
network and types the password. No cable, no file editing, no screen.

## What the Pi we have is

DietPi (Debian 13) with `ifupdown` + `wpa_supplicant` + `dhclient`; `wlan0` is configured in `/etc/network/interfaces.d/wlan0.conf` (`wpa-conf
/etc/wpa_supplicant/wpa_supplicant.conf`). No NetworkManager, no `hostapd`, no `dnsmasq`, no `avahi`. The Pi 3 radio supports AP mode. Raspberry Pi OS uses
NetworkManager instead: there the feature stays off and says so (`nmtui` does the job).

## Decisions

| Question | Decision | Why |
|---|---|---|
| When does the access point start? | When there is no default route (no Ethernet, no Wi-Fi with an address) for longer than a grace time: 30 s if no network is saved, 150 s if some are | after a power cut the Pi boots faster than the router: it must not give up on a known network too early |
| Does it stay in AP mode for ever? | With saved networks it leaves the AP every 2 minutes without a client and tries them again for 45 s | a router that comes back later is found again without anyone touching the Pi |
| AP security | WPA2, default password `pianosetup`, SSID `Piano-Setup-XXXX` (XXXX = last digits of the MAC); both settable in `/etc/piano-synth.conf` | the home Wi-Fi password travels over this radio link: it must be encrypted; a sticker with the password is enough for a device sold to a buyer |
| How does the phone find the page? | dnsmasq answers every name with the Pi's address and port 80 redirects to the page: the phone shows its "sign in to network" window; `http://10.42.0.1:8080/` works by hand | captive-portal detection of Android and iOS |
| Where is the password stored? | Only the derived 256-bit PSK (PBKDF2-SHA1, 4096 rounds, as `wpa_passphrase` does) goes into `wpa_supplicant.conf` (0600); the password is never logged, never passed on a command line | the same format DietPi already uses |
| Who changes the network? | One owner: the service `piano-wifi` (root). The web server only writes a request file in `/run/piano-wifi/` (0600) and reads a status file | no two processes fight over `wlan0` |
| What if the new network does not work? | The old configuration is restored (wrong password and out of range look the same to the radio); in AP mode the AP comes back with the reason on the page | the device can never be left without a way in |
| After success, how do I find it? | `http://<hostname>.local:8080/` through `avahi-daemon` (recommended package), plus the router's client list | the phone leaves the AP and cannot be told the new address |

## Phases

1. `pi/wifilib.py`: `wpa_supplicant.conf` editing (add / forget network, PSK derivation), `iw scan` parsing, `hostapd` and `dnsmasq` configuration. Pure functions.
2. `pi/piano-wifi.py` + `piano-wifi.service`: the supervisor (state machine client / AP, requests, status file), tested with a fake system.
3. Web: `GET/POST /api/wifi`, the **Wi-Fi** tab (status, saved networks, scan, connect), the page opens it by itself while the Pi is in AP mode.
4. Package: `hostapd`, `dnsmasq-base` as dependencies (no system services started), `avahi-daemon` recommended; documentation EN/RU.
5. Live test on the Pi: `piano-wifi ap-test <seconds>` (AP for a limited time, then back to the normal network by itself), with someone able to reach the Pi.

## Not in scope now

Hidden-network scanning without knowing the name (the name can be typed), WPA-Enterprise, a static address, several Wi-Fi adapters, Ethernet configuration.
