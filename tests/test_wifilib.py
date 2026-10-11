"""Tests for pi/wifilib.py: wpa_supplicant.conf editing, key derivation, scan parsing, access point configuration."""
import os
import stat
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'pi'))
import wifilib  # noqa: E402

WPA = """# WiFi country code, set here in case the access point does send one
country=GB
# Grant all members of group "netdev" permissions to configure WiFi, e.g. via wpa_cli or wpa_gui
ctrl_interface=DIR=/run/wpa_supplicant GROUP=netdev
# Allow wpa_cli/wpa_gui to overwrite this config file
update_config=1
network={
\tssid="Home"
\tscan_ssid=1
\tkey_mgmt=WPA-PSK
\tpsk=0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef
}
"""
HEX = 'ab' * 32


class KeyTests(unittest.TestCase):
    def test_psk_matches_the_published_wpa_test_vectors(self):
        self.assertEqual(wifilib.derive_psk('IEEE', 'password'), 'f42c6fc52df0ebef9ebb4b90b38a5f902e83fe1b135a70e23aed762e9710a12e')
        self.assertEqual(wifilib.derive_psk('ThisIsASSID', 'ThisIsAPassword'), '0dc0d6eb90555ed6419756b9a15ec3e3209b63df707dd508d14581f8982721af')

    def test_ssid_rules(self):
        self.assertEqual(wifilib.validate_ssid('Дом'), 'Дом'.encode())
        self.assertEqual(len(wifilib.validate_ssid('x' * 32)), 32)
        for bad in ('', 'x' * 33, 'я' * 17, 'a\nb', 'a\x00b', None, 5):
            with self.subTest(bad), self.assertRaises(ValueError):
                wifilib.validate_ssid(bad)

    def test_passphrase_rules(self):
        self.assertIsNone(wifilib.validate_passphrase(None))
        self.assertIsNone(wifilib.validate_passphrase(''))
        self.assertEqual(wifilib.validate_passphrase('12345678'), '12345678')
        for bad in ('1234567', 'x' * 64, 'пароль-пароль', 'tab\there1', 5):
            with self.subTest(bad), self.assertRaises(ValueError):
                wifilib.validate_passphrase(bad)


class WpaConfTests(unittest.TestCase):
    def test_the_saved_networks_are_listed(self):
        self.assertEqual(wifilib.networks(WPA), [dict(ssid='Home', secure=True, hidden=True, priority=0)])
        self.assertEqual(wifilib.country(WPA), 'GB')
        self.assertEqual(wifilib.networks('country=RU\n'), [])
        self.assertIsNone(wifilib.country('update_config=1\n'))

    def test_a_new_network_becomes_the_most_preferred_and_the_rest_stays(self):
        text = wifilib.add_network(WPA, 'Дом 2', HEX)
        nets = wifilib.networks(text)
        self.assertEqual([n['ssid'] for n in nets], ['Home', 'Дом 2'])
        self.assertEqual([n['priority'] for n in nets], [0, 1])
        self.assertIn('ssid=' + 'Дом 2'.encode().hex(), text)             # written as hex: no quoting problems
        self.assertIn('psk=' + HEX, text)
        for kept in ('country=GB', 'ctrl_interface=DIR=/run/wpa_supplicant GROUP=netdev', 'update_config=1', '# Allow wpa_cli/wpa_gui'):
            self.assertIn(kept, text)
        self.assertIn('psk=0123456789abcdef', text)                         # the old network is untouched
        self.assertTrue(text.endswith('}\n'))

    def test_the_same_name_is_replaced_not_duplicated(self):
        once = wifilib.add_network(WPA, 'Home', HEX)
        twice = wifilib.add_network(once, 'Home', 'cd' * 32)
        nets = wifilib.networks(twice)
        self.assertEqual([n['ssid'] for n in nets], ['Home'])
        self.assertIn('cd' * 32, twice)
        self.assertNotIn(HEX, twice)
        self.assertNotIn('0123456789abcdef0123', twice)

    def test_open_and_hidden_networks(self):
        text = wifilib.add_network(WPA, 'Cafe')
        self.assertIn('key_mgmt=NONE', text)
        self.assertFalse([n for n in wifilib.networks(text) if n['ssid'] == 'Cafe'][0]['secure'])
        text = wifilib.add_network(WPA, 'Secret', HEX, hidden=True)
        self.assertTrue([n for n in wifilib.networks(text) if n['ssid'] == 'Secret'][0]['hidden'])

    def test_a_network_can_be_forgotten_and_forgetting_the_unknown_is_harmless(self):
        text = wifilib.add_network(WPA, 'Other', HEX)
        self.assertEqual([n['ssid'] for n in wifilib.networks(wifilib.remove_network(text, 'Home'))], ['Other'])
        self.assertEqual(wifilib.remove_network(WPA, 'Nobody'), WPA)
        self.assertIn('country=GB', wifilib.remove_network(WPA, 'Home'))
        self.assertEqual(wifilib.networks(wifilib.remove_network(WPA, 'Home')), [])

    def test_a_bad_key_or_name_is_refused_before_anything_is_written(self):
        for kwargs in (dict(ssid='', psk=HEX), dict(ssid='x', psk='short'), dict(ssid='x', psk='zz' * 32), dict(ssid='x' * 40, psk=HEX)):
            with self.subTest(kwargs), self.assertRaises(ValueError):
                wifilib.add_network(WPA, **kwargs)

    def test_odd_files_do_not_break_the_parser(self):
        self.assertEqual(wifilib.networks(''), [])
        broken = WPA.replace('}\n', '')                                       # unterminated block
        self.assertEqual(wifilib.add_network(broken, 'New', HEX).count('network={'), 2)
        weird = 'network={\n\tssid=4e6574\n\tkey_mgmt=NONE\n\tpriority=x\n}\nnetwork={\n\tkey_mgmt=NONE\n}\n'
        self.assertEqual(wifilib.networks(weird), [dict(ssid='Net', secure=False, hidden=False, priority=0)])
        text = '\r\n'.join(WPA.split('\n'))
        self.assertEqual([n['ssid'] for n in wifilib.networks(text)], ['Home'])

    def test_write_secret_is_private_atomic_and_keeps_a_backup(self):
        with tempfile.TemporaryDirectory() as d:
            path, backup = os.path.join(d, 'wpa.conf'), os.path.join(d, 'wpa.conf.bak')
            wifilib.write_secret(path, 'one\n')
            wifilib.write_secret(path, 'two\n', backup=backup)
            with open(path) as f, open(backup) as b:
                self.assertEqual((f.read(), b.read()), ('two\n', 'one\n'))
            for p in (path, backup):
                self.assertEqual(stat.S_IMODE(os.stat(p).st_mode), 0o600)
            self.assertEqual(sorted(os.listdir(d)), ['wpa.conf', 'wpa.conf.bak'])


