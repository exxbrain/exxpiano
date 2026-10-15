#!/usr/bin/env python3
"""Makes the reusable exxpiano SD card image: DietPi + the software + the required banks.

  python3 scripts/build-sd-image.py --dietpi DietPi_RPi234-ARMv8-Trixie.img.xz

Result in dist/sd/: a versioned exxpiano-rpi234-...-sd.zip, a README and a manifest. Flash the same ZIP with Raspberry Pi Imager or balenaEtcher.
At first boot each installed copy creates a persistent random installation ID. The ID makes its setup Wi-Fi and hostname unique.

Needs Docker (on a Mac the image is made in a Linux container; Docker Desktop must be running) or a Linux machine with root and --no-docker.
The first start is offline: DietPi applies local hardware settings without its online update workflow, while exxpiano owns Wi-Fi setup.
HONEST STATUS: the whole build is tested on a stand-in image (same layout) and the finished image is read back and its programs run; it has not been
started on a Raspberry Pi: do that with the first card and look at /var/log/exxpiano-firstboot.log.
"""

import argparse
import datetime
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.request
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'pi'))
import wifilib  # noqa: E402

DIETPI_BASE = 'https://dietpi.com/downloads/images/'
# the official DietPi image of every target: its name, how people call the boards, and whether the board has the 3.5 mm jack (the Pi 5 has none)
TARGETS = {
    'rpi234': dict(image='DietPi_RPi234-ARMv8-Trixie.img.xz', label='Raspberry Pi 2/3/4 ARMv8', onboard_audio=True),
    'rpi5': dict(image='DietPi_RPi5-ARMv8-Trixie.img.xz', label='Raspberry Pi 5', onboard_audio=False),
}
HTTP_USER_AGENT = 'exxpiano-image-builder (+https://github.com/exxbrain/exxpiano)'
BUILDER_IMAGE = 'exxpiano-sd-builder'
DEFAULT_TARGET = 'rpi234'
VERSION_RE = re.compile(r'^[0-9A-Za-z][0-9A-Za-z._-]*$')


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
        version = f.read().strip()
    if not VERSION_RE.fullmatch(version):
        raise BuildError('VERSION is not safe for an artifact name: %r' % version)
    return version


def read_dietpi_version(path):
    try:
        with open(path, encoding='ascii') as f:
            version = f.read().strip()
    except OSError as exc:
        raise BuildError('the builder did not report the DietPi version: %s' % exc) from exc
    if not VERSION_RE.fullmatch(version):
        raise BuildError('the DietPi version is missing or not safe for an artifact name: %r' % version)
    return version


def target_of(args_or_plan):
    name = (args_or_plan.get('target') if isinstance(args_or_plan, dict) else getattr(args_or_plan, 'target', None)) or DEFAULT_TARGET
    return name, TARGETS[name]


def artifact_stem(plan, dietpi_version):
    return 'exxpiano-%s-v%s-dietpi-%s' % (plan['target'], plan['version'], dietpi_version)


def write_json(path, data):
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write('\n')
    os.replace(tmp, path)


def fetch_url(url):
    """Open a public build input while identifying this non-browser client."""
    request = urllib.request.Request(url, headers={'User-Agent': HTTP_USER_AGENT})
    return urllib.request.urlopen(request, timeout=60)


def download(url, dest, fetch=None):
    """Fetch the official DietPi image (only when asked for with --download); the sha256 file next to it is checked when it exists."""
    fetch = fetch or fetch_url
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
    except Exception:  # noqa: BLE001 - no checksum published or not reachable
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
        inputs['dietpi'] = os.path.join(ROOT, 'build', 'cache', target_of(args)[1]['image'])
    else:
        raise BuildError('give the DietPi image: --dietpi %s (from dietpi.com), or --download to fetch it' % target_of(args)[1]['image'])
    if (not args.download or args.dietpi) and not os.path.isfile(inputs['dietpi']):
        raise BuildError('no such DietPi image: %s' % inputs['dietpi'])
    inputs['piano'] = os.path.abspath(args.piano or os.path.join(ROOT, 'build', 'piano-full.sf2'))
    inputs['mech'] = os.path.abspath(args.mech or os.path.join(ROOT, 'build', 'mech.sf2'))
    lite = args.piano_lite or os.path.join(ROOT, 'build', 'piano-lite.sf2')  # for boards with 512 MB; optional unless asked for by name
    if target_of(args)[0] == 'rpi234' and (args.piano_lite or os.path.isfile(lite)):  # the Pi 5 has 2 GB or more: only the full bank
        inputs['piano_lite'] = os.path.abspath(lite)
    for key in [k for k in ('piano', 'mech', 'piano_lite') if k in inputs]:
        if not os.path.isfile(inputs[key]) or not os.access(inputs[key], os.R_OK):
            raise BuildError(
                'the required %s bank is missing or unreadable: %s (prepare defaults with scripts/make-soundfonts.sh or pass --%s FILE.sf2)'
                % (key, inputs[key], key)
            )
    return inputs


