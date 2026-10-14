"""Tests for pi/exxpiano-wifi.py: the supervisor's state machine on a fake system (no radio, no root), the system wrapper with a fake command runner."""
import contextlib
import importlib.util
import io
import os
import re
import sys
import tempfile
import unittest
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
PI = os.path.join(HERE, '..', 'pi')
sys.path.insert(0, PI)
import wifilib  # noqa: E402
spec = importlib.util.spec_from_file_location('piano_wifi', os.path.join(PI, 'exxpiano-wifi.py'))
pw = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pw)

WPA_EMPTY = 'country=GB\nctrl_interface=DIR=/run/wpa_supplicant GROUP=netdev\nupdate_config=1\n'
HOME_PSK = 'aa' * 32
OTHER_PSK = 'bb' * 32


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


class FakeSystem:
    """A radio that can reach some networks (ssid -> psk) and an Ethernet cable that may or may not be plugged in."""

    def __init__(self, wpa_path, clock):
        self.wpa_path, self.clock = wpa_path, clock
        self.reachable = {}
        self.cable = False
        self.calls = []
        self.state = 'down'                    # down | client | ap
        self.connected = None                  # ssid when the Wi-Fi is up
        self.visible = [dict(ssid='Home', signal=-50, secure=True, freq=2437), dict(ssid='Cafe', signal=-70, secure=False, freq=2412)]
        self.station_count = 0
        self.ap_args = None
        self.ap_fails = False
        self.exists_ = True
        self.nm = False

    def read_wpa(self):
        with open(self.wpa_path) as f:
            return f.read()

    # facts
    def managed_elsewhere(self):
        return self.nm

    def exists(self):
        return self.exists_

    def mac(self):
        return 'ba:27:eb:d2:81:de'

    def hostname(self):
        return 'exxpiano'

    def has_default_route(self):
        return self.cable or (self.state == 'client' and self.connected is not None)

    def link(self):
        if self.state == 'client' and self.connected:
            return dict(ssid=self.connected, ip='192.168.1.50', signal=-55)
        return None

    def scan(self):
        self.calls.append('scan')
        return list(self.visible)

    def stations(self):
        return self.station_count

    # client
    def client_down(self):
        self.calls.append('client_down')
        self.state, self.connected = 'down', None

    def client_up(self):
        self.calls.append('client_up')
        self.state = 'client'
        nets = sorted(wifilib.networks(self.read_wpa()), key=lambda n: -n['priority'])
        text = self.read_wpa()
        self.connected = None
        for n in nets:
            if n['ssid'] in self.reachable:
                psk = re.search(r'ssid=%s\n\tkey_mgmt=WPA-PSK\n\tpsk=([0-9a-f]+)' % n['ssid'].encode().hex(), text)
                if (psk and psk.group(1) == self.reachable[n['ssid']]) or (not psk and self.reachable[n['ssid']] is None):
                    self.connected = n['ssid']
                    break

    def has_cable_route(self):
        return self.cable

    def reload_wpa(self):
        self.calls.append('reload_wpa')

    # access point
    def ap_start(self, ssid, passphrase, channel, country):
        self.calls.append('ap_start')
        if self.ap_fails:
            raise RuntimeError('hostapd stopped')
        self.ap_args = (ssid, passphrase, channel, country)
        self.state, self.connected = 'ap', None

    def ap_stop(self):
        self.calls.append('ap_stop')
        if self.state == 'ap':
            self.state = 'down'


class FakePortal:
    def __init__(self):
        self.running = False

    def start(self):
        self.running = True

    def stop(self):
        self.running = False


class SupervisorCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.run_dir = os.path.join(self.tmp.name, 'run')
        self.wpa = os.path.join(self.tmp.name, 'wpa_supplicant.conf')
        self.write_wpa(WPA_EMPTY)
        self.clock = Clock()
        self.system = FakeSystem(self.wpa, self.clock)
        self.portal = FakePortal()
        self.cfg = pw.config_from_env({})
        self.sup = pw.Supervisor(self.system, self.cfg, self.run_dir, self.wpa, self.clock, lambda: 1_700_000_000 + self.clock.t, self.clock.sleep,
                                 self.portal, install_id='012ABC')

    def write_wpa(self, text):
        with open(self.wpa, 'w') as f:
            f.write(text)

    def with_home(self):
        self.write_wpa(wifilib.add_network(WPA_EMPTY, 'Home', HOME_PSK))
        self.system.reachable = {'Home': HOME_PSK}

    def advance(self, seconds, step=3.0):
        end = self.clock.t + seconds
        while self.clock.t < end:
            self.clock.t += step
            self.sup.step()

    def request(self, **kw):
        wifilib.post_request(self.run_dir, kw)

    def status(self):
        return wifilib.read_status(self.run_dir)


