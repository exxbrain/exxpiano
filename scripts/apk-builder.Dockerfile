# The environment that builds the Alpine packages of exxpiano (scripts/build-apks.py builds this once; Docker keeps it for the next builds).
FROM alpine:3.24
RUN apk add --no-cache alpine-sdk sudo \
    && adduser -D builder && addgroup builder abuild \
    && echo "builder ALL=(ALL) NOPASSWD: ALL" > /etc/sudoers.d/builder
