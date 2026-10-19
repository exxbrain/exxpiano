# The pinned Alpine base

The image and the packages are built only from what this folder names:

- `repositories`: the branch (`v3.24`) and the mirror the builder installs from. Both lines name the same branch, over HTTPS.
- `keys/`: the official signing keys of Alpine for aarch64. `apk` checks the signature of every index and package against them; a package
  that does not match stops the build. Their SHA-256 are written in `.github/alpine-image.env` and checked by `tests/test_alpine_platform.py`.

A stable branch of Alpine replaces packages with fixed builds and keeps no old versions, so two builds on different days can differ. The
manifest of every image lists the exact versions it was made from. To move to a newer branch, change both lines of `repositories`,
`ALPINE_BRANCH` and, if Alpine added keys, the keys and their checksums.