class WhenToStartTests(SupervisorCase):
    def test_a_working_network_never_starts_the_access_point(self):
        self.with_home()
        self.system.client_up()
        self.advance(600)
        self.assertEqual(self.sup.mode, 'client')
        self.assertNotIn('ap_start', self.system.calls)

    def test_with_no_saved_network_the_access_point_starts_after_the_short_grace(self):
        self.advance(6)
        self.assertEqual(self.sup.mode, 'client')
        self.advance(10)
        self.assertEqual(self.sup.mode, 'ap')                                          # 10 s of grace by default
        self.assertEqual(self.system.ap_args, ('exxpiano-Setup-012ABC', 'exxpianosetup', 6, 'GB'))
        self.assertTrue(self.portal.running)
        st = self.status()
        self.assertEqual((st['mode'], st['ap']['ssid']), ('ap', 'exxpiano-Setup-012ABC'))
        self.assertEqual([n['ssid'] for n in st['scan']], ['Home', 'Cafe'])             # scanned before the radio became an access point
        self.assertLess(self.system.calls.index('scan'), self.system.calls.index('ap_start'))

    def test_with_a_saved_network_the_pi_waits_much_longer_for_a_slow_router(self):
        self.with_home()
        self.system.reachable = {}                                                     # the router is still booting
        self.advance(100)
        self.assertEqual(self.sup.mode, 'client')
        self.advance(70)
        self.assertEqual(self.sup.mode, 'ap')

    def test_an_ethernet_cable_counts_as_a_network(self):
        self.system.cable = True
        self.advance(600)
        self.assertEqual(self.sup.mode, 'client')

    def test_no_wifi_adapter_no_access_point(self):
        self.system.exists_ = False
        self.advance(300)
        self.assertEqual(self.sup.mode, 'client')

    def test_a_failing_access_point_gives_the_normal_network_back_and_waits_before_trying_again(self):
        self.system.ap_fails = True
        self.advance(20)
        self.assertEqual(self.sup.mode, 'client')
        self.assertEqual(self.system.calls.count('ap_start'), 1)
        r = self.status()['last_result']
        self.assertFalse(r['ok'])
        self.assertIn('hostapd stopped', r['error'])                                    # the reason reaches the page
        self.assertIn('client_up', self.system.calls)
        self.advance(45)
        self.assertEqual(self.system.calls.count('ap_start'), 1)
        self.advance(40)
        self.assertEqual(self.system.calls.count('ap_start'), 2)                        # soon at first ...
        self.advance(700)
        self.assertLessEqual(self.system.calls.count('ap_start'), 6)                    # ... then not hammering a broken radio


