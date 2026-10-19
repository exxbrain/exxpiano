#!/usr/bin/env python3
"""The description of a GitHub Release: .github/release-description.md plus the section of CHANGELOG.md for this version.

  python3 scripts/release-notes.py 1.10.10 > notes.md
  python3 scripts/release-notes.py 1.10.10 --mirror https://dl.example.org/v1.10.10 dist/sd/*.zip > notes.md
  python3 scripts/release-notes.py --existing old.md --mirror https://dl.example.org/v1.10.10 dist/sd/*.zip > notes.md

  python3 scripts/release-notes.py --existing old.md --remove-mirror > notes.md

--existing takes a published description as it is and only puts the mirror links into it (replacing earlier ones), or with
--remove-mirror takes them out (the version was deleted from the mirror).

Fails (exit 1) when CHANGELOG.md has no `## <VERSION>` section, so a release cannot go out without its changes described.
"""

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def section(changelog, version):
    """The text under `## <version>` (the heading may continue after the version) up to the next `## `; None when there is none."""
    m = re.search(r'^## %s(?:[ \t(].*)?$\n(.*?)(?=^## |\Z)' % re.escape(version), changelog, re.M | re.S)
    return m.group(1).strip() if m else None


def mirror_section(base_url, files):
    """Links to the same files on the download mirror (the Cloudflare R2 bucket the release workflow copies them to)."""
    base_url = base_url.rstrip('/')
    links = '\n'.join('- [%s](%s/%s)' % (name, base_url, name) for name in sorted(os.path.basename(f) for f in files))
    return (
        '## Faster download (mirror)\n\n'
        'If the download from GitHub is slow, the same files are on a mirror:\n\n'
        '%s\n\n'
        'They are the same files as in this release: check them against `SHA256SUMS`.' % links
    )


def remove_mirror(text):
    """The description without its mirror section."""
    return re.sub(r'^## Faster download \(mirror\)\n.*?(?=^## |\Z)', '', text, flags=re.M | re.S).rstrip('\n') + '\n'


def add_mirror(text, base_url, files):
    """The description with the mirror section before `## What changed` (at the end without it); an earlier mirror section is replaced."""
    text = remove_mirror(text).rstrip('\n')
    section_text = mirror_section(base_url, files)
    m = re.search(r'^## What changed', text, re.M)
    if not m:
        return '%s\n\n%s\n' % (text, section_text)
    return '%s\n\n%s\n\n%s\n' % (text[: m.start()].rstrip('\n'), section_text, text[m.start() :].rstrip('\n'))


def notes(version, root=ROOT, mirror=None, mirror_files=()):
    with open(os.path.join(root, 'CHANGELOG.md'), encoding='utf-8') as f:
        body = section(f.read(), version)
    if not body:
        raise ValueError('CHANGELOG.md has no section "## %s" (or it is empty)' % version)
    with open(os.path.join(root, '.github', 'release-description.md'), encoding='utf-8') as f:
        description = f.read().rstrip('\n')
    text = '%s\n\n## What changed in %s\n\n%s\n' % (description, version, body)
    return add_mirror(text, mirror, mirror_files) if mirror and mirror_files else text


if __name__ == '__main__':
    try:
        args = sys.argv[1:]
        mirror, mirror_files = None, ()
        if '--mirror' in args:
            i = args.index('--mirror')
            mirror, mirror_files = args[i + 1], args[i + 2 :]
            args = args[:i]
        if args[:1] == ['--existing'] and args[2:] == ['--remove-mirror']:
            with open(args[1], encoding='utf-8') as f:
                sys.stdout.write(remove_mirror(f.read()))
            sys.exit(0)
        if args[:1] == ['--existing']:
            if not mirror_files:
                raise ValueError('--existing needs --mirror URL FILE...')
            with open(args[1], encoding='utf-8') as f:
                sys.stdout.write(add_mirror(f.read(), mirror, mirror_files))
            sys.exit(0)
        if args:
            version = args[0]
        else:
            with open(os.path.join(ROOT, 'VERSION')) as f:
                version = f.read().strip()
        sys.stdout.write(notes(version, mirror=mirror, mirror_files=mirror_files))
    except (ValueError, OSError) as exc:
        print('release-notes:', exc, file=sys.stderr)
        sys.exit(1)
