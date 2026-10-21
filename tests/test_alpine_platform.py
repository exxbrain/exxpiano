"""The pinned Alpine base: the repositories, the official keys and their checksums in .github/alpine-image.env must agree.

Run from the repository root:   python3 -m unittest discover -s tests -v
"""

import hashlib
import os
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))


def read_env(path):
    out = {}
    with open(path, encoding='utf-8') as f:
        for raw in f:
            line = raw.strip()
            if line and not line.startswith('#'):
                key, _, value = line.partition('=')
                out[key] = value
    return out


class PinnedAlpineTests(unittest.TestCase):
    def setUp(self):
        self.env = read_env(os.path.join(ROOT, '.github', 'alpine-image.env'))
        with open(os.path.join(ROOT, 'alpine', 'repositories'), encoding='utf-8') as f:
            self.repos = [line.strip() for line in f if line.strip() and not line.startswith('#')]

    def test_the_repositories_are_one_branch_over_https(self):
        self.assertEqual(len(self.repos), 2)
        for repo, part in zip(self.repos, ('main', 'community')):
            self.assertEqual(repo, '%s/%s/%s' % (self.env['ALPINE_MIRROR'], self.env['ALPINE_BRANCH'], part))
            self.assertTrue(repo.startswith('https://'))

    def test_every_key_matches_its_pinned_checksum(self):
        folder = os.path.join(ROOT, 'alpine', 'keys')
        keys = sorted(os.listdir(folder))
        self.assertTrue(keys)
        pinned = {k: v for k, v in self.env.items() if k.startswith('ALPINE_KEY_')}
        self.assertEqual(len(pinned), len(keys))
        for name in keys:
            short = name.rsplit('-', 1)[1].split('.')[0].upper()
            with open(os.path.join(folder, name), 'rb') as f:
                digest = hashlib.sha256(f.read()).hexdigest()
            self.assertEqual(digest, pinned['ALPINE_KEY_%s_SHA256' % short], name)


if __name__ == '__main__':
    unittest.main()