class ConnectTests(SupervisorCase):
    def to_ap(self):
        self.advance(40)
        self.assertEqual(self.sup.mode, 'ap')

    def test_choosing_the_home_network_in_the_access_point_connects_and_switches_the_access_point_off(self):
        self.to_ap()
        self.system.reachable = {'Home': HOME_PSK}
        self.request(action='connect', ssid='Home', psk=HOME_PSK)
        self.advance(3)
        self.assertEqual(self.sup.mode, 'client')
        self.assertEqual(self.system.connected, 'Home')
        self.assertFalse(self.portal.running)
        self.assertEqual([n['ssid'] for n in wifilib.networks(self.system.read_wpa())], ['Home'])
        st = self.status()
        self.assertEqual((st['last_result']['ok'], st['last_result']['ssid'], st['busy']), (True, 'Home', None))
        self.assertEqual(st['link']['ssid'], 'Home')
        self.assertEqual(st['hostname'], 'exxpiano')

    def test_a_wrong_password_restores_the_file_and_brings_the_access_point_back(self):
        self.to_ap()
        before = self.system.read_wpa()
        self.system.reachable = {'Home': HOME_PSK}
        self.request(action='connect', ssid='Home', psk=OTHER_PSK)
        self.advance(3)
        self.assertEqual(self.sup.mode, 'ap')
        self.assertTrue(self.portal.running)
        self.assertEqual(self.system.read_wpa(), before)
        r = self.status()['last_result']
        self.assertEqual((r['ok'], r['ssid']), (False, 'Home'))
        self.assertIn('wrong password', r['error'])

    def test_a_network_added_while_connected_elsewhere_switches_to_it(self):
        self.with_home()
        self.system.reachable = {'Home': HOME_PSK, 'Studio': OTHER_PSK}
        self.system.client_up()
        self.request(action='connect', ssid='Studio', psk=OTHER_PSK)
        self.advance(3)
        self.assertEqual(self.system.connected, 'Studio')                              # the new one is preferred
        self.assertEqual(sorted(n['ssid'] for n in wifilib.networks(self.system.read_wpa())), ['Home', 'Studio'])

    def test_a_failed_new_network_leaves_the_old_one_connected_and_the_file_unchanged(self):
        self.with_home()
        self.system.client_up()
        before = self.system.read_wpa()
        self.request(action='connect', ssid='Nowhere', psk=OTHER_PSK)
        self.advance(3)
        self.assertEqual(self.system.read_wpa(), before)
        self.assertEqual(self.system.connected, 'Home')
        self.assertEqual(self.sup.mode, 'client')
        self.assertFalse(self.status()['last_result']['ok'])

    def test_an_open_network_and_a_hidden_one(self):
        self.to_ap()
        self.system.reachable = {'Cafe': None}
        self.request(action='connect', ssid='Cafe')
        self.advance(3)
        self.assertEqual(self.system.connected, 'Cafe')
        self.assertIn('key_mgmt=NONE', self.system.read_wpa())

    def test_a_bad_request_changes_nothing(self):
        self.to_ap()
        before = self.system.read_wpa()
        for req in (dict(action='connect', ssid='', psk=HOME_PSK), dict(action='connect', ssid='x', psk='short'), dict(action='explode'),
                    dict(action='connect', ssid=5, psk=HOME_PSK), dict(action='connect')):
            with self.subTest(req):
                self.request(**req)
                self.advance(3)
                self.assertEqual(self.system.read_wpa(), before)
                self.assertEqual(self.sup.mode, 'ap')

    def test_the_only_way_in_cannot_be_forgotten_without_a_cable_or_another_network(self):
        self.with_home()
        self.system.client_up()
        before = self.system.read_wpa()
        self.request(action='forget', ssid='Home')
        self.advance(3)
        self.assertEqual(self.system.read_wpa(), before)
        self.assertEqual(self.system.connected, 'Home')
        r = self.status()['last_result']
        self.assertEqual((r['ok'], r['kind']), (False, 'forget'))
        self.assertIn('only way', r['error'])
        self.system.cable = True                                                           # with a cable it is safe
        self.request(action='forget', ssid='Home')
        self.advance(3)
        self.assertEqual(wifilib.networks(self.system.read_wpa()), [])

    def test_with_a_cable_forgetting_the_last_network_needs_no_setup_network(self):
        self.with_home()
        self.system.client_up()
        self.system.cable = True
        self.request(action='forget', ssid='Home')
        self.advance(3)
        self.assertEqual(wifilib.networks(self.system.read_wpa()), [])
        self.assertEqual(self.sup.mode, 'client')
        self.assertNotIn('ap_start', self.system.calls)

    def test_with_no_cable_and_nothing_left_the_setup_network_starts_at_once(self):
        self.write_wpa(wifilib.add_network(WPA_EMPTY, 'Gone', OTHER_PSK))                 # saved, but not in reach: no link
        self.request(action='forget', ssid='Gone')
        self.sup.step()                                                                    # the very step that handles the request
        self.assertEqual(self.sup.mode, 'ap')
        self.assertTrue(self.portal.running)
        self.assertEqual(wifilib.networks(self.system.read_wpa()), [])
        self.assertEqual(self.status()['last_result']['kind'], 'forget')

    def test_forgetting_a_network(self):
        self.with_home()
        self.system.client_up()
        self.write_wpa(wifilib.add_network(self.system.read_wpa(), 'Old', OTHER_PSK))
        self.request(action='forget', ssid='Old')
        self.advance(3)
        self.assertEqual([n['ssid'] for n in wifilib.networks(self.system.read_wpa())], ['Home'])
        self.assertIn('reload_wpa', self.system.calls)
        self.assertEqual(self.system.connected, 'Home')
        calls = len(self.system.calls)
        self.write_wpa(wifilib.add_network(self.system.read_wpa(), 'Spare', OTHER_PSK))   # another network exists: the one in use may go
        self.request(action='forget', ssid='Home')
        self.advance(3)
        self.assertEqual([n['ssid'] for n in wifilib.networks(self.system.read_wpa())], ['Spare'])
        self.assertIn('client_up', self.system.calls[calls:])
        self.assertIsNone(self.system.connected)

    def test_the_secret_never_reaches_the_status_file_or_the_log(self):
        self.to_ap()
        self.system.reachable = {'Home': HOME_PSK}
        self.request(action='connect', ssid='Home', psk=HOME_PSK)
        self.advance(3)
        with open(os.path.join(self.run_dir, 'status.json')) as f:
            self.assertNotIn(HOME_PSK, f.read())
        self.assertFalse(os.path.exists(os.path.join(self.run_dir, 'request.json')))     # consumed


