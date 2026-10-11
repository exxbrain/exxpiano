# The Linux environment that makes the SD card image (used by build-sd-image.py on a Mac; any Linux with root can run sd-image-inner.sh directly).
FROM debian:trixie
RUN apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      util-linux parted e2fsprogs dosfstools xz-utils ca-certificates python3 rsync file \
    && rm -rf /var/lib/apt/lists/*
