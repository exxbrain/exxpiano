#!/usr/bin/env python3
"""Makes the SD card image of one piano module: DietPi + the software + the banks + the number and the name of the device. One zip per device.

  python3 scripts/build-sd-image.py --dietpi DietPi_RPi234-ARMv8-Trixie.img.xz --piano build/archive/Salamander-stereo-6L-9s-v3.sf2 --number auto \\
      --password 'root password' --ap-password 'sticker password' --note 'for Ivan'

Result in dist/sd/<number>/:  piano-synth-<number>-sd.zip  (flash it with Raspberry Pi Imager "Use custom" or balenaEtcher: both take the zip as it is),
a sticker (the QR codes of this very device, ready to print) and a README with what the device is called. The device is entered in the register
(registry/devices.csv, see sd-registry.py): the number is unique, `--number auto` takes the next free one.

Needs Docker (on a Mac the image is made in a Linux container; Docker Desktop must be running) or a Linux machine with root and --no-docker.
The first start of the card needs the internet ONCE (DietPi's own first-run setup downloads its updates); nothing of ours does.
HONEST STATUS: the whole build is tested on a stand-in image (same layout) and the finished image is read back and its programs run; it has not been
started on a Raspberry Pi: do that with the first card and look at /var/log/piano-synth-firstboot.log.
"""
import argparse
import csv
import datetime
import hashlib
import importlib.util
import os
import shutil
import subprocess
import sys
import urllib.request
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'pi'))
import deviceid  # noqa: E402
import wifilib  # noqa: E402

DIETPI_URL = 'https://dietpi.com/downloads/images/DietPi_RPi234-ARMv8-Trixie.img.xz'
BUILDER_IMAGE = 'piano-sd-builder'