SCAN = """BSS 11:22:33:44:55:66(on wlan0) -- associated
\tfreq: 2437
\tcapability: ESS Privacy ShortSlotTime (0x0411)
\tsignal: -48.00 dBm
\tSSID: Home
\tRSN:\t * Version: 1
BSS 11:22:33:44:55:77(on wlan0)
\tfreq: 5180
\tcapability: ESS Privacy (0x0411)
\tsignal: -71.00 dBm
\tSSID: Home
\tRSN:\t * Version: 1
BSS aa:bb:cc:dd:ee:01(on wlan0)
\tfreq: 2412
\tcapability: ESS (0x0401)
\tsignal: -60.50 dBm
\tSSID: Cafe Guest
BSS aa:bb:cc:dd:ee:02(on wlan0)
\tfreq: 2412
\tcapability: ESS Privacy (0x0411)
\tsignal: -80.00 dBm
\tSSID: \\x00\\x00\\x00
BSS aa:bb:cc:dd:ee:03(on wlan0)
\tfreq: 2412
\tsignal: -85.00 dBm
\tSSID:
BSS aa:bb:cc:dd:ee:04(on wlan0)
\tfreq: 2462
\tcapability: ESS Privacy (0x0411)
\tsignal: -66.00 dBm
\tSSID: \\xd0\\x94\\xd0\\xbe\\xd0\\xbc
\tWPA:\t * Version: 1
"""


class ScanTests(unittest.TestCase):
    def test_networks_are_merged_sorted_and_described(self):
        scan = wifilib.parse_scan(SCAN)
        self.assertEqual([e['ssid'] for e in scan], ['Home', 'Cafe Guest', 'Дом'])
        home = scan[0]
        self.assertEqual((home['signal'], home['secure'], home['freq']), (-48, True, 2437))            # the stronger of the two
        self.assertEqual((scan[1]['signal'], scan[1]['secure']), (-60 if round(-60.5) == -60 else -61, False))
        self.assertTrue(scan[2]['secure'])

    def test_nothing_and_junk(self):
        self.assertEqual(wifilib.parse_scan(''), [])
        self.assertEqual(wifilib.parse_scan(None), [])
        self.assertEqual(wifilib.parse_scan('command failed: Resource busy (-16)\n'), [])


