#!/usr/bin/env python3
"""Builds the signed Alpine packages of exxpiano (works on macOS and Linux: the build runs in an alpine:3.24 arm64 container).

  python3 scripts/build-apks.py [--out dist/apk] [--banks PIANO.sf2 MECH.sf2 PREVIEW.sf2] [--version V] [--packages exxpiano ...]

Always builds  exxpiano-<version>-r0.apk (the program, its OpenRC services, the configuration) and the two MIDI libraries of the bridge that
Alpine does not have: py3-mido and py3-python-rtmidi. With --banks also exxpiano-soundfonts (the piano, mechanics and preview .sf2 files, hundreds of MB).
The result is a small signed repository: <out>/exxpiano/aarch64/*.apk with its APKINDEX.tar.gz, and the public key <out>/<key>.rsa.pub that
a device must trust (the image puts it into /etc/apk/keys).

The signing key: EXXPIANO_APK_KEY (the private key in PEM, from a CI secret) with EXXPIANO_APK_KEY_NAME (default exxpiano), or else the
development key in build/apk-key/, made once on the first build. Packages signed with the development key are for your own devices only.
The key reaches the container through its environment, never through a command line or a log.
EXXPIANO_ALPINE_MIRROR (for example https://mirrors.dotsrc.org/alpine) replaces the pinned mirror of alpine/repositories when the pinned one is
slow from where you are: the branch stays, and apk still checks every package with the keys of Alpine. Downloaded packages are kept in
build/apk-cache for the next builds.
Needs Docker (Docker Desktop on a Mac runs arm64 natively).
"""

import argparse
import gzip
import io
import os
import re
import shutil
import subprocess
import sys
import tarfile
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
ALPINE_IMAGE = 'alpine:3.24'
BUILDER_IMAGE = 'exxpiano-apk-builder'  # alpine:3.24 with alpine-sdk (scripts/apk-builder.Dockerfile)
REPO = 'exxpiano'  # abuild names the repository after the folder of the APKBUILD folders
# in the order abuild must build them: the bridge's libraries before the program that depends on them
PACKAGES = ('py3-mido', 'py3-python-rtmidi', 'exxpiano')
# what the source archive of the exxpiano package holds (paths in this repository)
SOURCE_TREES = ('pi', 'assets/midi')
SOURCE_FILES = ('VERSION', 'README.md', 'NOTICE.md', 'LICENSE', 'docs/TROUBLESHOOTING.md', 'packaging/exxpiano-install-banks')
VERSION_RE = re.compile(r'^\d+(\.\d+)*(_(alpha|beta|pre|rc|p)\d*)?$')
KEY_NAME_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$')
DEV_KEY_DIR = os.path.join(ROOT, 'build', 'apk-key')
DEV_KEY_NAME = 'exxpiano-dev'
APK_CACHE = os.path.join(ROOT, 'build', 'apk-cache')
PINNED_MIRROR = 'https://dl-cdn.alpinelinux.org/alpine'
MIRROR_RE = re.compile(r'^https://[A-Za-z0-9.-]+(:\d+)?(/[A-Za-z0-9._~/-]*)?$')


def alpine_mirror(env):
    """The mirror to use instead of the pinned one, or None (EXXPIANO_ALPINE_MIRROR; HTTPS only)."""
    mirror = (env.get('EXXPIANO_ALPINE_MIRROR') or '').strip().rstrip('/')
    if not mirror:
        return None
    if not MIRROR_RE.fullmatch(mirror):
        raise BuildError('EXXPIANO_ALPINE_MIRROR must be an https:// address such as https://mirrors.dotsrc.org/alpine')
    return mirror


class BuildError(Exception):
    pass


def apk_version(text):
    """The VERSION file -> a version apk accepts (1.10.20, 1.11.0-rc1 -> 1.11.0_rc1)."""
    version = text.strip().replace('-', '_')
    if not VERSION_RE.fullmatch(version):
        raise BuildError('VERSION %r is not a version Alpine packages accept (digits and dots, optionally _rc1 and the like)' % text.strip())
    return version


