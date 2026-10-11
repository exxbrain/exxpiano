"""Wi-Fi helpers (standard library only): wpa_supplicant.conf editing, key derivation, `iw scan` parsing, hostapd / dnsmasq configuration.

Everything here is a pure function on text, so it is tested without a radio. Secrets: a password is turned into the 256-bit key (what
`wpa_passphrase` does) before it is stored; the password itself is never written anywhere by this module.
"""
import hashlib
import os
import re
import tempfile

SSID_MAX_BYTES = 32
CHANNELS_2G = (1, 6, 11)
AP_ADDRESS = '10.42.0.1'
AP_DEFAULT_PASSWORD = 'pianosetup'
AP_DEFAULT_PREFIX = 'Piano-Setup'
AP_NETWORK = '10.42.0.0/24'


# ---- validation and keys
def validate_ssid(ssid):
    """The network name as bytes: 1..32 bytes of UTF-8 without control characters."""
    if not isinstance(ssid, str) or not ssid:
        raise ValueError('the network name is empty')
    if any(ord(c) < 32 or ord(c) == 127 for c in ssid):
        raise ValueError('the network name has control characters')
    raw = ssid.encode('utf-8')
    if len(raw) > SSID_MAX_BYTES:
        raise ValueError('the network name is longer than 32 bytes')
    return raw


def validate_passphrase(passphrase):
    """8..63 printable ASCII characters (WPA2); None means an open network."""
    if passphrase is None or passphrase == '':
        return None
    if not isinstance(passphrase, str):
        raise ValueError('the password is not text')
    if not 8 <= len(passphrase) <= 63:
        raise ValueError('the password must be 8 to 63 characters')
    if any(not 32 <= ord(c) <= 126 for c in passphrase):
        raise ValueError('the password may only have ordinary ASCII characters')
    return passphrase


def derive_psk(ssid, passphrase):
    """Hex key as in `wpa_passphrase SSID`: PBKDF2-HMAC-SHA1, 4096 rounds, 32 bytes."""
    return hashlib.pbkdf2_hmac('sha1', validate_passphrase(passphrase).encode('ascii'), validate_ssid(ssid), 4096, 32).hex()


# ---- wpa_supplicant.conf
def _split(text):
    """[('line', str) | ('net', [lines])] in file order; a network block is the lines from `network={` to its `}`."""
    items, block = [], None
    for line in text.split('\n'):
        stripped = line.strip()
        if block is None and re.match(r'^network\s*=\s*\{\s*$', stripped):
            block = [line]
        elif block is not None:
            block.append(line)
            if stripped == '}':
                items.append(('net', block))
                block = None
        else:
            items.append(('line', line))
    if block is not None:                                   # an unterminated block: keep it as it was
        items.extend(('line', l) for l in block)
    return items


def _join(items):
    out = []
    for kind, value in items:
        out.extend(value if kind == 'net' else [value])
    return '\n'.join(out)


def _fields(block):
    fields = {}
    for line in block[1:-1]:
        m = re.match(r'^\s*([A-Za-z0-9_]+)\s*=\s*(.*?)\s*$', line)
        if m:
            fields[m.group(1)] = m.group(2)
    return fields


def _decode_ssid(value):
    if value.startswith('"') and value.endswith('"') and len(value) >= 2:
        return value[1:-1]
    try:
        return bytes.fromhex(value).decode('utf-8', 'replace')
    except ValueError:
        return value


def networks(text):
    """The saved networks: [{ssid, secure, hidden, priority}] in file order."""
    out = []
    for kind, value in _split(text):
        if kind != 'net':
            continue
        f = _fields(value)
        if 'ssid' not in f:
            continue
        try:
            priority = int(f.get('priority', '0'))
        except ValueError:
            priority = 0
        out.append(dict(ssid=_decode_ssid(f['ssid']), secure=f.get('key_mgmt', 'WPA-PSK') != 'NONE', hidden=f.get('scan_ssid') == '1',
                        priority=priority))
    return out


