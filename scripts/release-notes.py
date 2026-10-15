#!/usr/bin/env python3
"""The description of a GitHub Release: .github/release-description.md plus the section of CHANGELOG.md for this version.

  python3 scripts/release-notes.py 1.10.10 > notes.md

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


def notes(version, root=ROOT):
    with open(os.path.join(root, 'CHANGELOG.md'), encoding='utf-8') as f:
        body = section(f.read(), version)
    if not body:
        raise ValueError('CHANGELOG.md has no section "## %s" (or it is empty)' % version)
    with open(os.path.join(root, '.github', 'release-description.md'), encoding='utf-8') as f:
        description = f.read().rstrip('\n')
    return '%s\n\n## What changed in %s\n\n%s\n' % (description, version, body)


if __name__ == '__main__':
    try:
        if len(sys.argv) > 1:
            version = sys.argv[1]
        else:
            with open(os.path.join(ROOT, 'VERSION')) as f:
                version = f.read().strip()
        sys.stdout.write(notes(version))
    except (ValueError, OSError) as exc:
        print('release-notes:', exc, file=sys.stderr)
        sys.exit(1)