def set_pkgver(apkbuild_text, version):
    out, n = re.subn(r'(?m)^pkgver=.*$', 'pkgver=' + version, apkbuild_text, count=1)
    if n != 1:
        raise BuildError('the APKBUILD has no pkgver line')
    return out


def source_members():
    """[(path in the archive relative to the top folder, path on disk)] of the exxpiano source archive, sorted."""
    found = []
    for tree in SOURCE_TREES:
        for dirpath, dirnames, filenames in os.walk(os.path.join(ROOT, tree)):
            dirnames[:] = sorted(d for d in dirnames if d != '__pycache__')
            for name in sorted(filenames):
                if name.endswith('.pyc') or name == '.DS_Store':
                    continue
                full = os.path.join(dirpath, name)
                found.append((os.path.relpath(full, ROOT), full))
    found.extend((rel, os.path.join(ROOT, rel)) for rel in SOURCE_FILES)
    return sorted(found)


def write_source_archive(path, top, mtime, version_text=None):
    """A reproducible .tar.gz of the sources: the same files give the same bytes (fixed times, owners and order). `version_text` is the
    content of VERSION in the archive (the version the package is built as; by default the VERSION file of the repository)."""
    with (
        open(path, 'wb') as raw,
        gzip.GzipFile(filename='', mode='wb', fileobj=raw, mtime=mtime) as gz,
        tarfile.open(fileobj=gz, mode='w', format=tarfile.PAX_FORMAT) as tf,
    ):
        for rel, full in source_members():
            if rel == 'VERSION' and version_text is not None:
                data = (version_text.strip() + '\n').encode('utf-8')
                ti = tarfile.TarInfo(top + '/VERSION')
                ti.size, ti.mode, ti.mtime, ti.uname, ti.gname = len(data), 0o644, mtime, 'root', 'root'
                tf.addfile(ti, io.BytesIO(data))
                continue
            ti = tf.gettarinfo(full, arcname=top + '/' + rel)
            ti.uid = ti.gid = 0
            ti.uname = ti.gname = 'root'
            ti.mtime = mtime
            ti.mode = 0o755 if os.access(full, os.X_OK) else 0o644
            with open(full, 'rb') as f:
                tf.addfile(ti, f)


def stage(work, version, banks, mtime, only=None, version_text=None):
    """The folders the container builds from: work/exxpiano/<package>/ with the APKBUILD, its scripts and its local sources."""
    shutil.rmtree(work, ignore_errors=True)
    names = PACKAGES + (('exxpiano-soundfonts',) if banks else ())
    if only:
        names = tuple(n for n in names if n in only)
    for name in names:
        src = os.path.join(ROOT, 'packaging', 'alpine', name)
        dst = os.path.join(work, REPO, name)
        shutil.copytree(src, dst)
        if name in ('exxpiano', 'exxpiano-soundfonts'):
            apkbuild = os.path.join(dst, 'APKBUILD')
            with open(apkbuild, encoding='utf-8') as f:
                text = set_pkgver(f.read(), version)
            with open(apkbuild, 'w', encoding='utf-8') as f:
                f.write(text)
    if 'exxpiano' in names:
        write_source_archive(os.path.join(work, REPO, 'exxpiano', 'exxpiano-%s.tar.gz' % version), 'exxpiano-%s' % version, mtime, version_text)
    if banks:
        for src, name in zip(banks, ('piano.sf2', 'mech.sf2', 'piano-preview.sf2')):
            shutil.copyfile(src, os.path.join(work, REPO, 'exxpiano-soundfonts', name))
    return names


def signing_key(env):
    """-> (name, private key PEM or None for the development key, warning or None)."""
    pem = env.get('EXXPIANO_APK_KEY', '').strip()
    if pem:
        name = env.get('EXXPIANO_APK_KEY_NAME', 'exxpiano')
        if not KEY_NAME_RE.fullmatch(name):
            raise BuildError('EXXPIANO_APK_KEY_NAME: letters, digits, dots, hyphens and underscores')
        if 'PRIVATE KEY' not in pem:
            raise BuildError('EXXPIANO_APK_KEY does not hold a private key in PEM')
        return name, pem, None
    return (
        DEV_KEY_NAME,
        None,
        (
            'WARNING: no EXXPIANO_APK_KEY: the packages are signed with the development key in %s. '
            'Use them on your own devices only; a release needs the project key.' % os.path.relpath(DEV_KEY_DIR, ROOT)
        ),
    )


