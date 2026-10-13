#!/bin/bash
# What the local console (HDMI + keyboard) shows instead of a login prompt: the state of the device and where to find it. Nothing is asked here:
# the device is used without a monitor, and a password prompt on its screen would only confuse. Administration is over SSH (user root).
# EXXPIANO_CONSOLE_ONCE=1 prints the screen once and exits (for tests); the other EXXPIANO_* variables only move the files for tests.
ID_FILE="${EXXPIANO_INSTALLATION_ID_PATH:-/var/lib/exxpiano/installation-id}"
PORT="${EXXPIANO_WEB_PORT:-8080}"

render() {
  local id host ips ip
  id="$(cat "$ID_FILE" 2>/dev/null | tr -d '\n' || true)"
  host="$(hostname 2>/dev/null || echo exxpiano)"; host="${host%.local}"
  ips="${EXXPIANO_IPS-$(hostname -I 2>/dev/null || true)}"
  printf '\033[2J\033[H'
  printf 'exxpiano digital piano\n'
  printf '======================\n\n'
  printf 'Installation ID : %s\n' "${id:-(is being created, a few seconds)}"
  printf 'Name            : %s\n\n' "$host"
  if [ -n "${ips// /}" ]; then
    printf 'The control page:\n'
    for ip in $ips; do printf '  http://%s:%s/\n' "$ip" "$PORT"; done
    printf '  http://%s.local:%s/\n\n' "$host" "$PORT"
  else
    printf 'No network yet. If none is found the device starts its own Wi-Fi:\n'
    printf '  %s   (then open http://10.42.0.1:%s/)\n\n' "exxpiano-Setup-${id:-<ID>}" "$PORT"
  fi
  printf 'There is no login on this screen and nothing to type here:\n'
  printf 'the piano starts by itself. Administration: ssh root@<address>.\n'
}

if [ "${EXXPIANO_CONSOLE_ONCE:-0}" = 1 ]; then render; exit 0; fi
stty -echo 2>/dev/null || true
while :; do
  render
  read -rs -t 5 -n 100 _ 2>/dev/null || true              # keys are swallowed (and not shown) instead of piling up in the terminal
done
