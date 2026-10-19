#!/usr/bin/env python3
"""Makes the reusable exxpiano SD card image: Alpine Linux (64-bit, OpenRC) + the exxpiano packages + the required banks.

  python3 scripts/build-sd-image.py [--apk-repo dist/apk] [--piano P.sf2 --mech M.sf2] [--country RU --timezone Europe/Moscow]

Result in dist/sd/: exxpiano-rpi-v<version>-alpine-<alpine version>-sd.zip (one image for the Raspberry Pi 3, 4 and 5), a README and a
manifest. Flash the same ZIP with Raspberry Pi Imager or balenaEtcher. At first boot each installed copy creates a persistent random
installation ID; the ID makes its setup Wi-Fi and hostname unique.

The packages come from scripts/build-apks.py (dist/apk); everything else from the Alpine branch and mirror pinned in alpine/ and checked
with the keys there. Needs Docker (Docker Desktop on a Mac runs the arm64 builder natively), or Alpine aarch64 with root and --no-docker.
The first start is offline: no Internet, no Ethernet cable, no saved Wi-Fi is needed.
HONEST STATUS: the image is built and read back, and its programs run in a container; booting it on a Raspberry Pi is checked by hand
(see the README of the image and /var/log/exxpiano-firstboot.log).
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
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'pi'))
import wifilib  # noqa: E402

BUILDER_IMAGE = 'exxpiano-sd-builder'
LABEL = 'Raspberry Pi 3, 4 and 5 (64-bit)'
VERSION_RE = re.compile(r'^[0-9A-Za-z][0-9A-Za-z._-]*$')
ALPINE_VERSION_RE = re.compile(r'^3\.24\.\d+$')
COUNTRY_RE = re.compile(r'^[A-Z]{2}$')
MIRROR_RE = re.compile(r'^https://[A-Za-z0-9.-]+(:\d+)?(/[A-Za-z0-9._~/-]*)?$')
APK_CACHE = os.path.join(ROOT, 'build', 'apk-cache')


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


def read_env_file(path):
    out = {}
    with open(path, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#'):
                key, _, value = line.partition('=')
                out[key] = value
    return out


def read_info(info_dir):
    """What the builder reports: the Alpine version and every installed package (name-version-release per line)."""
    try:
        with open(os.path.join(info_dir, 'alpine-version.txt'), encoding='ascii') as f:
            alpine = f.read().strip()
        with open(os.path.join(info_dir, 'packages.txt'), encoding='utf-8') as f:
            packages = [line.strip() for line in f if line.strip()]
    except OSError as exc:
        raise BuildError('the builder did not report the Alpine version and the packages: %s' % exc)
    if not ALPINE_VERSION_RE.fullmatch(alpine):
        raise BuildError('the Alpine version is missing or not 3.24.x: %r' % alpine)
    return alpine, packages


def artifact_stem(version, alpine_version):
    return 'exxpiano-rpi-v%s-alpine-%s' % (version, alpine_version)


def write_json(path, data):
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write('\n')
    os.replace(tmp, path)


def resolve_inputs(args):
    """Check everything before the long part starts. Returns a dict of absolute paths."""
    inputs = {}
    repo = os.path.abspath(args.apk_repo or os.path.join(ROOT, 'dist', 'apk'))
    if not os.path.isfile(os.path.join(repo, 'exxpiano', 'aarch64', 'APKINDEX.tar.gz')):
        raise BuildError('no package repository in %s: make it with scripts/build-apks.py (or pass --apk-repo DIR)' % repo)
    keys = sorted(n for n in os.listdir(repo) if n.endswith('.rsa.pub'))
    if len(keys) != 1:
        raise BuildError('%s must hold exactly one public key (*.rsa.pub), the one its packages are signed with' % repo)
    inputs['apk_repo'] = repo
    inputs['piano'] = os.path.abspath(args.piano or os.path.join(ROOT, 'build', 'piano-full.sf2'))
    inputs['mech'] = os.path.abspath(args.mech or os.path.join(ROOT, 'build', 'mech.sf2'))
    lite = args.piano_lite or os.path.join(ROOT, 'build', 'piano-lite.sf2')       # for boards with 512 MB; optional unless asked for by name
    if args.piano_lite or os.path.isfile(lite):
        inputs['piano_lite'] = os.path.abspath(lite)
    if args.ssh_key:
        inputs['ssh_key'] = os.path.abspath(args.ssh_key)
    for key in [k for k in ('piano', 'mech', 'piano_lite') if k in inputs]:
        if not os.path.isfile(inputs[key]) or not os.access(inputs[key], os.R_OK):
            raise BuildError('the required %s bank is missing or unreadable: %s (prepare defaults with scripts/make-soundfonts.sh or pass --%s FILE.sf2)'
                             % (key, inputs[key], key.replace('_', '-')))
    if 'ssh_key' in inputs and not os.path.isfile(inputs['ssh_key']):
        raise BuildError('no such public key: %s' % inputs['ssh_key'])
    return inputs


def docker_ok():
    if not shutil.which('docker'):
        raise BuildError('Docker is not installed: install Docker Desktop (or use Alpine aarch64 with --no-docker as root)')
    if subprocess.run(['docker', 'info'], capture_output=True).returncode != 0:
        raise BuildError('Docker is not running: start Docker Desktop and try again')
    r = subprocess.run(['docker', 'build', '-q', '--platform', 'linux/arm64', '-t', BUILDER_IMAGE,
                        '-f', os.path.join(ROOT, 'scripts', 'sd-builder.Dockerfile'), os.path.join(ROOT, 'scripts')], capture_output=True)
    if r.returncode != 0:
        raise BuildError('the builder image could not be made: %s' % r.stderr.decode(errors='replace')[-300:])


def inner_args(plan, path_of):
    """The arguments of sd-image-inner.sh; `path_of(key)` gives the path of an input as the script sees it."""
    a = ['--apk-repo', path_of('apk_repo'), '--out', plan['img_in_builder'], '--info-out', plan['info_in_builder'], '--cache-dir', plan['cache_in_builder'],
         '--hostname', 'exxpiano', '--country', plan['country'], '--timezone', plan['timezone'],
         '--piano', path_of('piano'), '--mech', path_of('mech')]
    if plan.get('has_lite'):
        a += ['--piano-lite', path_of('piano_lite')]
    if plan.get('has_ssh_key'):
        a += ['--ssh-key', path_of('ssh_key')]
    if plan.get('mirror'):
        a += ['--mirror', plan['mirror']]
    return a                                    # the passwords go through the environment (SD_PASSWORD, SD_AP_PASSWORD), not through the arguments


def docker_command(plan, inputs, script_args):
    """`docker run` with the repository and every input mounted read-only (so nothing of yours can be changed) and the output folder writable."""
    mounts, mapping = [], {}
    for key, path in sorted(inputs.items()):
        if os.path.isdir(path):
            slot = '/in/%s' % key
            mounts += ['-v', '%s:%s:ro' % (path, slot)]
            mapping[key] = slot
        else:
            slot = '/in/%d' % len(mounts)
            mounts += ['-v', '%s:%s:ro' % (os.path.dirname(path), slot)]
            mapping[key] = '%s/%s' % (slot, os.path.basename(path))
    cmd = ['docker', 'run', '--rm', '--platform', 'linux/arm64', '-e', 'SD_PASSWORD', '-e', 'SD_AP_PASSWORD',
           '-v', '%s:/repo:ro' % ROOT, '-v', '%s:/out' % plan['out_dir'], '-v', '%s:/cache' % APK_CACHE] + mounts + [BUILDER_IMAGE]
    return cmd + ['bash', '/repo/scripts/sd-image-inner.sh'] + script_args(mapping)


def make_zip(img, zip_path, image_name):
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED, compresslevel=3, allowZip64=True) as z:
        z.write(img, image_name)


def readme(plan, zip_name):
    login = ('root logs in over SSH with the password given when the image was built.' if plan['password'] else
             'root logs in over SSH with a key only. Put your public key into the file authorized_keys on the boot partition of the card\n'
             '  (the FAT partition any computer can open) before the first start, or at any time later, and start the device.')
    return '''exxpiano SD image
==================
Built: version {version}, {when}
Image: {zip_name}
Alpine Linux: {alpine_version} (64-bit, OpenRC), packages from {mirror}/{branch}
Banks: {banks}
Writing the card
  1. Raspberry Pi Imager: "Choose OS" -> "Use custom" -> pick the zip (it is used as it is). Or balenaEtcher: "Flash from file" -> the zip.
  2. Put the card in a {label} and switch it on. The first start takes a minute or two: the card grows to its size, then the banks load.
     The first start is offline: it needs neither Internet nor an Ethernet cable.
  3. The installation creates and permanently saves its own 6-character hexadecimal ID.

Without a saved network the device starts Wi-Fi exxpiano-Setup-<INSTALLATION-ID> (password: {ap_pw}).
Its hostname is exxpiano-<installation-id-in-lowercase>; open http://<hostname>.local:8080/ or use the address from the router's client list.
The exact installation ID, Wi-Fi name and QR code are shown by the device web page after the first start.
The Raspberry Pi 5 has no 3.5 mm jack: connect a USB sound card or an audio interface, the piano plays through it.

Administration: {login}
If something is wrong: the log of the first start is /var/log/exxpiano-firstboot.log; the services log into /var/log/messages.
'''.format(version=plan['version'], when=plan['when'], zip_name=zip_name, alpine_version=plan['alpine_version'],
           mirror=plan.get('mirror_used') or plan['alpine']['ALPINE_MIRROR'], branch=plan['alpine']['ALPINE_BRANCH'], banks=plan['banks_text'],
           ap_pw=plan['ap_password'] or wifilib.AP_DEFAULT_PASSWORD, label=LABEL, login=login)


def parse_args(argv):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--apk-repo', help='the packages made by scripts/build-apks.py; default dist/apk')
    ap.add_argument('--piano', help='the piano bank (.sf2); default build/piano-full.sf2')
    ap.add_argument('--piano-lite', help='the smaller piano bank for boards with 512 MB (.sf2); default build/piano-lite.sf2 when it exists. The image picks it at the first start when the memory is under 800 MB')
    ap.add_argument('--mech', help='the mechanics bank (.sf2); default build/mech.sf2')
    ap.add_argument('--password', default=os.environ.get('SD_PASSWORD', ''),
                    help='the root password (SSH); default: SD_PASSWORD; without either root logs in by key only')
    ap.add_argument('--ssh-key', help='a public key root accepts over SSH (for example ~/.ssh/id_ed25519.pub)')
    ap.add_argument('--ap-password', default=os.environ.get('SD_AP_PASSWORD', ''),
                    help='the password of the setup network; default: SD_AP_PASSWORD, then exxpianosetup')
    ap.add_argument('--country', default='GB', help='Wi-Fi country code (RU, GB ...)')
    ap.add_argument('--timezone', default='UTC')
    ap.add_argument('--mirror', default=os.environ.get('EXXPIANO_ALPINE_MIRROR', ''),
                    help='an Alpine mirror (https) instead of the pinned one, when that is slow from here; default: EXXPIANO_ALPINE_MIRROR. The branch and the keys stay')
    ap.add_argument('--out', default=os.path.join(ROOT, 'dist', 'sd'), help='output folder')
    ap.add_argument('--keep-image', action='store_true', help='keep the raw .img next to the zip')
    ap.add_argument('--no-docker', action='store_true', help='run on this Alpine aarch64 machine as root instead of in Docker')
    ap.add_argument('--dry-run', action='store_true', help='check everything and print the plan, make nothing')
    return ap.parse_args(argv)


def plan_image(args, inputs):
    """Validate the short inputs and describe the one reusable image."""
    if args.password and len(args.password) < 8:
        raise BuildError('--password: at least 8 characters')
    if args.ap_password:
        wifilib.validate_passphrase(args.ap_password)
    country = args.country.upper()
    if not COUNTRY_RE.fullmatch(country):
        raise BuildError('--country: two letters (GB, RU ...)')
    if not re.fullmatch(r'[A-Za-z0-9_+-]+(/[A-Za-z0-9_+-]+)*', args.timezone):
        raise BuildError('--timezone: a zone name such as Europe/Moscow')
    mirror = args.mirror.strip().rstrip('/')
    if mirror and not MIRROR_RE.fullmatch(mirror):
        raise BuildError('--mirror: an https:// address such as https://mirrors.dotsrc.org/alpine')
    alpine = read_env_file(os.path.join(ROOT, '.github', 'alpine-image.env'))
    banks = [os.path.basename(inputs[k]) for k in ('piano', 'piano_lite', 'mech') if k in inputs]
    key = sorted(n for n in os.listdir(inputs['apk_repo']) if n.endswith('.rsa.pub'))[0]
    return dict(version=read_version(), has_lite='piano_lite' in inputs, has_ssh_key='ssh_key' in inputs, banks=banks,
                out_dir=os.path.abspath(args.out), password=args.password, ap_password=args.ap_password, country=country,
                timezone=args.timezone, alpine=alpine, signing_key=key, signing_key_sha256=sha256_of(os.path.join(inputs['apk_repo'], key)),
                banks_text=', '.join(banks), when=datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M UTC'),
                mirror=mirror, img_in_builder='/out/exxpiano.img', info_in_builder='/out/info', cache_in_builder='/cache')


def build_one(args, inputs, plan):
    out_dir = plan['out_dir']
    os.makedirs(out_dir, exist_ok=True)
    img = os.path.join(out_dir, 'exxpiano.img')
    info = os.path.join(out_dir, 'info')
    if os.path.exists(img):
        os.unlink(img)
    shutil.rmtree(info, ignore_errors=True)
    if args.no_docker:
        if sys.platform != 'linux' or os.geteuid() != 0 or not os.path.exists('/etc/alpine-release'):
            raise BuildError('--no-docker needs Alpine Linux (aarch64) and root')
        local_plan = dict(plan, img_in_builder=img, info_in_builder=info, cache_in_builder=APK_CACHE)
        cmd = ['bash', os.path.join(ROOT, 'scripts', 'sd-image-inner.sh')] + inner_args(local_plan, lambda k: inputs[k])
    else:
        docker_ok()
        os.makedirs(APK_CACHE, exist_ok=True)
        cmd = docker_command(plan, inputs, lambda mapping: inner_args(plan, lambda k: mapping[k]))
    print('Making the reusable image (a few minutes) ...')
    env = dict(os.environ, SD_PASSWORD=args.password, SD_AP_PASSWORD=args.ap_password)
    if subprocess.run(cmd, env=env).returncode != 0:
        raise BuildError('the image could not be made (see the messages above)')
    if not os.path.isfile(img):
        raise BuildError('the builder reported success but there is no image')
    plan['alpine_version'], packages = read_info(info)
    try:
        with open(os.path.join(info, 'mirror.txt'), encoding='ascii') as f:
            plan['mirror_used'] = f.read().strip()
    except OSError:
        plan['mirror_used'] = plan['mirror'] or plan['alpine']['ALPINE_MIRROR']
    shutil.rmtree(info, ignore_errors=True)
    stem = artifact_stem(plan['version'], plan['alpine_version'])
    zip_name = stem + '-sd.zip'
    zip_path = os.path.join(out_dir, zip_name)
    print('Packing %s ...' % zip_name)
    make_zip(img, zip_path, stem + '.img')
    raw_image_hash = sha256_of(img)
    zip_hash = sha256_of(zip_path)
    if not args.keep_image:
        os.unlink(img)
    with open(os.path.join(out_dir, 'README.txt'), 'w', encoding='utf-8') as f:
        f.write(readme(plan, zip_name))
    alpine = plan['alpine']
    write_json(os.path.join(out_dir, 'manifest.json'), dict(
        schema_version=3, product='exxpiano', target_family='rpi', boards=['Raspberry Pi 3', 'Raspberry Pi 4', 'Raspberry Pi 5'],
        product_version=plan['version'], alpine_version=plan['alpine_version'], alpine_branch=alpine['ALPINE_BRANCH'],
        alpine_mirror=plan['mirror_used'], alpine_pinned_mirror=alpine['ALPINE_MIRROR'], alpine_keys_sha256=sorted(v for k, v in alpine.items() if k.startswith('ALPINE_KEY_')),
        package_signing_key=plan['signing_key'], package_signing_key_sha256=plan['signing_key_sha256'], packages=packages,
        installation_identity='generated-at-first-boot', setup_ssid_pattern='exxpiano-Setup-<6-hex-installation-id>',
        hostname_pattern='exxpiano-<6-hex-installation-id-lowercase>', banks=plan['banks'],
        image=zip_name, image_sha256=zip_hash, raw_image_sha256=raw_image_hash, built_at=plan['when']))
    print('\nDone:')
    print('  card image : %s (%.0f MB)' % (zip_path, os.path.getsize(zip_path) / 1e6))
    print('  manifest   : %s' % os.path.join(out_dir, 'manifest.json'))
    print('  readme     : %s' % os.path.join(out_dir, 'README.txt'))
    return zip_path


def main(argv=None):
    args = parse_args(argv)
    try:
        inputs = resolve_inputs(args)
        plan = plan_image(args, inputs)
        print('Software %s; installation ID, hostname and setup Wi-Fi are generated automatically at first boot' % plan['version'])
        print('Packages: %s (signed with %s); Alpine %s from %s' % (inputs['apk_repo'], plan['signing_key'], plan['alpine']['ALPINE_BRANCH'],
                                                                     plan['alpine']['ALPINE_MIRROR']))
        print('Banks: %s -> %s' % (plan['banks_text'], os.path.abspath(args.out)))
        if args.dry_run:
            print('(dry run: nothing is made)')
            return 0
        build_one(args, inputs, plan)
        return 0
    except (BuildError, ValueError) as exc:
        print('build-sd-image:', exc, file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