# Runs in the container as root, then builds as the user "builder" (abuild does not build as root).
INNER = r'''
set -eu
[ -z "${ALPINE_MIRROR:-}" ] || sed -i "s#^https://dl-cdn.alpinelinux.org/alpine#$ALPINE_MIRROR#" /etc/apk/repositories
ln -sfn /var/cache/apk /etc/apk/cache    # the downloads are kept on the host (build/apk-cache) for the next builds
apk update -q >/dev/null                 # the builder image keeps no package index: abuild installs the build dependencies from it
install -d -o builder /home/builder/.abuild
if [ -n "${EXXPIANO_APK_KEY:-}" ]; then
  umask 077; printf '%s\n' "$EXXPIANO_APK_KEY" > "/home/builder/.abuild/$KEY_NAME.rsa"; umask 022
elif [ -s "/devkey/$KEY_NAME.rsa" ]; then
  cp "/devkey/$KEY_NAME.rsa" "/home/builder/.abuild/$KEY_NAME.rsa"
else
  openssl genrsa -out "/home/builder/.abuild/$KEY_NAME.rsa" 4096 2>/dev/null
  cp "/home/builder/.abuild/$KEY_NAME.rsa" "/devkey/$KEY_NAME.rsa"; chmod 600 "/devkey/$KEY_NAME.rsa"
  [ -z "${HOST_UID:-}" ] || chown "$HOST_UID:${HOST_GID:-$HOST_UID}" "/devkey/$KEY_NAME.rsa"
fi
unset EXXPIANO_APK_KEY
openssl rsa -in "/home/builder/.abuild/$KEY_NAME.rsa" -pubout -out "/home/builder/.abuild/$KEY_NAME.rsa.pub" 2>/dev/null
chown -R builder /home/builder/.abuild; chmod 600 "/home/builder/.abuild/$KEY_NAME.rsa"
cp "/home/builder/.abuild/$KEY_NAME.rsa.pub" /etc/apk/keys/
echo "PACKAGER_PRIVKEY=/home/builder/.abuild/$KEY_NAME.rsa" > /home/builder/.abuild/abuild.conf
# abuild builds into a folder of the container, not into the mounted /out: on Linux the mounted folder belongs to the user of the host and
# "builder" may not write there (Docker Desktop on a Mac does not check, a GitHub runner does: "mkdir: can't create directory
# '/out/exxpiano/aarch64': Permission denied"). Root copies the result and gives it to the user of the host.
cp -r /work /home/builder/work; chown -R builder /home/builder/work
install -d -o builder /home/builder/repo
# the packages built first are build dependencies of exxpiano: apk finds them in this repository (signed with the key installed above)
echo /home/builder/repo/exxpiano >> /etc/apk/repositories
for pkg in $PACKAGES; do
  echo "== $pkg"
  cd "/home/builder/work/exxpiano/$pkg"
  case "$pkg" in exxpiano|exxpiano-soundfonts) su builder -c "abuild checksum" >/dev/null ;; esac
  rc=0; su builder -c "REPODEST=/home/builder/repo abuild -r" > /tmp/abuild.log 2>&1 || rc=$?
  grep -v "^>>> .*: \(Fetching\|Checking\|Unpacking\)" /tmp/abuild.log || true
  [ "$rc" = 0 ] || { echo "abuild failed for $pkg (exit $rc)"; exit 1; }
  ls /home/builder/repo/exxpiano/*/"$pkg"-*.apk >/dev/null 2>&1 || { echo "abuild made no package for $pkg"; exit 1; }
  apk update -q >/dev/null 2>&1 || true   # the next package sees this one
done
mkdir -p /out; cp -r /home/builder/repo/. /out/
cp "/home/builder/.abuild/$KEY_NAME.rsa.pub" /out/
[ -z "${HOST_UID:-}" ] || chown -R "$HOST_UID:${HOST_GID:-$HOST_UID}" /out
'''