class AccessPointLifeTests(SupervisorCase):
    def test_without_saved_networks_it_stays_for_ever_even_with_nobody_there(self):
        self.advance(40)
        self.advance(3600)
        self.assertEqual(self.sup.mode, 'ap')
        self.assertEqual(self.system.calls.count('ap_start'), 1)

    def test_with_saved_networks_an_unused_access_point_looks_for_them_again(self):
        self.with_home()
        self.system.reachable = {}
        self.advance(160)
        self.assertEqual(self.sup.mode, 'ap')
        self.system.reachable = {'Home': HOME_PSK}                                       # the router is back
        self.advance(130)
        self.assertEqual(self.sup.mode, 'client')
        self.assertEqual(self.system.connected, 'Home')
        self.assertFalse(self.portal.running)

    def test_if_they_are_still_not_there_the_access_point_returns(self):
        self.with_home()
        self.system.reachable = {}
        self.advance(160)
        starts = self.system.calls.count('ap_start')
        self.advance(200)
        self.assertEqual(self.sup.mode, 'ap')
        self.assertGreater(self.system.calls.count('ap_start'), starts)

    def test_a_phone_on_the_access_point_is_never_thrown_out(self):
        self.with_home()
        self.system.reachable = {}
        self.advance(160)
        self.system.station_count = 1
        self.advance(1200)
        self.assertEqual(self.sup.mode, 'ap')
        self.assertEqual(self.system.calls.count('ap_start'), 1)

    def test_plugging_in_a_cable_closes_the_access_point(self):
        self.advance(40)
        self.system.cable = True
        self.advance(6)
        self.assertEqual(self.sup.mode, 'client')
        self.assertFalse(self.portal.running)

    def test_the_test_mode_runs_for_the_requested_time_and_returns(self):
        self.with_home()
        self.system.client_up()
        self.request(action='ap', seconds=60)
        self.advance(3)
        self.assertEqual(self.sup.mode, 'ap')
        self.system.cable = True                                                         # a test is not cut short by a cable
        self.advance(40)
        self.assertEqual(self.sup.mode, 'ap')
        self.system.cable = False
        self.advance(40)
        self.assertEqual(self.sup.mode, 'client')
        self.assertEqual(self.system.connected, 'Home')

    def test_the_test_time_is_bounded(self):
        self.request(action='ap', seconds=10 ** 6)
        self.advance(3)
        self.assertLessEqual(self.sup.ap_until - self.clock.t, 600)
        self.request(action='ap', seconds=1)
        self.advance(3)
        self.assertGreaterEqual(self.sup.ap_until - self.clock.t, 20)

    def test_shutdown_while_serving_gives_the_radio_back(self):
        self.advance(40)
        self.sup.shutdown()
        self.assertEqual(self.sup.mode, 'client')
        self.assertFalse(self.portal.running)
        self.assertIn('client_up', self.system.calls)


class PortalTests(unittest.TestCase):
    def test_every_request_is_sent_to_the_setup_page(self):
        portal = pw.Portal('127.0.0.1', 8080, port=0)
        portal.start()
        self.addCleanup(portal.stop)

        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *a, **kw):
                return None
        opener = urllib.request.build_opener(NoRedirect)
        for path in ('/', '/generate_204', '/hotspot-detect.html', '/anything/at/all'):
            with self.subTest(path):
                try:
                    opener.open('http://127.0.0.1:%d%s' % (portal.port, path), timeout=3)
                    self.fail('expected a redirect')
                except urllib.error.HTTPError as e:
                    self.assertEqual((e.code, e.headers['Location']), (302, 'http://127.0.0.1:8080/'))


