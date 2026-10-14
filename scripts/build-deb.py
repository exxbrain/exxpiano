#!/usr/bin/env python3
"""Build Debian packages without dpkg-deb (works on macOS and Linux, only the standard library).

  scripts/build-deb.py [--version 1.0.0] [--out dist] [--banks PIANO.sf2 MECH.sf2]

Always builds  exxpiano_<version>_all.deb   (program, services, config, dependencies).
With --banks also builds exxpiano-soundfonts_<version>_all.deb (the two .sf2 files, hundreds of MB).
Install on the Pi:  sudo apt install ./exxpiano_1.0.0_all.deb
"""
import argparse
import gzip
import hashlib
import os
import sys
import tarfile
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
MAINTAINER = 'Denis Zakharov <noreply@localhost>'
# hostapd and dnsmasq-base: the setup access point (no system service of theirs is used: exxpiano-wifi starts them itself)
DEPENDS = 'fluidsynth, alsa-utils, python3, python3-mido, python3-rtmidi, hostapd, dnsmasq-base, iw, wpasupplicant, ifupdown, isc-dhcp-client'
RECOMMENDS = 'avahi-daemon'


def src(*parts):
    return os.path.join(ROOT, *parts)


def read(path):
    with open(path, 'rb') as f:
        return f.read()


def md5_of(path):
    h = hashlib.md5()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def parents(arcname):
    parts = arcname.split('/')[:-1]
    return ['/'.join(parts[:i + 1]) for i in range(len(parts))]


def write_tar_gz(path, files, mtime, extra_dirs=()):
    """files: list of (arcname, source) where source is a path (str) or bytes; mode comes from the tuple."""
    dirs = sorted({d for arc, _, _ in files for d in parents(arc)} | set(extra_dirs))
    with open(path, 'wb') as raw, \
            gzip.GzipFile(filename='', mode='wb', fileobj=raw, compresslevel=6, mtime=mtime) as gz, \
            tarfile.open(fileobj=gz, mode='w', format=tarfile.GNU_FORMAT) as tf:
        def owner(ti):
            ti.uid = ti.gid = 0
            ti.uname = ti.gname = 'root'
            ti.mtime = mtime
            return ti
        root = owner(tarfile.TarInfo('./'))
        root.type, root.mode = tarfile.DIRTYPE, 0o755
        tf.addfile(root)
        for d in dirs:
            ti = owner(tarfile.TarInfo('./' + d))
            ti.type, ti.mode = tarfile.DIRTYPE, 0o755
            tf.addfile(ti)
        for arc, source, mode in files:
            ti = owner(tarfile.TarInfo('./' + arc))
            ti.mode = mode
            if isinstance(source, bytes):
                import io
                ti.size = len(source)
                tf.addfile(ti, io.BytesIO(source))
            else:
                ti.size = os.path.getsize(source)
                with open(source, 'rb') as f:
                    tf.addfile(ti, f)


def ar_header(name, size, mtime):
    hdr = '%-16s%-12d%-6d%-6d%-8s%-10d`\n' % (name, mtime, 0, 0, '100644', size)
    assert len(hdr) == 60, hdr
    return hdr.encode('ascii')


def write_deb(deb_path, control_tar, data_tar, mtime):
    with open(deb_path, 'wb') as out:
        out.write(b'!<arch>\n')
        for name, path in (('debian-binary', None), ('control.tar.gz', control_tar), ('data.tar.gz', data_tar)):
            if path is None:
                data = b'2.0\n'
                out.write(ar_header(name, len(data), mtime))
                out.write(data)
                size = len(data)
            else:
                size = os.path.getsize(path)
                out.write(ar_header(name, size, mtime))
                with open(path, 'rb') as f:
                    for block in iter(lambda: f.read(1 << 20), b''):
                        out.write(block)
            if size % 2:
                out.write(b'\n')


