# The environment that makes the SD card image (build-sd-image.py builds it once; any Alpine aarch64 machine with root can run
# sd-image-inner.sh directly). The image is made from files: mkfs.ext4 -d and mtools need no loop device and no privileged container.
FROM alpine:3.24
RUN apk add --no-cache bash coreutils dosfstools e2fsprogs e2fsprogs-extra mtools python3 sfdisk xz