def build(args, env=None, runner=subprocess.run):
    env = dict(os.environ if env is None else env)
    with open(os.path.join(ROOT, 'VERSION'), encoding='utf-8') as f:
        version_text = (args.version or f.read()).strip()
    version = apk_version(version_text)
    banks = None
    if args.banks:
        banks = [os.path.abspath(p) for p in args.banks]
        for p in banks:
            if not os.path.isfile(p):
                raise BuildError('no such file: ' + p)
    key_name, pem, warning = signing_key(env)
    if warning:
        print(warning, file=sys.stderr)
    mtime = int(env.get('SOURCE_DATE_EPOCH') or time.time())
    work = getattr(args, 'work', None) or os.path.join(ROOT, 'build', 'apk-work')
    out = os.path.abspath(args.out)
    only = getattr(args, 'packages', None)
    if only:
        unknown = set(only) - set(PACKAGES + ('exxpiano-soundfonts',))
        if unknown:
            raise BuildError('unknown package: ' + ', '.join(sorted(unknown)))
    names = stage(work, version, banks, mtime, only, version_text)
    if not only:
        shutil.rmtree(os.path.join(out, REPO), ignore_errors=True)  # a full build replaces the repository; --packages adds to it
    os.makedirs(out, exist_ok=True)
    r = runner(
        ['docker', 'build', '-q', '-t', BUILDER_IMAGE, '-f', os.path.join(ROOT, 'scripts', 'apk-builder.Dockerfile'), os.path.join(ROOT, 'scripts')], env=env
    )
    if r.returncode != 0:
        raise BuildError('the builder image could not be made (is Docker running?)')
    os.makedirs(DEV_KEY_DIR, exist_ok=True)
    child_env = dict(env, KEY_NAME=key_name, PACKAGES=' '.join(names), ALPINE_MIRROR=alpine_mirror(env) or '')
    os.makedirs(APK_CACHE, exist_ok=True)
    child_env.pop('EXXPIANO_APK_KEY', None)
    if pem:
        child_env['EXXPIANO_APK_KEY'] = pem
    if hasattr(os, 'getuid'):  # the packages and the development key belong to the user who builds, not to root or "builder"
        child_env['HOST_UID'], child_env['HOST_GID'] = str(os.getuid()), str(os.getgid())
    cmd = [
        'docker',
        'run',
        '--rm',
        '--platform',
        'linux/arm64',
        '-e',
        'KEY_NAME',
        '-e',
        'PACKAGES',
        '-e',
        'EXXPIANO_APK_KEY',
        '-e',
        'ALPINE_MIRROR',
        '-e',
        'HOST_UID',
        '-e',
        'HOST_GID',
        '-v',
        APK_CACHE + ':/var/cache/apk',
        '-v',
        work + ':/work:ro',
        '-v',
        out + ':/out',
        '-v',
        DEV_KEY_DIR + ':/devkey',
        BUILDER_IMAGE,
        'sh',
        '-c',
        INNER,
    ]
    r = runner(cmd, env=child_env)
    if r.returncode != 0:
        raise BuildError('the package build failed (see above)')
    built = sorted(os.path.join(dp, n) for dp, _, fs in os.walk(os.path.join(out, REPO)) for n in fs if n.endswith('.apk'))
    for path in built:
        print('%s  (%.1f MB)' % (os.path.relpath(path, ROOT), os.path.getsize(path) / 1e6))
    print('public key: %s' % os.path.relpath(os.path.join(out, key_name + '.rsa.pub'), ROOT))
    return built


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--version', help='default: the VERSION file')
    ap.add_argument('--out', default=os.path.join(ROOT, 'dist', 'apk'))
    ap.add_argument('--banks', nargs=3, metavar=('PIANO_SF2', 'MECH_SF2', 'PREVIEW_SF2'))
    ap.add_argument('--packages', nargs='+', metavar='NAME', help='build only these (added to the repository in --out, for tests)')
    args = ap.parse_args(argv)
    try:
        build(args)
    except BuildError as exc:
        print('build-apks: %s' % exc, file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