class SystemTests(unittest.TestCase):
    def make(self, answers):
        calls = []

        def run(cmd, timeout):
            calls.append(cmd)
            for key, value in answers.items():
                if key in ' '.join(cmd):
                    return value
            return (1, '')
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.net = os.path.join(self.tmp.name, 'network')
        self.wpa = os.path.join(self.tmp.name, 'wpa_supplicant.conf')
        self.dietpi = os.path.join(self.tmp.name, 'dietpi.txt')
        s = pw.System('wlan0', '/tmp/none', self.wpa, run, net_dir=self.net, dietpi_txt=(self.dietpi,))
        return s, calls

    def test_the_link_comes_from_the_supplicant(self):
        s, _ = self.make({'wpa_cli': (0, 'bssid=aa\nssid=Home\nip_address=192.168.1.95\nwpa_state=COMPLETED\n'), 'iw dev': (0, 'Connected to aa\n\tsignal: -52 dBm\n')})
        self.assertEqual(s.link(), dict(ssid='Home', ip='192.168.1.95', signal=-52))
        s, _ = self.make({'wpa_cli': (0, 'wpa_state=SCANNING\n')})
        self.assertIsNone(s.link())
        s, _ = self.make({'wpa_cli': (127, 'no such file')})
        self.assertIsNone(s.link())

    def test_default_route_stations_and_network_manager(self):
        s, _ = self.make({'ip -4 route': (0, 'default via 192.168.1.1 dev wlan0\n')})
        self.assertTrue(s.has_default_route())
        s, _ = self.make({'ip -4 route': (0, '')})
        self.assertFalse(s.has_default_route())
        s, _ = self.make({'station dump': (0, 'Station aa:bb (on wlan0)\n\tinactive time: 0 ms\nStation cc:dd (on wlan0)\n')})
        self.assertEqual(s.stations(), 2)
        s, _ = self.make({'is-active': (0, 'active\n')})
        self.assertTrue(s.managed_elsewhere())
        s, _ = self.make({'is-active': (3, 'inactive\n')})
        self.assertFalse(s.managed_elsewhere())

    def wired(self, answers=None):
        """A System with a fake /sys/class/net: -> (system, calls, helper that makes an interface with carrier 0 or 1)."""
        s, calls = self.make(dict({'dhclient': (0, ''), 'addr show': (0, '')}, **(answers or {})))
        s.sys_net = os.path.join(self.tmp.name, 'sys_net')
        os.makedirs(s.sys_net)

        def nic(name, carrier, physical=True):
            os.makedirs(os.path.join(s.sys_net, name, 'device' if physical else 'virtual'), exist_ok=True)
            with open(os.path.join(s.sys_net, name, 'carrier'), 'w') as f:
                f.write('%d\n' % carrier)
        return s, calls, nic

    def test_a_usb_ethernet_adapter_with_a_cable_gets_an_address_once_per_cable(self):
        s, calls, nic = self.wired()
        self.write(os.path.join(self.net, 'interfaces'), 'source interfaces.d/*\n')
        self.write(os.path.join(self.net, 'interfaces.d', 'eth0'), 'allow-hotplug eth0\niface eth0 inet dhcp\n')       # the board's own port: ifupdown does it
        nic('eth0', 1)
        nic('eth1', 0)                                                                       # the adapter, no cable yet
        nic('lo', 1, physical=False)
        nic('wlan0', 1)
        s.ensure_wired()
        self.assertFalse([c for c in calls if c[0] == 'dhclient'])                           # nothing to do without a cable
        self.assertIn(['ip', 'link', 'set', 'eth1', 'up'], calls)
        nic('eth1', 1)                                                                       # the cable is plugged in
        s.ensure_wired()
        s.ensure_wired()
        dh = [c for c in calls if c[0] == 'dhclient']
        self.assertEqual(len(dh), 1)
        self.assertEqual((dh[0][:3], dh[0][-1]), (['dhclient', '-1', '-nw'], 'eth1'))
        self.assertNotIn('eth0', ' '.join(' '.join(c) for c in dh))                          # an interface with a stanza is left alone
        nic('eth1', 0)
        s.ensure_wired()                                                                     # unplugged
        nic('eth1', 1)
        s.ensure_wired()                                                                     # plugged again: a new request
        self.assertEqual(len([c for c in calls if c[0] == 'dhclient']), 2)

    def test_an_interface_that_already_has_an_address_is_not_touched(self):
        s, calls, nic = self.wired({'addr show': (0, '3: eth1    inet 192.168.1.40/24 brd 192.168.1.255 scope global eth1\n')})
        nic('eth1', 1)
        s.ensure_wired()
        self.assertFalse([c for c in calls if c[0] == 'dhclient'])

    def test_a_missing_dhclient_or_sysfs_does_not_break_anything(self):
        s, calls, nic = self.wired({'dhclient': (127, 'no such file')})
        nic('eth1', 1)
        s.ensure_wired()
        s.sys_net = os.path.join(self.tmp.name, 'nowhere')
        s.ensure_wired()

    def write(self, path, text):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w') as f:
            f.write(text)

    def test_a_fresh_card_gets_what_ifup_needs_and_the_country_of_the_image(self):
        s, calls = self.make({'ifup': (0, '')})
        self.write(self.dietpi, 'AUTO_SETUP_NET_WIFI_COUNTRY_CODE=RU\nAUTO_SETUP_NET_WIFI_ENABLED=0\n')
        self.write(os.path.join(self.net, 'interfaces'), 'auto lo\niface lo inet loopback\n')       # no wlan0 and no source line: "unknown interface wlan0"
        s.client_up()
        with open(self.wpa) as f:
            conf = f.read().splitlines()
        self.assertEqual(conf[:2], ['ctrl_interface=DIR=/run/wpa_supplicant GROUP=netdev', 'country=RU'])
        stanza = os.path.join(self.net, 'interfaces.d', 'exxpiano-wlan0')
        with open(stanza) as f:
            text = f.read()
        self.assertIn('iface wlan0 inet dhcp', text)
        self.assertIn('wpa-conf ' + self.wpa, text)
        self.assertIn(['rfkill', 'unblock', 'wifi'], calls)
        self.assertIn(['iw', 'reg', 'set', 'RU'], calls)
        self.assertIn(['ifup', '-i', stanza, 'wlan0'], calls)                          # the main file does not include interfaces.d: the stanza is read directly
        self.assertEqual(os.stat(self.wpa).st_mode & 0o777, 0o600)

    def test_what_dietpi_already_set_up_is_left_alone(self):
        s, calls = self.make({'ifup': (0, '')})
        self.write(os.path.join(self.net, 'interfaces'), 'source interfaces.d/*\n')
        self.write(os.path.join(self.net, 'interfaces.d', 'wlan0'), 'allow-hotplug wlan0\niface wlan0 inet dhcp\n\twpa-conf /etc/wpa_supplicant/wpa_supplicant.conf\n')
        own = 'ctrl_interface=DIR=/run/wpa_supplicant GROUP=netdev\ncountry=GB\nnetwork={\n\tssid="x"\n}\n'
        self.write(self.wpa, own)
        s.client_up()
        with open(self.wpa) as f:
            self.assertEqual(f.read(), own)
        self.assertFalse(os.path.exists(os.path.join(self.net, 'interfaces.d', 'exxpiano-wlan0')))
        self.assertIn(['ifup', 'wlan0'], calls)

    def test_saved_networks_survive_when_only_the_header_is_added(self):
        s, calls = self.make({'ifup': (0, '')})
        self.write(os.path.join(self.net, 'interfaces'), 'source interfaces.d/*\n')
        self.write(self.wpa, 'network={\n\tssid="Home"\n\tpsk="secret123"\n}\n')
        self.write(self.dietpi, 'AUTO_SETUP_NET_WIFI_COUNTRY_CODE=GB\n')
        s.client_up()
        with open(self.wpa) as f:
            conf = f.read()
        self.assertTrue(conf.startswith('ctrl_interface=DIR=/run/wpa_supplicant GROUP=netdev\ncountry=GB\n'))
        self.assertIn('psk="secret123"', conf)

    def test_a_card_that_cannot_be_prepared_still_tries_ifup(self):
        s, calls = self.make({'ifup': (1, 'ifup: unknown interface wlan0')})
        self.write(os.path.join(self.tmp.name, 'blocker'), 'a file where a folder is needed')
        s.net_dir = os.path.join(self.tmp.name, 'blocker')                              # the stanza cannot be written here
        s.client_up()
        self.assertIn(['ifup', 'wlan0'], calls)

    def test_client_mode_goes_through_ifupdown_only(self):
        s, calls = self.make({'ifup': (0, '')})
        self.write(os.path.join(self.net, 'interfaces'), 'source interfaces.d/*\n')                # a card set up by DietPi
        self.write(os.path.join(self.net, 'interfaces.d', 'wlan0'), 'allow-hotplug wlan0\niface wlan0 inet dhcp\n')
        s.client_up()
        s.client_down()
        names = [c[0] for c in calls]
        self.assertEqual(set(names), {'ifdown', 'ifup', 'wpa_cli', 'rfkill'})
        self.assertIn(['ifup', 'wlan0'], calls)
        self.assertIn(['ifdown', '--force', 'wlan0'], calls)

    def test_scan_retries_when_the_radio_is_busy(self):
        results = iter([(1, 'command failed: Resource busy (-16)'), (0, 'BSS aa:bb:cc:dd:ee:ff(on wlan0)\n\tsignal: -40.0 dBm\n\tSSID: Home\n\tRSN:\t * x\n')])
        s = pw.System('wlan0', '/tmp/none', '/tmp/none.conf', lambda cmd, timeout: next(results))
        real = pw.time.sleep
        pw.time.sleep = lambda x: None
        self.addCleanup(setattr, pw.time, 'sleep', real)
        self.assertEqual([n['ssid'] for n in s.scan()], ['Home'])


    def test_scan_falls_back_to_wpa_cli_and_explains_a_failure(self):
        calls = []

        def run(cmd, timeout):
            calls.append(cmd[0])
            if cmd[0] == 'iw':
                return 1, 'command failed: Device or resource busy (-16)'
            if cmd[:4] == ['wpa_cli', '-i', 'wlan0', 'scan']:
                return 0, 'OK'
            return 0, 'bssid / frequency / signal level / flags / ssid\naa:bb:cc:dd:ee:ff\t2437\t-45\t[WPA2-PSK-CCMP][ESS]\tHome\n11:22:33:44:55:66\t2412\t-70\t[ESS]\tCafe\n'
        s = pw.System('wlan0', '/tmp/none', '/tmp/none.conf', run)
        real = pw.time.sleep
        pw.time.sleep = lambda x: None
        self.addCleanup(setattr, pw.time, 'sleep', real)
        wl_sleep = pw.wifilib.time.sleep
        pw.wifilib.time.sleep = lambda x: None
        self.addCleanup(setattr, pw.wifilib.time, 'sleep', wl_sleep)
        self.assertEqual([(n['ssid'], n['secure']) for n in s.scan()], [('Home', True), ('Cafe', False)])
        self.assertIsNone(s.scan_error)
        self.assertEqual(calls.count('iw'), 3)
        dead = pw.System('wlan0', '/tmp/none', '/tmp/none.conf', lambda cmd, timeout: (127, 'no such file'))
        self.assertEqual(dead.scan(), [])
        self.assertEqual(dead.scan_error, 'the iw program is not installed')

    def test_a_scan_on_an_interface_that_is_down_brings_it_up_and_tries_again(self):
        calls = []
        state = {'up': False}

        def run(cmd, timeout):
            calls.append(cmd[:3])
            if cmd[:3] == ['ip', 'link', 'set']:
                state['up'] = True
                return 0, ''
            if cmd[0] == 'iw':
                if state['up']:
                    return 0, 'BSS aa:bb:cc:dd:ee:ff(on wlan0)\n\tsignal: -40.0 dBm\n\tSSID: Home\n\tRSN:\t * x\n'
                return 240, 'command failed: Network is down (-100)'
            return 1, ''
        found, error = pw.wifilib.scan_networks(run, 'wlan0', sleep=lambda x: None)
        self.assertEqual(([n['ssid'] for n in found], error), (['Home'], None))
        self.assertIn(['ip', 'link', 'set'], calls)
        stuck = lambda cmd, timeout: (240, 'command failed: Network is down (-100)') if cmd[0] == 'iw' else (1, 'RF-kill')   # noqa: E731
        found, error = pw.wifilib.scan_networks(stuck, 'wlan0', sleep=lambda x: None)
        self.assertIsNone(found)
        self.assertIn('interface is down', error)

    def test_failure_reasons_are_short_and_readable(self):
        for rc, out, word in ((1, 'command failed: Operation not possible due to RF-kill (-132)', 'rfkill'),
                              (1, 'command failed: Device or resource busy (-16)', 'busy'),
                              (1, 'command failed: Operation not permitted (-1)', 'root'),
                              (1, 'command failed: No such device (-19)', 'adapter'),
                              (1, 'weird', 'weird')):
            self.assertIn(word, pw.wifilib.scan_failure_reason(rc, out))
        self.assertEqual(pw.wifilib.parse_wpa_scan('junk\n'), [])