def _load(name):
    spec = importlib.util.spec_from_file_location(name.replace('-', '_'), os.path.join(ROOT, 'scripts', name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


registry = _load('sd-registry')


class BuildError(Exception):
    pass


def sha256_of(path, block=1 << 20):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(block), b''):
            h.update(chunk)
    return h.hexdigest()


def read_version():
    with open(os.path.join(ROOT, 'VERSION')) as f:
        return f.read().strip()


def download(url, dest, fetch=None):
    """Fetch the official DietPi image (only when asked for with --download); the sha256 file next to it is checked when it exists."""
    fetch = fetch or (lambda u: urllib.request.urlopen(u, timeout=60))
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    part = dest + '.part'
    h = hashlib.sha256()
    with fetch(url) as r, open(part, 'wb') as f:
        for chunk in iter(lambda: r.read(1 << 20), b''):
            f.write(chunk)
            h.update(chunk)
    expected = None
    try:
        with fetch(url + '.sha256') as r:
            expected = r.read().decode().split()[0].lower()
    except Exception:                                     # noqa: BLE001 - no checksum published or not reachable
        print('build-sd-image: no checksum file found next to the image: not verified')
    if expected and expected != h.hexdigest():
        os.unlink(part)
        raise BuildError('the downloaded image does not match its sha256 (%s != %s)' % (h.hexdigest(), expected))
    os.replace(part, dest)
    return dest


def resolve_inputs(args):
    """Check everything before the long part starts. Returns a dict of absolute paths."""
    inputs = {}
    if args.dietpi:
        inputs['dietpi'] = os.path.abspath(args.dietpi)
    elif args.download:
        inputs['dietpi'] = os.path.join(ROOT, 'build', 'cache', os.path.basename(DIETPI_URL))
    else:
        raise BuildError('give the DietPi image: --dietpi DietPi_RPi234-ARMv8-Trixie.img.xz (from dietpi.com), or --download to fetch it')
    if not args.download or args.dietpi:
        if not os.path.isfile(inputs['dietpi']):
            raise BuildError('no such DietPi image: %s' % inputs['dietpi'])
    if not args.no_banks:
        if not args.piano:
            found = sorted(f for f in os.listdir(os.path.join(ROOT, 'build', 'archive')) if f.endswith('.sf2')) if os.path.isdir(os.path.join(ROOT, 'build', 'archive')) else []
            raise BuildError('give the piano bank: --piano FILE.sf2 (or --no-banks). In build/archive: %s' % (', '.join(found) or 'nothing'))
        inputs['piano'] = os.path.abspath(args.piano)
        mech = args.mech or os.path.join(ROOT, 'build', 'archive', 'Salamander-mech.sf2')
        inputs['mech'] = os.path.abspath(mech)
        for key in ('piano', 'mech'):
            if not os.path.isfile(inputs[key]):
                raise BuildError('no such bank file: %s' % inputs[key])
    return inputs


def read_serials(path):
    """A list of boards to make cards for: CSV with the columns serial (required), number, name, note. Blank lines and lines starting with # are skipped."""
    with open(path, newline='', encoding='utf-8') as f:
        lines = [l for l in f if l.strip() and not l.lstrip().startswith('#')]
    rows = list(csv.DictReader(lines))
    if not rows or 'serial' not in (rows[0] or {}):
        raise BuildError('%s: the first line must name the columns, for example: serial,number,name,note' % path)
    jobs, seen = [], set()
    for n, row in enumerate(rows, 2):
        try:
            serial = deviceid.normalize_serial(row.get('serial'))
        except ValueError as exc:
            raise BuildError('%s, line %d: %s' % (path, n, exc))
        if serial[-8:] in seen:
            raise BuildError('%s, line %d: the serial %s is listed twice' % (path, n, serial))
        seen.add(serial[-8:])
        jobs.append(dict(serial=serial, number=(row.get('number') or '').strip() or None, name=(row.get('name') or '').strip() or None, note=(row.get('note') or '').strip()))
    return jobs


def choose_number(args, job=None, taken=()):
    path = args.registry
    spec = (job or {}).get('number') or args.number
    if str(spec).lower() == 'serial':
        if not (job or {}).get('serial'):
            raise BuildError('--number serial needs --serial')
        spec = job['serial'][-4:].upper()
    if str(spec).lower() == 'auto':
        used = [int(r['number']) for r in registry.load(path) if r['number'].isdigit()] + [int(t) for t in taken if str(t).isdigit()]
        return str(max(used) + 1 if used else 1).zfill(4)
    number = registry.normalize(spec)
    if number in taken:
        raise BuildError('the number %s is used twice in this run' % number)
    existing = registry.get(number, path)
    if existing and not args.force:
        raise BuildError('the number %s is already registered (version %s, built %s, note: %s). Take another, `--number auto`, or --force to replace it'
                         % (number, existing['version'], existing['built_at'], existing['note'] or '-'))
    return number


def build_deb(version, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    subprocess.run([sys.executable, os.path.join(ROOT, 'scripts', 'build-deb.py'), '--version', version, '--out', out_dir], check=True, stdout=subprocess.DEVNULL)
    return os.path.join(out_dir, 'piano-synth_%s_all.deb' % version)


def docker_ok():
    if not shutil.which('docker'):
        raise BuildError('Docker is not installed: install Docker Desktop (or use a Linux machine with --no-docker as root)')
    if subprocess.run(['docker', 'info'], capture_output=True).returncode != 0:
        raise BuildError('Docker is not running: start Docker Desktop and try again')
    if subprocess.run(['docker', 'image', 'inspect', BUILDER_IMAGE], capture_output=True).returncode != 0:
        print('build-sd-image: making the builder image (once)')
        subprocess.run(['docker', 'build', '-q', '-t', BUILDER_IMAGE, '-f', os.path.join(ROOT, 'scripts', 'sd-builder.Dockerfile'), os.path.join(ROOT, 'scripts')], check=True)


def inner_args(plan, path_of):
    """The arguments of sd-image-inner.sh; `path_of(key)` gives the path of an input as the script sees it."""
    a = ['--image', path_of('dietpi'), '--out', plan['out_img_in_builder'], '--deb', path_of('deb'), '--number', plan['number'], '--hostname', plan['hostname'],
         '--country', plan['country'], '--timezone', plan['timezone'], '--locale', plan['locale'], '--keyboard', plan['keyboard'], '--workdir', '/tmp/piano-sd-work']
    if plan.get('serial'):
        a += ['--serial', plan['serial']] + (['--lock'] if plan.get('lock') else [])
    if plan['no_banks']:
        a.append('--no-banks')
    else:
        a += ['--piano', path_of('piano'), '--mech', path_of('mech')]
    return a                                    # the passwords go through the environment (SD_PASSWORD, SD_AP_PASSWORD), not through the arguments


def docker_command(plan, inputs, script_args, smoke=False):
    """`docker run` with the repository and every input folder mounted read-only (so nothing of yours can be changed) and the output folder writable."""
    mounts, mapping = [], {}
    for key, path in inputs.items():
        folder = os.path.dirname(path)
        slot = '/in/%d' % len(mounts)
        mounts += ['-v', '%s:%s:ro' % (folder, slot)]
        mapping[key] = '%s/%s' % (slot, os.path.basename(path))
    cmd = ['docker', 'run', '--rm', '--privileged', '-e', 'SD_PASSWORD', '-e', 'SD_AP_PASSWORD', '-v', '%s:/repo:ro' % ROOT, '-v', '%s:/out' % plan['out_dir']] + mounts + [BUILDER_IMAGE]
    if smoke:
        return cmd + ['bash', '/repo/scripts/sd-smoke-test.sh', plan['out_img_in_builder']]
    return cmd + ['bash', '/repo/scripts/sd-image-inner.sh'] + script_args(mapping)


def make_zip(img, zip_path):
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED, compresslevel=3, allowZip64=True) as z:
        z.write(img, os.path.basename(img))


def readme(plan, ssid, zip_name):
    return '''Piano module № {number}  ({hostname})
==========================================
Built: version {version}, {when}
Image: {zip_name}
Banks: {banks}
{board}
Writing the card
  1. Raspberry Pi Imager: "Choose OS" -> "Use custom" -> pick the zip (it is used as it is). Or balenaEtcher: "Flash from file" -> the zip.
  2. Put the card in the Raspberry Pi 3 and switch it on. The first start takes several minutes (DietPi sets itself up, then the card grows to its size).
     DietPi's first run needs the internet ONCE: plug in an Ethernet cable for the first start.
  3. After that the device is called {hostname}: http://{hostname}.local:8080/ (or the address in the router's client list).

Without a network the device starts its own Wi-Fi: {ssid}  (password: {ap_pw}).
Scan the QR codes of the sticker: the first joins that network, the second opens the settings page (the page warns if the code belongs to another device).

If something is wrong: the log of the first start is /var/log/piano-synth-firstboot.log; DietPi's own log is /var/tmp/dietpi/logs/.
'''.format(number=plan['number'], hostname=plan['hostname'], version=plan['version'], when=plan['when'], zip_name=zip_name,
           banks=plan['banks_text'], ssid=ssid, ap_pw=plan['ap_password'] or wifilib.AP_DEFAULT_PASSWORD,
           board=('Board: Raspberry Pi serial %s (%s)\n' % (plan['serial'], 'the synthesizer refuses another board' if plan.get('lock') else 'another board gets a warning on the page')) if plan.get('serial') else '')


def parse_args(argv):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--dietpi', help='DietPi_RPi234-ARMv8-Trixie.img.xz (or an unpacked .img) from dietpi.com')
    ap.add_argument('--download', action='store_true', help='fetch the official DietPi image into build/cache (only when you ask for it)')
    ap.add_argument('--piano', help='the piano bank (.sf2)')
    ap.add_argument('--mech', help='the mechanics bank (.sf2); default build/archive/Salamander-mech.sf2')
    ap.add_argument('--no-banks', action='store_true', help='leave the banks out (install them later on the device)')
    ap.add_argument('--serial', help='the serial of the Raspberry Pi this card is for (cat /proc/cpuinfo: Serial). The card warns on any other board')
    ap.add_argument('--serials', metavar='FILE.csv', help='make a card for every board in the list (columns: serial, number, name, note)')
    ap.add_argument('--lock', action='store_true', help='with --serial/--serials: the synthesizer does not start on another board')
    ap.add_argument('--number', default='auto', help='the number of the device: 7 / 0007 / AB12, auto (the next free one), or serial (the last 4 digits of the serial)')
    ap.add_argument('--name', help='the host name (default piano-<number>)')
    ap.add_argument('--note', default='', help='a note for the register (a customer, a date)')
    ap.add_argument('--force', action='store_true', help='replace a number or a serial that is already in the register')
    ap.add_argument('--password', default='', help="DietPi's global password (root and dietpi users); without it the DietPi default stays: change it")
    ap.add_argument('--ap-password', default='', help='the password of the setup network (default pianosetup)')
    ap.add_argument('--country', default='GB', help='Wi-Fi country code (RU, GB ...)')
    ap.add_argument('--timezone', default='UTC')
    ap.add_argument('--locale', default='en_GB.UTF-8')
    ap.add_argument('--keyboard', default='us')
    ap.add_argument('--out', default=os.path.join(ROOT, 'dist', 'sd'), help='output folder (a folder per number is made inside)')
    ap.add_argument('--registry', default=registry.DEFAULT_PATH)
    ap.add_argument('--keep-image', action='store_true', help='keep the raw .img next to the zip')
    ap.add_argument('--no-smoke', action='store_true', help="skip running the finished image's programs in a container")
    ap.add_argument('--no-docker', action='store_true', help='run on this Linux machine as root instead of in Docker')
    ap.add_argument('--dry-run', action='store_true', help='check everything and print the plan, make nothing')
    return ap.parse_args(argv)


def plan_jobs(args, inputs):
    """Every card of this run, fully checked before the first one is made."""
    if args.serials and args.serial:
        raise BuildError('use --serial for one board or --serials for a list, not both')
    if args.lock and not (args.serial or args.serials):
        raise BuildError('--lock needs --serial or --serials: a card can only be locked to a board it knows')
    if args.password and len(args.password) < 8:
        raise BuildError('--password: at least 8 characters')
    if args.ap_password:
        wifilib.validate_passphrase(args.ap_password)
    if args.serials:
        jobs = read_serials(args.serials)
        for job in jobs:
            job['note'] = job['note'] or args.note
        if args.name:
            raise BuildError('--name is for one card: give the name in the list instead')
    else:
        jobs = [dict(serial=deviceid.normalize_serial(args.serial) if args.serial else None, number=None, name=args.name, note=args.note)]
    plans, taken = [], []
    version = read_version()
    banks = [] if args.no_banks else [os.path.basename(inputs['piano']), os.path.basename(inputs['mech'])]
    for job in jobs:
        number = choose_number(args, job, taken)
        taken.append(number)
        if job['serial'] and not args.force:
            existing = registry.find_serial(job['serial'], args.registry)
            if existing:
                raise BuildError('the board %s already has a card in the register: number %s (%s, built %s). --force makes a new one' %
                                 (job['serial'], existing['number'], existing['version'], existing['built_at']))
        hostname = job['name'] or 'piano-' + number.lower()
        if not wifilib.HOSTNAME_RE.match(hostname):
            raise BuildError('the name %r: lowercase letters, digits and hyphens' % hostname)
        plans.append(dict(number=number, hostname=hostname, version=version, out_dir=os.path.abspath(os.path.join(args.out, number)), no_banks=args.no_banks,
                          password=args.password, ap_password=args.ap_password, country=args.country, timezone=args.timezone, locale=args.locale,
                          keyboard=args.keyboard, banks_text=', '.join(banks) or '(none: install later)', serial=job['serial'], lock=bool(args.lock and job['serial']),
                          note=job['note'], when=datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M UTC'),
                          out_img_in_builder='/out/piano-synth-%s.img' % number))
    return plans


def build_one(args, inputs, plan):
    number, out_dir = plan['number'], plan['out_dir']
    os.makedirs(out_dir, exist_ok=True)
    if args.no_docker:
        if os.name != 'posix' or sys.platform == 'darwin' or os.geteuid() != 0:
            raise BuildError('--no-docker needs Linux and root')
        cmd = ['bash', os.path.join(ROOT, 'scripts', 'sd-image-inner.sh')] + inner_args(dict(plan, out_img_in_builder=os.path.join(out_dir, 'piano-synth-%s.img' % number)), lambda k: inputs[k])
    else:
        docker_ok()
        cmd = docker_command(plan, inputs, lambda mapping: inner_args(plan, lambda k: mapping[k]))
    print('Making the image of № %s (a few minutes) ...' % number)
    env = dict(os.environ, SD_PASSWORD=args.password, SD_AP_PASSWORD=args.ap_password)
    if subprocess.run(cmd, env=env).returncode != 0:
        raise BuildError('the image could not be made (see the messages above)')
    img = os.path.join(out_dir, 'piano-synth-%s.img' % number)
    if not os.path.isfile(img):
        raise BuildError('the builder reported success but there is no image')
    if not args.no_smoke and not args.no_docker:
        print('Starting the programs of the finished image in a container ...')
        if subprocess.run(docker_command(plan, {}, None, smoke=True), env=env).returncode != 0:
            raise BuildError('the finished image fails its smoke test (see above); it is left in %s' % out_dir)
    zip_name = 'piano-synth-%s-sd.zip' % number
    zip_path = os.path.join(out_dir, zip_name)
    print('Packing %s ...' % zip_name)
    make_zip(img, zip_path)
    img_hash = sha256_of(img)
    if not args.keep_image:
        os.unlink(img)
    ssid = wifilib.ap_ssid('Piano-Setup', '', number)
    sticker_dir = os.path.join(out_dir, 'sticker')
    subprocess.run([sys.executable, os.path.join(ROOT, 'scripts', 'make-qr.py'), '--number', number, '--password', args.ap_password or wifilib.AP_DEFAULT_PASSWORD,
                    '--out', sticker_dir], check=True, stdout=subprocess.DEVNULL)
    with open(os.path.join(out_dir, 'README-%s.txt' % number), 'w', encoding='utf-8') as f:
        f.write(readme(plan, ssid, zip_name))
    record = registry.add(dict(number=number, hostname=plan['hostname'], ssid=ssid, version=plan['version'], image=zip_name, image_sha256=img_hash,
                               banks='' if plan['no_banks'] else plan['banks_text'], note=plan['note'], serial=plan['serial'] or '',
                               locked='yes' if plan['lock'] else ''), args.registry, replace=args.force)
    print('\nDone. Device № %s:' % record['number'])
    print('  card image : %s (%.0f MB)' % (zip_path, os.path.getsize(zip_path) / 1e6))
    print('  sticker    : %s' % sticker_dir)
    print('  register   : %s' % args.registry)
    return record


def main(argv=None):
    args = parse_args(argv)
    done = []
    try:
        inputs = resolve_inputs(args)
        plans = plan_jobs(args, inputs)
        for plan in plans:
            print('Device № %s (%s), software %s, setup network %s%s' % (plan['number'], plan['hostname'], plan['version'], wifilib.ap_ssid('Piano-Setup', '', plan['number']),
                                                                     ', board %s%s' % (plan['serial'], ' (locked)' if plan['lock'] else '') if plan['serial'] else ''))
        print('DietPi image: %s%s' % (inputs['dietpi'], ' (will be downloaded)' if args.download and not args.dietpi else ''))
        print('Banks: %s | %d card(s) -> %s' % (plans[0]['banks_text'], len(plans), os.path.abspath(args.out)))
        if args.dry_run:
            print('(dry run: nothing is made)')
            return 0
        if args.download and not args.dietpi:
            print('Downloading %s ...' % DIETPI_URL)
            download(DIETPI_URL, inputs['dietpi'])
        inputs['deb'] = build_deb(plans[0]['version'], os.environ.get('PIANO_SD_WORK') or os.path.join(ROOT, 'build', 'sd-work'))
        for plan in plans:
            done.append(build_one(args, inputs, plan)['number'])
        if len(plans) > 1:
            print('\nAll %d cards are made: %s' % (len(done), ', '.join(done)))
        return 0
    except (BuildError, registry.RegistryError, ValueError) as exc:
        print('build-sd-image:', exc, file=sys.stderr)
        if done:
            print('build-sd-image: made before the failure: %s (they are in the register)' % ', '.join(done), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