def country(text):
    m = re.search(r'^\s*country\s*=\s*([A-Za-z]{2})\s*$', text, re.M)
    return m.group(1).upper() if m else None


def add_network(text, ssid, psk=None, hidden=False):
    """The file with this network added as the most preferred one (an earlier entry with the same name is replaced)."""
    raw = validate_ssid(ssid)
    if psk is not None and not re.fullmatch(r'[0-9a-f]{64}', psk):
        raise ValueError('the key is not 64 hex digits')
    items = [it for it in _split(text) if not (it[0] == 'net' and _decode_ssid(_fields(it[1]).get('ssid', '')) == ssid)]
    top = max([n['priority'] for n in networks(_join(items))] or [0])
    block = ['network={', '\tssid=' + raw.hex(), '\t' + ('key_mgmt=WPA-PSK\n\tpsk=' + psk if psk else 'key_mgmt=NONE')]
    if hidden:
        block.append('\tscan_ssid=1')
    block += ['\tpriority=%d' % (top + 1), '}']
    block = '\n'.join(block).split('\n')
    while items and items[-1] == ('line', ''):              # keep the file tidy: one blank line is added by the final newline
        items.pop()
    items.append(('net', block))
    items.append(('line', ''))
    return _join(items)


def remove_network(text, ssid):
    items = [it for it in _split(text) if not (it[0] == 'net' and _decode_ssid(_fields(it[1]).get('ssid', '')) == ssid)]
    return _join(items)


def write_secret(path, text, backup=None):
    """Atomic write with mode 0600; the previous file is kept as `backup` when given."""
    directory = os.path.dirname(os.path.abspath(path))
    if backup and os.path.exists(path):
        with open(path, 'rb') as src:
            data = src.read()
        fd, tmp = tempfile.mkstemp(prefix='.bak.', dir=directory)
        with os.fdopen(fd, 'wb') as f:
            f.write(data)
        os.chmod(tmp, 0o600)
        os.replace(tmp, backup)
    fd, tmp = tempfile.mkstemp(prefix='.wifi.', dir=directory)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(text)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


# ---- scan results
def _unescape_iw(value):
    """`iw` prints unusual bytes in an SSID as \\xHH."""
    raw = re.sub(r'\\x([0-9a-fA-F]{2})', lambda m: chr(int(m.group(1), 16)), value)
    try:
        return raw.encode('latin-1').decode('utf-8')
    except (UnicodeEncodeError, UnicodeDecodeError):
        return raw


def parse_scan(text):
    """`iw dev wlan0 scan` -> [{ssid, signal (dBm), secure, freq}] strongest first, one entry per name, hidden networks left out."""
    best = {}
    for chunk in re.split(r'(?m)^BSS ', text or '')[1:]:
        ssid_m = re.search(r'(?m)^\s*SSID: ?(.*)$', chunk)
        if not ssid_m:
            continue
        ssid = _unescape_iw(ssid_m.group(1).rstrip())
        if not ssid or all(c == '\x00' for c in ssid):
            continue
        sig = re.search(r'(?m)^\s*signal: (-?\d+(?:\.\d+)?) dBm', chunk)
        freq = re.search(r'(?m)^\s*freq: (\d+)', chunk)
        secure = bool(re.search(r'(?m)^\s*(RSN|WPA):', chunk)) or bool(re.search(r'capability:.*\bPrivacy\b', chunk))
        entry = dict(ssid=ssid, signal=int(round(float(sig.group(1)))) if sig else -100, secure=secure, freq=int(freq.group(1)) if freq else None)
        if ssid not in best or entry['signal'] > best[ssid]['signal']:
            best[ssid] = entry
    return sorted(best.values(), key=lambda e: -e['signal'])