class ConfigTests(unittest.TestCase):
    def test_defaults(self):
        c = pw.config_from_env({})
        self.assertEqual((c['ap_prefix'], c['ap_password'], c['channel'], c['grace_new'], c['grace_known'], c['enabled']),
                         ('exxpiano-Setup', 'exxpianosetup', 6, 10, 150, True))

    def test_values_are_read_clamped_and_checked(self):
        c = pw.config_from_env({'WIFI_AP_SSID': 'My-Piano', 'WIFI_AP_PASSWORD': 'much-better-secret', 'WIFI_AP_CHANNEL': '11', 'WIFI_GRACE_NEW': '1',
                                'WIFI_GRACE_KNOWN': 'abc', 'WIFI_SETUP': '0', 'WEB_PORT': '9090'})
        self.assertEqual((c['ap_prefix'], c['ap_password'], c['channel'], c['grace_new'], c['grace_known'], c['enabled'], c['web_port']),
                         ('My-Piano', 'much-better-secret', 11, 5, 150, False, 9090))
        self.assertEqual(pw.config_from_env({'WIFI_AP_PASSWORD': 'short'})['ap_password'], 'exxpianosetup')    # an unusable password is not used
        self.assertEqual(pw.config_from_env({'WIFI_AP_CHANNEL': '3'})['channel'], 6)                       # only 1, 6, 11