def build_package(out_dir, name, version, depends, description, files, extra_dirs, scripts, conffiles, mtime, tmp, recommends=''):
    """files: [(arcname, source_path_or_bytes, mode)]; scripts: {'postinst': bytes, ...}."""
    md5lines, installed = [], 0
    for arc, source, _ in files:
        if isinstance(source, bytes):
            digest, size = hashlib.md5(source).hexdigest(), len(source)
        else:
            digest, size = md5_of(source), os.path.getsize(source)
        md5lines.append('%s  %s\n' % (digest, arc))
        installed += size
    control = (
        'Package: %s\nVersion: %s\nArchitecture: all\nMaintainer: %s\nInstalled-Size: %d\n'
        'Depends: %s\n%sSection: sound\nPriority: optional\nDescription: %s\n'
    ) % (name, version, MAINTAINER, (installed + 1023) // 1024, depends, 'Recommends: %s\n' % recommends if recommends else '', description)
    ctl_files = [('control', control.encode('utf-8'), 0o644),
                 ('md5sums', ''.join(md5lines).encode('ascii'), 0o644)]
    if conffiles:
        ctl_files.append(('conffiles', ''.join(c + '\n' for c in conffiles).encode('ascii'), 0o644))
    for script, data in scripts.items():
        ctl_files.append((script, data, 0o755))

    data_tar = os.path.join(tmp, name + '-data.tar.gz')
    ctl_tar = os.path.join(tmp, name + '-control.tar.gz')
    write_tar_gz(data_tar, files, mtime, extra_dirs)
    # control archive has no directories except './'
    with open(ctl_tar, 'wb') as raw, \
            gzip.GzipFile(filename='', mode='wb', fileobj=raw, compresslevel=9, mtime=mtime) as gz, \
            tarfile.open(fileobj=gz, mode='w', format=tarfile.GNU_FORMAT) as tf:
        import io
        root = tarfile.TarInfo('./')
        root.type, root.mode, root.uid, root.gid, root.uname, root.gname, root.mtime = \
            tarfile.DIRTYPE, 0o755, 0, 0, 'root', 'root', mtime
        tf.addfile(root)
        for arc, data, mode in ctl_files:
            ti = tarfile.TarInfo('./' + arc)
            ti.size, ti.mode, ti.uid, ti.gid, ti.uname, ti.gname, ti.mtime = len(data), mode, 0, 0, 'root', 'root', mtime
            tf.addfile(ti, io.BytesIO(data))
    deb_path = os.path.join(out_dir, '%s_%s_all.deb' % (name, version))
    write_deb(deb_path, ctl_tar, data_tar, mtime)
    return deb_path


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--version', default=read(src('VERSION')).decode().strip(), help='default: the VERSION file')
    ap.add_argument('--out', default=src('dist'))
    ap.add_argument('--banks', nargs=2, metavar=('PIANO_SF2', 'MECH_SF2'))
    args = ap.parse_args()

    mtime = int(os.environ.get('SOURCE_DATE_EPOCH', time.time()))
    os.makedirs(args.out, exist_ok=True)
    import tempfile
    copyright_text = (
        'exxpiano: scripts MIT (Denis Zakharov). Sounds: Salamander Grand Piano V3 by Alexander Holm, CC-BY.\n\n'
        + read(src('NOTICE.md')).decode('utf-8') + '\n' + read(src('LICENSE')).decode('utf-8')).encode('utf-8')

    main_files = [
        ('opt/exxpiano/exxpiano-fx.py', src('pi', 'exxpiano-fx.py'), 0o755),
        ('opt/exxpiano/VERSION', (args.version + '\n').encode(), 0o644),
        ('opt/exxpiano/exxpiano-run', src('pi', 'exxpiano-run'), 0o755),
        ('opt/exxpiano/exxpianoconf.py', src('pi', 'exxpianoconf.py'), 0o644),
        ('opt/exxpiano/sf2info.py', src('pi', 'sf2info.py'), 0o755),
        ('opt/exxpiano/banklib.py', src('pi', 'banklib.py'), 0o644),
        ('opt/exxpiano/bankimport.py', src('pi', 'bankimport.py'), 0o644),
        ('opt/exxpiano/usblib.py', src('pi', 'usblib.py'), 0o644),
        ('opt/exxpiano/midilib.py', src('pi', 'midilib.py'), 0o644),
        ('opt/exxpiano/midiplayer.py', src('pi', 'midiplayer.py'), 0o644),
        ('opt/exxpiano/exxpiano-demo-midi.py', src('pi', 'exxpiano-demo-midi.py'), 0o755),
        ('opt/exxpiano/exxpiano-meter.py', src('pi', 'exxpiano-meter.py'), 0o755),
        ('etc/alsa/conf.d/60-exxpiano-tap.conf', src('pi', '60-exxpiano-tap.conf'), 0o644),
        ('usr/share/exxpiano/midi/liszt-un-sospiro-2011.mid', src('assets', 'midi', 'liszt-un-sospiro-2011.mid'), 0o644),
        ('usr/share/exxpiano/midi/liszt-la-campanella-2013.mid', src('assets', 'midi', 'liszt-la-campanella-2013.mid'), 0o644),
        ('usr/share/exxpiano/midi/NOTICE.md', src('assets', 'midi', 'NOTICE.md'), 0o644),
        ('opt/exxpiano/exxpiano-usb.py', src('pi', 'exxpiano-usb.py'), 0o755),
        ('lib/systemd/system/exxpiano-usb@.service', src('pi', 'exxpiano-usb@.service'), 0o644),
        ('opt/exxpiano/profiles.py', src('pi', 'profiles.py'), 0o644),
        ('opt/exxpiano/sysmon.py', src('pi', 'sysmon.py'), 0o644),
        ('opt/exxpiano/wifilib.py', src('pi', 'wifilib.py'), 0o644),
        ('opt/exxpiano/qrlib.py', src('pi', 'qrlib.py'), 0o644),
        ('opt/exxpiano/deviceid.py', src('pi', 'deviceid.py'), 0o755),
        ('opt/exxpiano/exxpiano-wifi.py', src('pi', 'exxpiano-wifi.py'), 0o755),
        ('lib/systemd/system/exxpiano-wifi.service', src('pi', 'exxpiano-wifi.service'), 0o644),
        ('opt/exxpiano/exxpiano-web.py', src('pi', 'exxpiano-web.py'), 0o755),
        ('opt/exxpiano/web/index.html', src('pi', 'web', 'index.html'), 0o644),
        ('lib/systemd/system/exxpiano-web.service', src('pi', 'exxpiano-web.service'), 0o644),
        ('lib/udev/rules.d/90-exxpiano.rules', src('pi', '90-exxpiano.rules'), 0o644),
        ('etc/exxpiano.conf', src('pi', 'exxpiano.conf'), 0o644),
        ('lib/systemd/system/exxpiano.service', src('pi', 'exxpiano.service'), 0o644),
        ('lib/systemd/system/exxpiano-fx.service', src('pi', 'exxpiano-fx.service'), 0o644),
        ('usr/bin/exxpiano-install-banks', src('packaging', 'exxpiano-install-banks'), 0o755),
        ('usr/share/doc/exxpiano/README.md', src('README.md'), 0o644),
        ('usr/share/doc/exxpiano/TROUBLESHOOTING.md', src('docs', 'TROUBLESHOOTING.md'), 0o644),
        ('usr/share/doc/exxpiano/copyright', copyright_text, 0o644),
    ]
    scripts = {name: read(src('packaging', 'debian', name)) for name in ('postinst', 'prerm', 'postrm')}
    description = (
        'Digital piano on FluidSynth with piano mechanics (Raspberry Pi)\n'
        ' Systemd services exxpiano (FluidSynth) and exxpiano-fx (MIDI bridge that adds hammer,\n'
        ' damper, string resonance and pedal noise). Settings: /etc/exxpiano.conf. Web page on\n'
        ' port 8080 with Wi-Fi setup (the Pi starts its own network when it has none).\n'
        ' .\n'
        ' Sound banks are not included. Install exxpiano-soundfonts or run\n'
        ' exxpiano-install-banks.\n')

    with tempfile.TemporaryDirectory() as tmp:
        built = [build_package(args.out, 'exxpiano', args.version, DEPENDS, description, main_files,
                               ['opt/exxpiano/soundfonts', 'var/lib/exxpiano'], scripts, ['/etc/exxpiano.conf'], mtime, tmp, RECOMMENDS)]
        if args.banks:
            piano, mech = args.banks
            for p in (piano, mech):
                if not os.path.isfile(p):
                    sys.exit('No such file: ' + p)
            bank_files = [('opt/exxpiano/soundfonts/piano.sf2', os.path.abspath(piano), 0o644),
                          ('opt/exxpiano/soundfonts/mech.sf2', os.path.abspath(mech), 0o644)]
            bank_desc = (
                'Salamander piano sound banks for exxpiano\n'
                ' piano.sf2 (piano) and mech.sf2 (mechanics), built from Salamander Grand Piano V3\n'
                ' by Alexander Holm (CC-BY).\n')
            built.append(build_package(
                args.out, 'exxpiano-soundfonts', args.version, 'exxpiano (>= %s)' % args.version,
                bank_desc, bank_files, [],
                {'postinst': read(src('packaging', 'debian', 'soundfonts-postinst'))}, [], mtime, tmp))
    for path in built:
        print('%s  (%.1f MB)' % (path, os.path.getsize(path) / 1e6))


if __name__ == '__main__':
    main()
