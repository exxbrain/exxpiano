#!/usr/bin/env python3
"""exxpiano-wifi: keeps the Pi reachable. Without a network it starts its own access point so a phone can pick the home Wi-Fi.

  exxpiano-wifi.py            run the supervisor (the OpenRC service exxpiano-wifi does this)
  exxpiano-wifi.py status     print what the supervisor says
  exxpiano-wifi.py scan       scan now (client mode only)
  exxpiano-wifi.py ensure-name [NAME ...]  give a device that still has a generic name the unique name exxpiano-<installation-id>
                              (the NAMEs count as generic too: the first start passes the generic name of the image, exxpiano)
  exxpiano-wifi.py ap-test N  start the access point for N seconds, then go back to the normal network by itself

The supervisor is the only process that touches wlan0. The web page asks it for things through files in /run/exxpiano-wifi
(see wifilib.py). Written for Alpine Linux: in client mode it runs wpa_supplicant and udhcpc on wlan0 itself (ifupdown-ng only looks after
the wired port of the board); where NetworkManager, iwd or connman runs it stays out of the way.
"""
import http.server
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wifilib  # noqa: E402

IFACE = os.environ.get('WIFI_IFACE', 'wlan0')
WPA_CONF = os.environ.get('WIFI_WPA_CONF', '/etc/wpa_supplicant/wpa_supplicant.conf')
TICK = 3.0
NET_DIR = '/etc/network'
CTRL_LINE = 'ctrl_interface=/run/wpa_supplicant'                       # root only: the supervisor and the web interface run as root
SYS_NET = '/sys/class/net'
OTHER_MANAGERS = ('networkmanager', 'iwd', 'connman')                  # OpenRC services of network managers that own Wi-Fi themselves
WIRED_RE = re.compile(r'^(eth\d+|en[a-z0-9]+)$')


def log(*parts):
    print('exxpiano-wifi:', *parts, file=sys.stderr, flush=True)       # never a password or a key: only names and states


def config_from_env(env=None):
    env = os.environ if env is None else env

    def num(key, default, lo, hi):
        try:
            return max(lo, min(hi, int(env.get(key, default))))
        except ValueError:
            return default
    password = env.get('WIFI_AP_PASSWORD') or wifilib.AP_DEFAULT_PASSWORD
    try:
        wifilib.validate_passphrase(password)
    except ValueError:
        log('WIFI_AP_PASSWORD is not 8 to 63 ordinary characters: using the default')
        password = wifilib.AP_DEFAULT_PASSWORD
    channel = num('WIFI_AP_CHANNEL', 6, 1, 11)
    return dict(ap_prefix=env.get('WIFI_AP_SSID') or 'exxpiano-Setup', ap_password=password, channel=channel if channel in wifilib.CHANNELS_2G else 6,
                grace_new=num('WIFI_GRACE_NEW', 10, 5, 3600), grace_known=num('WIFI_GRACE_KNOWN', 150, 5, 3600),
                retry_idle=num('WIFI_RETRY_IDLE', 120, 20, 3600), retry_wait=num('WIFI_RETRY_WAIT', 45, 10, 300),
                connect_wait=num('WIFI_CONNECT_WAIT', 45, 10, 300), web_port=num('WEB_PORT', 8080, 1, 65535),
                enabled=env.get('WIFI_SETUP', '1') != '0')