class EnsureNameTests(unittest.TestCase):
    def run_cli(self, hostname, mac='b8:27:eb:d2:81:de'):
        calls = []

        class FakeSystem(pw.System):
            def __init__(self):
                super().__init__('wlan0', '/tmp/none', '/tmp/none.conf', lambda cmd, timeout=15: (calls.append(cmd), (0, ''))[1])

            def hostname(self):
                return hostname

            def mac(self):
                return mac
        real = pw.System
        real_installation_id = pw.wifilib.installation_id
        pw.System = FakeSystem
        pw.wifilib.installation_id = lambda: '012ABC'
        try:
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = pw.main(['ensure-name'])
        finally:
            pw.System = real
            pw.wifilib.installation_id = real_installation_id
        return code, out.getvalue(), calls

    def test_a_generic_name_gets_the_unique_one(self):
        for generic in ('DietPi', 'raspberrypi', 'piano', 'localhost'):
            code, out, calls = self.run_cli(generic)
            self.assertEqual(code, 0)
            self.assertIn(['hostnamectl', 'set-hostname', 'exxpiano-012abc'], calls)
            self.assertIn('exxpiano-012abc', out)

    def test_the_installation_id_names_the_device_even_without_an_adapter(self):
        code, out, calls = self.run_cli('dietpi', mac='')
        self.assertEqual(code, 0)
        self.assertIn(['hostnamectl', 'set-hostname', 'exxpiano-012abc'], calls)

    def test_a_chosen_name_is_kept(self):
        for chosen in ('exxpiano', 'kitchen-piano', 'exxpiano-81de'):
            code, out, calls = self.run_cli(chosen)
            self.assertEqual(calls, [])
            self.assertIn('kept', out)