def build_deb(version, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    subprocess.run(
        [sys.executable, os.path.join(ROOT, 'scripts', 'build-deb.py'), '--version', version, '--out', out_dir], check=True, stdout=subprocess.DEVNULL
    )
    return os.path.join(out_dir, 'exxpiano_%s_all.deb' % version)


def docker_ok():
    if not shutil.which('docker'):
        raise BuildError('Docker is not installed: install Docker Desktop (or use a Linux machine with --no-docker as root)')
    if subprocess.run(['docker', 'info'], capture_output=True).returncode != 0:
        raise BuildError('Docker is not running: start Docker Desktop and try again')
    if subprocess.run(['docker', 'image', 'inspect', BUILDER_IMAGE], capture_output=True).returncode != 0:
        print('build-sd-image: making the builder image (once)')
        subprocess.run(
            ['docker', 'build', '-q', '-t', BUILDER_IMAGE, '-f', os.path.join(ROOT, 'scripts', 'sd-builder.Dockerfile'), os.path.join(ROOT, 'scripts')],
            check=True,
        )


def inner_args(plan, path_of):
    """The arguments of sd-image-inner.sh; `path_of(key)` gives the path of an input as the script sees it."""
    a = [
        '--image',
        path_of('dietpi'),
        '--out',
        plan['out_img_in_builder'],
        '--dietpi-version-out',
        plan['dietpi_version_out_in_builder'],
        '--deb',
        path_of('deb'),
        '--hostname',
        'exxpiano',
        '--country',
        plan['country'],
        '--timezone',
        plan['timezone'],
        '--locale',
        plan['locale'],
        '--keyboard',
        plan['keyboard'],
        '--workdir',
        '/tmp/piano-sd-work',
    ]
    a += ['--piano', path_of('piano'), '--mech', path_of('mech')]
    if plan.get('has_lite'):
        a += ['--piano-lite', path_of('piano_lite')]
    a += ['--onboard-audio', '1' if plan['onboard_audio'] else '0']
    return a  # the passwords go through the environment (SD_PASSWORD, SD_AP_PASSWORD), not through the arguments


def docker_command(plan, inputs, script_args, smoke=False):
    """`docker run` with the repository and every input folder mounted read-only (so nothing of yours can be changed) and the output folder writable."""
    mounts, mapping = [], {}
    for key, path in inputs.items():
        folder = os.path.dirname(path)
        slot = '/in/%d' % len(mounts)
        mounts += ['-v', '%s:%s:ro' % (folder, slot)]
        mapping[key] = '%s/%s' % (slot, os.path.basename(path))
    cmd = (
        [
            'docker',
            'run',
            '--rm',
            '--privileged',
            '-e',
            'SD_PASSWORD',
            '-e',
            'SD_AP_PASSWORD',
            '-e',
            'SD_ONBOARD_AUDIO',
            '-v',
            '%s:/repo:ro' % ROOT,
            '-v',
            '%s:/out' % plan['out_dir'],
        ]
        + mounts
        + [BUILDER_IMAGE]
    )
    if smoke:
        return cmd + ['bash', '/repo/scripts/sd-smoke-test.sh', plan['out_img_in_builder']]
    return cmd + ['bash', '/repo/scripts/sd-image-inner.sh'] + script_args(mapping)


def make_zip(img, zip_path, image_name):
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED, compresslevel=3, allowZip64=True) as z:
        z.write(img, image_name)