class HostnameTests(unittest.TestCase):
    def test_the_number_and_the_suggested_name_come_from_the_wifi_address(self):
        self.assertEqual(wifilib.device_number('b8:27:eb:d2:81:de'), '81DE')
        self.assertEqual(wifilib.suggested_hostname('b8:27:eb:d2:81:de'), 'exxpiano-81de')
        self.assertEqual(wifilib.device_number(''), '0000')
        self.assertEqual(wifilib.ap_ssid('exxpiano-Setup', 'b8:27:eb:d2:81:de'), 'exxpiano-Setup-81DE')   # the same number in the setup network
        self.assertTrue(wifilib.HOSTNAME_RE.match(wifilib.suggested_hostname('b8:27:eb:d2:81:de')))

    def test_a_number_given_at_build_time_wins_over_the_address(self):
        self.assertEqual(wifilib.device_number('b8:27:eb:d2:81:de', '0007'), '0007')
        self.assertEqual(wifilib.device_number('b8:27:eb:d2:81:de', 'ab12'), 'AB12')
        self.assertEqual(wifilib.suggested_hostname('b8:27:eb:d2:81:de', '0007'), 'exxpiano-0007')
        self.assertEqual(wifilib.ap_ssid('exxpiano-Setup', '', '0007'), 'exxpiano-Setup-0007')                 # no address needed
        for bad in ('', 'x y', '12345678901', '../1', None):                                          # an unusable value falls back to the address
            self.assertEqual(wifilib.device_number('b8:27:eb:d2:81:de', bad), '81DE')
        self.assertEqual(wifilib.setup_credentials({'DEVICE_NUMBER': '0007'}, 'b8:27:eb:d2:81:de'), ('exxpiano-Setup-0007', 'exxpianosetup'))

    def test_apply_hostname_sets_it_fixes_the_hosts_file_and_restarts_avahi(self):
        calls = []
        with tempfile.TemporaryDirectory() as d:
            hosts = os.path.join(d, 'hosts')
            with open(hosts, 'w') as f:
                f.write('127.0.0.1\tlocalhost\n127.0.1.1\told-name\n::1 localhost ip6-localhost\n')
            self.assertEqual(wifilib.apply_hostname('exxpiano-81de', lambda cmd: (calls.append(cmd), (0, ''))[1], hosts), 'exxpiano-81de')
            with open(hosts) as f:
                text = f.read()
        self.assertEqual(text, '127.0.0.1\tlocalhost\n127.0.1.1\texxpiano-81de\n::1 localhost ip6-localhost\n')
        self.assertEqual(calls, [['hostnamectl', 'set-hostname', 'exxpiano-81de'], ['systemctl', 'try-restart', 'avahi-daemon.service']])

    def test_bad_names_and_failures(self):
        for bad in ('', '-a', 'a-', 'A', 'a b', 'a_b', 'x' * 64, None):
            with self.subTest(bad), self.assertRaises(ValueError):
                wifilib.apply_hostname(bad, lambda cmd: (0, ''))
        with self.assertRaises(OSError):
            wifilib.apply_hostname('ok', lambda cmd: (1, 'Access denied'), '/nonexistent/hosts')
        self.assertEqual(wifilib.apply_hostname('ok', lambda cmd: (0, ''), '/nonexistent/hosts'), 'ok')    # a missing hosts file is not fatal


class AccessPointTests(unittest.TestCase):
    def test_the_name_ends_with_the_adapter_address(self):
        self.assertEqual(wifilib.ap_ssid('exxpiano-Setup', 'ba:27:eb:d2:81:de'), 'exxpiano-Setup-81DE')
        self.assertEqual(wifilib.ap_ssid('', ''), 'exxpiano-Setup-0000')
        self.assertLessEqual(len(wifilib.ap_ssid('x' * 60, 'aa:bb')), 32)

    def test_hostapd_configuration(self):
        text = wifilib.hostapd_conf('wlan0', 'exxpiano-Setup-81DE', 'exxpianosetup', 6, 'RU', '/run/exxpiano-wifi/hostapd')
        for line in ('interface=wlan0', 'ssid=exxpiano-Setup-81DE', 'wpa=2', 'wpa_key_mgmt=WPA-PSK', 'rsn_pairwise=CCMP', 'wpa_passphrase=exxpianosetup',
                     'channel=6', 'country_code=RU', 'ctrl_interface=/run/exxpiano-wifi/hostapd'):
            self.assertIn(line + '\n', text)
        self.assertNotIn('country_code', wifilib.hostapd_conf('wlan0', 'x', 'exxpianosetup', 1, 'xx'))
        for bad in (dict(passphrase='short'), dict(passphrase=None), dict(passphrase='exxpianosetup', channel=14), dict(ssid='')):
            kw = dict(interface='wlan0', ssid='x', passphrase='exxpianosetup', channel=6)
            kw.update(bad)
            with self.subTest(bad), self.assertRaises(ValueError):
                wifilib.hostapd_conf(**kw)

    def test_dnsmasq_answers_every_name_with_the_pi_and_stays_on_its_interface(self):
        text = wifilib.dnsmasq_conf('wlan0', '/run/exxpiano-wifi')
        for line in ('interface=wlan0', 'listen-address=10.42.0.1', 'bind-interfaces', 'address=/#/10.42.0.1', 'dhcp-range=10.42.0.10,10.42.0.60,255.255.255.0,2h',
                     'dhcp-option=3,10.42.0.1', 'dhcp-option=6,10.42.0.1', 'no-resolv', 'user=nobody', 'group=nogroup', 'pid-file=/run/exxpiano-wifi/dnsmasq.pid'):
            self.assertIn(line + '\n', text)


if __name__ == '__main__':
    unittest.main()