class ExchangeTests(unittest.TestCase):
    def test_requests_are_private_one_shot_and_junk_is_dropped(self):
        with tempfile.TemporaryDirectory() as d:
            run_dir = os.path.join(d, 'run')
            self.assertIsNone(wifilib.take_request(run_dir))
            rid = wifilib.post_request(run_dir, dict(action='connect', ssid='x', psk='ab' * 32))
            self.assertEqual(oct(os.stat(os.path.join(run_dir, 'request.json')).st_mode & 0o777), oct(0o600))
            self.assertEqual(oct(os.stat(run_dir).st_mode & 0o777), oct(0o700))
            req = wifilib.take_request(run_dir)
            self.assertEqual((req['action'], req['id']), ('connect', rid))
            self.assertIsNone(wifilib.take_request(run_dir))                      # taken: gone
            with open(os.path.join(run_dir, 'request.json'), 'w') as f:
                f.write('not json')
            self.assertIsNone(wifilib.take_request(run_dir))
            self.assertFalse(os.path.exists(os.path.join(run_dir, 'request.json')))
            with open(os.path.join(run_dir, 'status.json'), 'w') as f:
                f.write('[1, 2]')
            self.assertIsNone(wifilib.read_status(run_dir))


if __name__ == '__main__':
    unittest.main()