def readme(plan, zip_name):
    return '''exxpiano SD image
==================
Built: version {version}, {when}
Image: {zip_name}
DietPi: {dietpi_version} (source SHA-256: {dietpi_sha256})
Banks: {banks}
Writing the card
  1. Raspberry Pi Imager: "Choose OS" -> "Use custom" -> pick the zip (it is used as it is). Or balenaEtcher: "Flash from file" -> the zip.
  2. Put the card in a compatible {label} and switch it on. The first start takes several minutes (DietPi sets itself up, then the card grows to its size).
     The first start is offline: it needs neither Internet nor an Ethernet cable.
  3. The installation creates and permanently saves its own 6-character hexadecimal ID.

Without a saved network the device starts Wi-Fi exxpiano-Setup-<INSTALLATION-ID> (password: {ap_pw}).
Its hostname is exxpiano-<installation-id-in-lowercase>; open http://<hostname>.local:8080/ or use the address from the router's client list.
The exact installation ID, Wi-Fi name and QR code are shown by the device web page after the first start.

{audio_note}If something is wrong: the log of the first start is /var/log/exxpiano-firstboot.log; DietPi's own logs are in /var/lib/dietpi/logs/.
'''.format(
        version=plan['version'],
        when=plan['when'],
        zip_name=zip_name,
        dietpi_version=plan['dietpi_version'],
        dietpi_sha256=plan['dietpi_sha256'],
        banks=plan['banks_text'],
        ap_pw=plan['ap_password'] or wifilib.AP_DEFAULT_PASSWORD,
        label=plan['label'],
        audio_note=''
        if plan['onboard_audio']
        else 'This board has no 3.5 mm jack: connect a USB sound card or an audio interface, the piano plays through it.\n\n',
    )


def parse_args(argv):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument(
        '--target',
        choices=sorted(TARGETS),
        default=DEFAULT_TARGET,
        help='the boards the image is for: rpi234 (Raspberry Pi 2/3/4, the default) or rpi5 (Raspberry Pi 5: DietPi_RPi5-ARMv8-Trixie.img.xz, no 3.5 mm jack: needs a USB sound card)',
    )
    ap.add_argument('--dietpi', help='the DietPi image of the target (for rpi234 DietPi_RPi234-ARMv8-Trixie.img.xz; or an unpacked .img) from dietpi.com')
    ap.add_argument('--download', action='store_true', help='fetch the official DietPi image into build/cache (only when you ask for it)')
    ap.add_argument('--piano', help='the piano bank (.sf2); default build/piano-full.sf2')
    ap.add_argument(
        '--piano-lite',
        help='the smaller piano bank for boards with 512 MB (.sf2); default build/piano-lite.sf2 when it exists. The image picks it at the first start when the memory is under 800 MB',
    )
    ap.add_argument('--mech', help='the mechanics bank (.sf2); default build/mech.sf2')
    ap.add_argument(
        '--password',
        default=os.environ.get('SD_PASSWORD', ''),
        help="DietPi's global password (root and dietpi users); default: SD_PASSWORD; without either the DietPi default stays: change it",
    )
    ap.add_argument(
        '--ap-password', default=os.environ.get('SD_AP_PASSWORD', ''), help='the password of the setup network; default: SD_AP_PASSWORD, then exxpianosetup'
    )
    ap.add_argument('--country', default='GB', help='Wi-Fi country code (RU, GB ...)')
    ap.add_argument('--timezone', default='UTC')
    ap.add_argument('--locale', default='en_GB.UTF-8')
    ap.add_argument('--keyboard', default='us')
    ap.add_argument('--out', default=os.path.join(ROOT, 'dist', 'sd'), help='output folder')
    ap.add_argument('--keep-image', action='store_true', help='keep the raw .img next to the zip')
    ap.add_argument('--no-smoke', action='store_true', help="skip running the finished image's programs in a container")
    ap.add_argument('--no-docker', action='store_true', help='run on this Linux machine as root instead of in Docker')
    ap.add_argument('--dry-run', action='store_true', help='check everything and print the plan, make nothing')
    return ap.parse_args(argv)


def plan_image(args, inputs):
    """Validate the short inputs and describe the one reusable image."""
    if args.password and len(args.password) < 8:
        raise BuildError('--password: at least 8 characters')
    if args.ap_password:
        wifilib.validate_passphrase(args.ap_password)
    version = read_version()
    banks = [os.path.basename(inputs[k]) for k in ('piano', 'piano_lite', 'mech') if k in inputs]
    target, info = target_of(args)
    return dict(
        version=version,
        target=target,
        label=info['label'],
        onboard_audio=info['onboard_audio'],
        has_lite='piano_lite' in inputs,
        banks=banks,
        out_dir=os.path.abspath(args.out),
        password=args.password,
        ap_password=args.ap_password,
        country=args.country,
        timezone=args.timezone,
        locale=args.locale,
        keyboard=args.keyboard,
        banks_text=', '.join(banks),
        when=datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M UTC'),
        out_img_in_builder='/out/exxpiano.img',
        dietpi_version_out_in_builder='/out/dietpi-version.txt',
    )