# ---- access point
def ap_ssid(prefix, mac):
    """`Piano-Setup-AB12`: the prefix and the last four hex digits of the adapter's address."""
    tail = re.sub(r'[^0-9A-Fa-f]', '', mac or '')[-4:].upper() or '0000'
    return ('%s-%s' % (prefix or 'Piano-Setup', tail))[:SSID_MAX_BYTES]


def setup_credentials(conf, mac):
    """(name, password) of the setup network from the configuration values (a dict) and the Wi-Fi address; bad values fall back to the defaults."""
    password = conf.get('WIFI_AP_PASSWORD') or AP_DEFAULT_PASSWORD
    try:
        validate_passphrase(password)
    except ValueError:
        password = AP_DEFAULT_PASSWORD
    return ap_ssid(conf.get('WIFI_AP_SSID') or AP_DEFAULT_PREFIX, mac), password


def hostapd_conf(interface, ssid, passphrase, channel=6, country_code=None, ctrl_dir=None):
    validate_ssid(ssid)
    if validate_passphrase(passphrase) is None:
        raise ValueError('the access point needs a password')
    if channel not in CHANNELS_2G:
        raise ValueError('channel must be 1, 6 or 11')
    lines = ['interface=' + interface, 'driver=nl80211', 'ssid=' + ssid, 'hw_mode=g', 'channel=%d' % channel, 'ieee80211n=1', 'wmm_enabled=1',
             'auth_algs=1', 'wpa=2', 'wpa_key_mgmt=WPA-PSK', 'rsn_pairwise=CCMP', 'wpa_passphrase=' + passphrase, 'max_num_sta=4',
             'macaddr_acl=0', 'ignore_broadcast_ssid=0']
    if country_code and re.fullmatch(r'[A-Z]{2}', country_code):
        lines += ['country_code=' + country_code, 'ieee80211d=1']
    if ctrl_dir:
        lines += ['ctrl_interface=' + ctrl_dir]
    return '\n'.join(lines) + '\n'


def dnsmasq_conf(interface, run_dir, address=AP_ADDRESS):
    """DHCP for the phone and a DNS that answers every name with the Pi (so the phone's sign-in check lands on the page)."""
    base = address.rsplit('.', 1)[0]
    return '\n'.join([
        'interface=' + interface, 'listen-address=' + address, 'bind-interfaces', 'no-resolv', 'no-hosts', 'no-poll',
        'user=nobody', 'group=nogroup',                       # dnsmasq-base does not create the user `dnsmasq`
        'dhcp-range=%s.10,%s.60,255.255.255.0,2h' % (base, base), 'dhcp-option=3,' + address, 'dhcp-option=6,' + address,
        'address=/#/' + address, 'pid-file=' + os.path.join(run_dir, 'dnsmasq.pid'), 'dhcp-leasefile=' + os.path.join(run_dir, 'dnsmasq.leases'), '']
    )


# ---- the exchange between the web page and the supervisor (files in /run/piano-wifi, root only)
RUN_DIR = '/run/piano-wifi'
MAX_JSON = 64 * 1024


def write_json(path, data):
    """Atomic, mode 0600 (the request files hold a network key)."""
    import json
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, mode=0o700, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix='.json.', dir=directory)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(data, f)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def read_json(path):
    import json
    try:
        if os.path.getsize(path) > MAX_JSON:
            return None
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def post_request(run_dir, request):
    """The page asks the supervisor for something; one request at a time (a second one replaces an unread first)."""
    import time
    request = dict(request, id=str(int(time.time() * 1000)))
    write_json(os.path.join(run_dir, 'request.json'), request)
    return request['id']


def take_request(run_dir):
    """The supervisor reads a request and removes the file; junk is removed too and gives None."""
    path = os.path.join(run_dir, 'request.json')
    data = read_json(path)
    try:
        os.unlink(path)
    except OSError:
        pass
    return data


def read_status(run_dir):
    return read_json(os.path.join(run_dir, 'status.json'))


def write_status(run_dir, status):
    write_json(os.path.join(run_dir, 'status.json'), status)