# ---------------------------------------------------------------- the real system
class System:
    """Everything that touches the machine; the tests replace it."""

    def __init__(self, iface=IFACE, run_dir=wifilib.RUN_DIR, wpa_conf=WPA_CONF, run=None, net_dir=NET_DIR, sys_net=SYS_NET, country=None,
                 proc='/proc', kill=os.kill):
        self.iface, self.run_dir, self.wpa_conf = iface, run_dir, wpa_conf
        self.net_dir, self.sys_net, self.proc, self._kill = net_dir, sys_net, proc, kill
        # the Wi-Fi country of the image (WIFI_COUNTRY in /etc/exxpiano.conf): written into wpa_supplicant.conf when that has none
        self.country = (country if country is not None else os.environ.get('WIFI_COUNTRY', '')).strip().upper() or None
        self._wired = {}                                # wired interface -> True while a DHCP client was started for the cable that is plugged in
        self._run = run or self._real_run
        self.procs = []
        self.scan_error = None                          # why the last scan found nothing, or None

    @staticmethod
    def _real_run(cmd, timeout=15):
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
            return r.returncode, r.stdout + r.stderr
        except (OSError, subprocess.SubprocessError) as exc:
            return 127, str(exc)

    def run(self, cmd, timeout=15):
        return self._run(cmd, timeout)

    # -- facts
    def managed_elsewhere(self):
        """Another network manager owns Wi-Fi (NetworkManager, iwd or connman runs): this program must not interfere."""
        for name in OTHER_MANAGERS:
            rc, out = self.run(['rc-service', name, 'status'], 5)
            if re.search(r'status: (started|starting)', out or ''):
                return True
        return False

    def exists(self):
        return os.path.isdir('/sys/class/net/%s' % self.iface)

    def mac(self):
        try:
            with open('/sys/class/net/%s/address' % self.iface) as f:
                return f.read().strip()
        except OSError:
            return ''

    def hostname(self):
        return socket.gethostname()

    def has_default_route(self):
        rc, out = self.run(['ip', '-4', 'route', 'show', 'default'], 5)
        return rc == 0 and bool(out.strip())

    def has_cable_route(self):
        """A default route that does not go through the Wi-Fi: Ethernet is another way in."""
        rc, out = self.run(['ip', '-4', 'route', 'show', 'default'], 5)
        return rc == 0 and any(m.group(1) != self.iface for m in re.finditer(r'\bdev (\S+)', out))

    def link(self):
        """{ssid, ip, signal} of the Wi-Fi connection, or None."""
        rc, out = self.run(['wpa_cli', '-i', self.iface, 'status'], 5)
        if rc != 0:
            return None
        kv = dict(line.split('=', 1) for line in out.splitlines() if '=' in line)
        if kv.get('wpa_state') != 'COMPLETED':
            return None
        link = dict(ssid=kv.get('ssid', ''), ip=kv.get('ip_address') or None, signal=None)
        rc, out = self.run(['iw', 'dev', self.iface, 'link'], 5)
        m = re.search(r'signal: (-?\d+) dBm', out) if rc == 0 else None
        if m:
            link['signal'] = int(m.group(1))
        return link

    def scan(self):
        found, self.scan_error = wifilib.scan_networks(self.run, self.iface, sleep=time.sleep)
        return found or []

    def stations(self):
        rc, out = self.run(['iw', 'dev', self.iface, 'station', 'dump'], 5)
        return len(re.findall(r'(?m)^Station ', out)) if rc == 0 else 0

    # -- the wired ports: the board's own one has a stanza in /etc/network/interfaces (ifupdown-ng); a USB adapter gets nothing by itself
    def _interfaces_files(self):
        files = [os.path.join(self.net_dir, 'interfaces')]
        folder = os.path.join(self.net_dir, 'interfaces.d')
        try:
            files += [os.path.join(folder, n) for n in sorted(os.listdir(folder))]
        except OSError:
            pass
        return files

    def _has_stanza(self, name=None):
        name = name or self.iface
        for path in self._interfaces_files():
            try:
                with open(path, encoding='utf-8', errors='replace') as f:
                    if re.search(r'(?m)^\s*iface\s+%s\s' % re.escape(name), f.read()):
                        return True
            except OSError:
                continue
        return False

    def ensure_wired(self):
        """A wired interface that nothing configures (a USB Ethernet adapter or a USB hub with a network port: ifupdown-ng knows only eth0) gets
        an address by DHCP when a cable is plugged in, so that the Pi can be reached and the setup network can give way to the cable.
        One DHCP client per plugged cable; an interface with a stanza, or that already has an address, is left to its owner."""
        try:
            names = sorted(os.listdir(self.sys_net))
        except OSError:
            return
        for name in names:
            if not WIRED_RE.match(name) or not os.path.isdir(os.path.join(self.sys_net, name, 'device')) or self._has_stanza(name):
                continue
            self.run(['ip', 'link', 'set', name, 'up'], 5)
            try:
                with open(os.path.join(self.sys_net, name, 'carrier'), encoding='ascii') as f:
                    plugged = f.read().strip() == '1'
            except OSError:
                plugged = False
            if not plugged:
                self._wired.pop(name, None)                           # unplugged: the next cable is a new one
                continue
            if self._wired.get(name):
                continue
            self._wired[name] = True
            rc, out = self.run(['ip', '-4', '-o', 'addr', 'show', 'dev', name], 5)
            if rc == 0 and out.strip():
                continue
            log('a cable is plugged in %s and nothing configures it: asking for an address' % name)
            rc, out = self.run(['udhcpc', '-i', name, '-b', '-S', '-p', os.path.join(self.run_dir, 'udhcpc-%s.pid' % name)], 20)
            if rc != 0:
                log('udhcpc on %s failed: %s' % (name, out.strip()[-160:]))

    def ensure_client_config(self):
        """Make wpa_supplicant possible on a card that was never set up for Wi-Fi: Wi-Fi not blocked, and a wpa_supplicant.conf that wpa_cli
        can talk to (with the Wi-Fi country). What is already there is left alone."""
        self.run(['rfkill', 'unblock', 'wifi'], 5)
        try:
            with open(self.wpa_conf, encoding='utf-8') as f:
                text = f.read()
        except OSError:
            text = ''
        # a file brought over from DietPi names the group netdev, which Alpine does not have (wpa_supplicant would not open its socket)
        new = re.sub(r'(?m)^ctrl_interface=DIR=/run/wpa_supplicant GROUP=netdev$', CTRL_LINE, text)
        country = wifilib.country(text) or self.country
        if 'ctrl_interface' not in new:
            new = CTRL_LINE + '\n' + new
        if country and not wifilib.country(new):
            first, _, rest = new.partition('\n')
            new = first + '\ncountry=' + country + '\n' + rest if first.startswith('ctrl_interface') else 'country=' + country + '\n' + new
        if new != text:
            os.makedirs(os.path.dirname(os.path.abspath(self.wpa_conf)), exist_ok=True)
            wifilib.write_secret(self.wpa_conf, new)
            log('wpa_supplicant.conf was missing its header: written')
        if country:
            self.run(['iw', 'reg', 'set', country], 5)

    def _pidfile(self, program):
        return os.path.join(self.run_dir, '%s-%s.pid' % (program, self.iface))

    def _stop(self, program):
        """End the program this supervisor started for wlan0 (by its pid file; a pid that now belongs to another program is left alone)."""
        path = self._pidfile(program)
        try:
            with open(path, encoding='ascii') as f:
                pid = int(f.read().strip())
            with open(os.path.join(self.proc, str(pid), 'comm'), encoding='utf-8', errors='replace') as f:
                if f.read().strip() == program:
                    self._kill(pid, signal.SIGTERM)
        except (OSError, ValueError):
            pass
        try:
            os.unlink(path)
        except OSError:
            pass

    # -- client mode: wpa_supplicant joins the saved networks, udhcpc asks for the address
    def client_down(self):
        self.run(['wpa_cli', '-i', self.iface, 'terminate'], 5)
        self._stop('wpa_supplicant')
        self._stop('udhcpc')
        self.run(['ip', 'addr', 'flush', 'dev', self.iface], 5)

    def client_up(self):
        try:
            self.ensure_client_config()
        except OSError as exc:                                      # not root, a read-only disk: wpa_supplicant will say what is missing
            log('could not prepare the Wi-Fi configuration:', exc)
        self.client_down()
        try:
            os.makedirs(self.run_dir, mode=0o700, exist_ok=True)
        except OSError:
            pass
        self.run(['ip', 'link', 'set', self.iface, 'up'], 5)
        rc, out = self.run(['wpa_supplicant', '-B', '-i', self.iface, '-c', self.wpa_conf, '-P', self._pidfile('wpa_supplicant')], 20)
        if rc != 0:
            log('wpa_supplicant on %s failed: %s' % (self.iface, out.strip()[-200:]))
            return
        rc, out = self.run(['udhcpc', '-i', self.iface, '-b', '-S', '-p', self._pidfile('udhcpc')], 20)
        if rc != 0:
            log('udhcpc on %s failed: %s' % (self.iface, out.strip()[-200:]))

    def reload_wpa(self):
        self.run(['wpa_cli', '-i', self.iface, 'reconfigure'], 5)

    # -- access point
    def ap_start(self, ssid, passphrase, channel, country):
        os.makedirs(self.run_dir, mode=0o700, exist_ok=True)
        ctrl = os.path.join(self.run_dir, 'hostapd')
        hostapd_conf = os.path.join(self.run_dir, 'hostapd.conf')
        dnsmasq_conf = os.path.join(self.run_dir, 'dnsmasq.conf')
        wifilib.write_secret(hostapd_conf, wifilib.hostapd_conf(self.iface, ssid, passphrase, channel, country, ctrl))
        wifilib.write_secret(dnsmasq_conf, wifilib.dnsmasq_conf(self.iface, self.run_dir))
        self.client_down()
        self.run(['ip', 'addr', 'flush', 'dev', self.iface], 5)
        self.run(['ip', 'link', 'set', self.iface, 'up'], 5)
        hostapd = subprocess.Popen(['hostapd', hostapd_conf], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        self.procs.append(hostapd)
        for _ in range(20):                                         # up to 10 s for the radio to switch to AP mode
            time.sleep(0.5)
            if hostapd.poll() is not None:
                raise RuntimeError('hostapd stopped: ' + (hostapd.stdout.read() or '')[-300:])
            rc, out = self.run(['iw', 'dev', self.iface, 'info'], 5)
            if rc == 0 and re.search(r'type AP', out):
                break
        else:
            raise RuntimeError('the radio did not switch to access point mode')
        self.run(['ip', 'addr', 'add', wifilib.AP_ADDRESS + '/24', 'dev', self.iface], 5)
        dnsmasq = subprocess.Popen(['dnsmasq', '--conf-file=' + dnsmasq_conf, '--keep-in-foreground'], stdout=subprocess.DEVNULL,
                                   stderr=subprocess.PIPE, text=True)
        self.procs.append(dnsmasq)
        time.sleep(0.4)
        if dnsmasq.poll() is not None:
            raise RuntimeError('dnsmasq stopped: ' + (dnsmasq.stderr.read() or '')[-300:])

    def ap_stop(self):
        for p in reversed(self.procs):
            try:
                p.terminate()
                p.wait(timeout=4)
            except (OSError, subprocess.SubprocessError):
                try:
                    p.kill()
                except OSError:
                    pass
        self.procs = []
        self.run(['ip', 'addr', 'flush', 'dev', self.iface], 5)
        self.run(['ip', 'link', 'set', self.iface, 'down'], 5)
        for name in ('hostapd.conf', 'dnsmasq.conf'):                # they hold the access point password
            try:
                os.unlink(os.path.join(self.run_dir, name))
            except OSError:
                pass


class Portal:
    """Port 80 on the access point address: every request is sent to the setup page (phones then show their "sign in" window)."""

    def __init__(self, address, web_port, port=80):
        self.address, self.web_port, self.port, self.server, self.thread = address, web_port, port, None, None

    def start(self):
        target = 'http://%s:%d/' % (self.address, self.web_port)

        class Handler(http.server.BaseHTTPRequestHandler):
            def _redirect(self):
                self.send_response(302)
                self.send_header('Location', target)
                self.send_header('Cache-Control', 'no-store')
                self.send_header('Content-Length', '0')
                self.end_headers()

            do_GET = do_HEAD = do_POST = _redirect

            def log_message(self, *args):
                pass
        try:
            self.server = http.server.ThreadingHTTPServer((self.address, self.port), Handler)
            self.port = self.server.server_address[1]
        except OSError as exc:
            log('the sign-in redirect on port 80 is not available:', exc)
            return
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def stop(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            self.server = None


# ---------------------------------------------------------------- the state machine
class Supervisor:
    def __init__(self, system, cfg, run_dir=wifilib.RUN_DIR, wpa_conf=WPA_CONF, clock=time.monotonic, wall=time.time, sleep=time.sleep,
                 portal=None, install_id=None):
        self.sys, self.cfg, self.run_dir, self.wpa_conf = system, cfg, run_dir, wpa_conf
        self.clock, self.wall, self.sleep = clock, wall, sleep
        self.portal = portal
        self.installation_id = install_id or wifilib.installation_id()
        self.mode = 'client'
        self.offline_since = None
        self.idle_since = None
        self.ap_until = None
        self.ap_ssid = None
        self.scan_result, self.scanned_at = [], None
        self.last_result = None
        self.busy = None
        self.next_ap_attempt = 0.0
        self.ap_failures = 0
        self._last_status = 0.0

    # -- files
    def _read_wpa(self):
        try:
            with open(self.wpa_conf, encoding='utf-8') as f:
                return f.read()
        except OSError:
            return ''

    def _known(self):
        return wifilib.networks(self._read_wpa())

    def _write_wpa(self, text):
        wifilib.write_secret(self.wpa_conf, text, backup=self.wpa_conf + '.piano-bak')

    def publish(self, link=None):
        link = link if link is not None else (self.sys.link() if self.mode == 'client' else None)
        wifilib.write_status(self.run_dir, dict(
            pid=os.getpid(), mode=self.mode, busy=self.busy, link=link, cable=self.sys.has_cable_route(), hostname=self.sys.hostname(), ap=dict(ssid=self.ap_ssid) if self.mode == 'ap' else None,
            scan=self.scan_result, scan_error=getattr(self.sys, 'scan_error', None), scanned_at=self.scanned_at, last_result=self.last_result, updated=int(self.wall())))
        self._last_status = self.clock()

    def _result(self, ok, kind, ssid=None, error=None):
        self.last_result = dict(ok=ok, kind=kind, ssid=ssid, error=error, at=int(self.wall()))

    # -- modes
    def enter_ap(self, seconds=None):
        """Start the access point. The scan comes first: the radio cannot scan while it serves the access point."""
        self.busy = 'ap'
        self.publish(link=None)
        if self.mode == 'client' and self.sys.exists():
            self.scan_result, self.scanned_at = self.sys.scan(), int(self.wall())
        wpa = self._read_wpa()
        self.ap_ssid = wifilib.ap_ssid(self.cfg['ap_prefix'], self.installation_id)
        try:
            self.sys.ap_start(self.ap_ssid, self.cfg['ap_password'], self.cfg['channel'], wifilib.country(wpa))
        except Exception as exc:                                    # noqa: BLE001 - any failure must end with the normal network back
            log('the access point did not start:', exc)
            self.sys.ap_stop()
            self.sys.client_up()
            self.mode, self.busy = 'client', None
            self.ap_failures += 1
            self.next_ap_attempt = self.clock() + (60 if self.ap_failures <= 3 else 300)    # soon at first: it may be a passing problem
            self._result(False, 'ap', error='the access point did not start: %s' % str(exc)[-120:])
            self.publish()
            return False
        if self.portal:
            self.portal.start()
        self.mode, self.busy = 'ap', None
        self.ap_failures = 0
        self.idle_since = None
        self.ap_until = self.clock() + seconds if seconds else None
        log('access point %s is up' % self.ap_ssid)
        self.publish(link=None)
        return True

    def leave_ap(self):
        if self.portal:
            self.portal.stop()
        self.sys.ap_stop()
        self.mode = 'client'
        self.ap_until = None
        self.sys.client_up()

    def wait_online(self, ssid=None, timeout=45):
        """True when the Wi-Fi is associated (with `ssid` when given) and has an address and a route."""
        end = self.clock() + timeout
        while self.clock() < end:
            link = self.sys.link()
            if link and link.get('ip') and self.sys.has_default_route() and (ssid is None or link['ssid'] == ssid):
                return True
            self.sleep(2)
        return False

    # -- requests
    def handle(self, req):
        action = req.get('action')
        try:
            if action == 'connect':
                self.connect(req)
            elif action == 'forget':
                self.forget(req)
            elif action == 'ap':
                seconds = max(30, min(600, int(req.get('seconds', 120))))
                if self.mode != 'ap':
                    self.enter_ap(seconds)
                else:
                    self.ap_until = self.clock() + seconds
            else:
                log('unknown request ignored')
        except Exception as exc:                                    # noqa: BLE001 - a bad request must never stop the supervisor
            log('request failed:', exc)
            self.busy = None
            self._result(False, action or 'request', error='internal error')

    def connect(self, req):
        ssid = req.get('ssid')
        psk = req.get('psk')
        self.busy = 'connect'
        self.publish()
        before = self._read_wpa()
        try:
            self._write_wpa(wifilib.add_network(before, ssid, psk, bool(req.get('hidden'))))
        except ValueError as exc:
            self.busy = None
            self._result(False, 'connect', ssid, str(exc))
            self.publish()
            return
        log('connecting to %r' % ssid)
        was_ap = self.mode == 'ap'
        if was_ap:
            self.leave_ap()
        else:
            self.sys.client_up()
        if self.wait_online(ssid, self.cfg['connect_wait']):
            self.busy = None
            self._result(True, 'connect', ssid)
            log('connected to %r' % ssid)
            self.offline_since = None
            self.publish()
            return
        log('could not connect to %r, restoring the previous settings' % ssid)
        self._write_wpa(before)
        self.busy = None
        self._result(False, 'connect', ssid, 'wrong password, or the network is out of range')
        if was_ap:
            self.enter_ap()
        else:
            self.sys.client_up()
            self.publish()

    def only_way_in(self, ssid):
        """True when forgetting `ssid` would cut the connection in use with nothing else to fall back on."""
        link = self.sys.link() if self.mode == 'client' else None
        others = [n for n in self._known() if n['ssid'] != ssid]
        return bool(link and link.get('ssid') == ssid and not others and not self.sys.has_cable_route())

    def forget(self, req):
        ssid = req.get('ssid')
        if self.only_way_in(ssid):
            self._result(False, 'forget', ssid, 'this is the only way to reach the Pi: connect to another network or plug in a cable first')
            self.publish()
            return
        link = self.sys.link() if self.mode == 'client' else None
        self._write_wpa(wifilib.remove_network(self._read_wpa(), ssid))
        self._result(True, 'forget', ssid)
        if self.mode == 'client' and not self._known() and not self.sys.has_cable_route():
            self.enter_ap()                                         # nothing is left to connect to: no waiting, the setup network starts at once
        elif link and link.get('ssid') == ssid:
            self.sys.client_up()                                    # leave the network that was just forgotten
            self.publish()
        else:
            self.sys.reload_wpa()
            self.publish()

    # -- one round of the loop
    def step(self):
        wired = getattr(self.sys, 'ensure_wired', None)
        if wired:
            try:
                wired()                                              # a cable in a USB network adapter gets its address
            except Exception as exc:                                 # noqa: BLE001 - never at the cost of the Wi-Fi supervisor
                log('wired interface check failed:', exc)
        req = wifilib.take_request(self.run_dir)
        if req:
            self.handle(req)
        now = self.clock()
        online = self.sys.has_default_route()
        if self.mode == 'client':
            if online:
                self.offline_since = None
            else:
                self.offline_since = self.offline_since if self.offline_since is not None else now
                grace = self.cfg['grace_known'] if self._known() else self.cfg['grace_new']
                if now - self.offline_since >= grace and now >= self.next_ap_attempt and self.sys.exists():
                    self.enter_ap()
        else:
            if self.ap_until is not None:
                if now >= self.ap_until:                            # a test: back to the normal network
                    self.leave_ap()
                    self.wait_online(None, self.cfg['retry_wait'])
            elif online:                                            # a cable was plugged in: no need for the access point
                self.leave_ap()
            elif self._known():
                if self.sys.stations() == 0:
                    self.idle_since = self.idle_since if self.idle_since is not None else now
                    if now - self.idle_since >= self.cfg['retry_idle']:
                        self.try_known_again()
                else:
                    self.idle_since = None
        if self.clock() - self._last_status >= 5:
            self.publish()

    def try_known_again(self):
        """Nobody is using the access point and networks are saved: look for them again for a while."""
        log('no one is connected to the access point: trying the saved networks')
        self.leave_ap()
        if not self.wait_online(None, self.cfg['retry_wait']):
            self.enter_ap()
        else:
            self.publish()

    def shutdown(self):
        if self.mode == 'ap':
            self.leave_ap()


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    cmd = argv[0] if argv else 'run'
    run_dir = wifilib.RUN_DIR
    if cmd == 'status':
        print(json.dumps(wifilib.read_status(run_dir), indent=1, ensure_ascii=False))
        return 0
    if cmd == 'scan':
        for n in System().scan():
            print('%4d dBm  %-6s %s' % (n['signal'], 'secure' if n['secure'] else 'open', n['ssid']))
        return 0
    if cmd == 'ensure-name':
        system = System()
        current = system.hostname().lower()
        install_id = wifilib.installation_id()
        if current in wifilib.GENERIC_HOSTNAMES or current in [n.lower() for n in argv[1:]]:
            name = 'exxpiano-' + install_id.lower()
            wifilib.apply_hostname(name, system.run)
            print('The device is now called %s (it was %s)' % (name, current))
        else:
            print('The name %s is kept' % current)
        return 0
    if cmd == 'ap-test':
        seconds = int(argv[1]) if len(argv) > 1 else 120
        wifilib.post_request(run_dir, dict(action='ap', seconds=seconds))
        print('Requested: the access point starts within a few seconds and stops after %d s; the Pi then returns to its network by itself.' % seconds)
        return 0
    if cmd != 'run':
        print(__doc__)
        return 2
    cfg = config_from_env()
    system = System()
    if cfg['enabled'] and not system.managed_elsewhere():
        try:
            system.ensure_client_config()                           # a fresh card has no Wi-Fi set up: do it before the first scan or connection
        except Exception as exc:                                    # noqa: BLE001 - the supervisor must start anyway
            log('could not prepare the Wi-Fi configuration:', exc)
    if not cfg['enabled']:
        log('disabled (WIFI_SETUP=0)')
        return 0
    if system.managed_elsewhere():
        log('NetworkManager manages the network here: use nmtui; nothing to do')
        return 0
    if not shutil.which('hostapd') or not shutil.which('dnsmasq'):
        log('hostapd or dnsmasq is missing: the access point is not available (apt install hostapd dnsmasq-base)')
    sup = Supervisor(system, cfg, portal=Portal(wifilib.AP_ADDRESS, cfg['web_port']), install_id=wifilib.installation_id())
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *a: stop.set())
    log('started')
    while not stop.is_set():
        try:
            sup.step()
        except Exception as exc:                                    # noqa: BLE001 - the loop must survive anything
            log('step failed:', exc)
        stop.wait(TICK)
    sup.shutdown()
    return 0


if __name__ == '__main__':
    sys.exit(main())