def build_one(args, inputs, plan):
    out_dir = plan['out_dir']
    os.makedirs(out_dir, exist_ok=True)
    img = os.path.join(out_dir, 'exxpiano.img')
    dietpi_version_file = os.path.join(out_dir, 'dietpi-version.txt')
    for stale in (img, dietpi_version_file):
        if os.path.exists(stale):
            os.unlink(stale)
    if args.no_docker:
        if os.name != 'posix' or sys.platform == 'darwin' or os.geteuid() != 0:
            raise BuildError('--no-docker needs Linux and root')
        local_plan = dict(plan, out_img_in_builder=img, dietpi_version_out_in_builder=dietpi_version_file)
        cmd = ['bash', os.path.join(ROOT, 'scripts', 'sd-image-inner.sh')] + inner_args(local_plan, lambda k: inputs[k])
    else:
        docker_ok()
        cmd = docker_command(plan, inputs, lambda mapping: inner_args(plan, lambda k: mapping[k]))
    print('Making the reusable image (a few minutes) ...')
    env = dict(os.environ, SD_PASSWORD=args.password, SD_AP_PASSWORD=args.ap_password)
    if subprocess.run(cmd, env=env).returncode != 0:
        raise BuildError('the image could not be made (see the messages above)')
    if not os.path.isfile(img):
        raise BuildError('the builder reported success but there is no image')
    plan['dietpi_version'] = read_dietpi_version(dietpi_version_file)
    os.unlink(dietpi_version_file)
    if not args.no_smoke and not args.no_docker:
        print('Starting the programs of the finished image in a container ...')
        env['SD_ONBOARD_AUDIO'] = '1' if plan['onboard_audio'] else '0'
        if subprocess.run(docker_command(plan, {}, None, smoke=True), env=env).returncode != 0:
            raise BuildError('the finished image fails its smoke test (see above); it is left in %s' % out_dir)
    stem = artifact_stem(plan, plan['dietpi_version'])
    zip_name = stem + '-sd.zip'
    zip_path = os.path.join(out_dir, zip_name)
    print('Packing %s ...' % zip_name)
    make_zip(img, zip_path, stem + '.img')
    raw_image_hash = sha256_of(img)
    zip_hash = sha256_of(zip_path)
    if not args.keep_image:
        os.unlink(img)
    readme_name = 'README-%s.txt' % plan['target']
    with open(os.path.join(out_dir, readme_name), 'w', encoding='utf-8') as f:
        f.write(readme(plan, zip_name))
    manifest_name = 'manifest-%s.json' % plan['target']
    write_json(
        os.path.join(out_dir, manifest_name),
        dict(
            schema_version=2,
            product='exxpiano',
            target_family=plan['target'],
            product_version=plan['version'],
            dietpi_version=plan['dietpi_version'],
            dietpi_source=os.path.basename(inputs['dietpi']),
            dietpi_sha256=plan['dietpi_sha256'],
            installation_identity='generated-at-first-boot',
            setup_ssid_pattern='exxpiano-Setup-<6-hex-installation-id>',
            hostname_pattern='exxpiano-<6-hex-installation-id-lowercase>',
            banks=plan['banks'],
            image=zip_name,
            image_sha256=zip_hash,
            raw_image_sha256=raw_image_hash,
            built_at=plan['when'],
        ),
    )
    print('\nDone:')
    print('  card image : %s (%.0f MB)' % (zip_path, os.path.getsize(zip_path) / 1e6))
    print('  manifest   : %s' % os.path.join(out_dir, manifest_name))
    print('  readme     : %s' % os.path.join(out_dir, readme_name))
    return zip_path


def main(argv=None):
    args = parse_args(argv)
    try:
        inputs = resolve_inputs(args)
        plan = plan_image(args, inputs)
        print('Software %s; installation ID, hostname and setup Wi-Fi are generated automatically at first boot' % plan['version'])
        print('DietPi image: %s%s' % (inputs['dietpi'], ' (will be downloaded)' if args.download and not args.dietpi else ''))
        print('Banks: %s -> %s' % (plan['banks_text'], os.path.abspath(args.out)))
        if args.dry_run:
            print('(dry run: nothing is made)')
            return 0
        if args.download and not args.dietpi:
            url = DIETPI_BASE + target_of(args)[1]['image']
            print('Downloading %s ...' % url)
            download(url, inputs['dietpi'])
        dietpi_sha256 = sha256_of(inputs['dietpi'])
        plan['dietpi_sha256'] = dietpi_sha256
        inputs['deb'] = build_deb(plan['version'], os.environ.get('EXXPIANO_SD_WORK') or os.path.join(ROOT, 'build', 'sd-work'))
        build_one(args, inputs, plan)
        return 0
    except (BuildError, ValueError) as exc:
        print('build-sd-image:', exc, file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
