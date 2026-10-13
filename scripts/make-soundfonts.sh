#!/bin/bash
# Build the sound banks from the original Salamander Grand Piano V3 (run on a Mac/Linux, not on the Pi).
#
#   scripts/make-soundfonts.sh [-p PRESET] [-v SAMPLES] [-o OUTPUT_DIR] [-s SALAMANDER_DIR] [-u URL] [-n SFZ_NAME]
#
# -p  Preset of the piano bank (size = memory the bank takes on a Pi 3):
#       full      ~700 MB  14 layers, stereo, 12 s tails. The main one, for boards with 1 GB or more (gain 1.0)
#       lite      ~250 MB  6 layers, stereo, 9 s tails (gain 1.0)
#
# -v  Which published Salamander package to take the samples from (all CC-BY, from freepats.zenvoid.org):
#       44k16       WAV, 44.1 kHz 16 bit, 412 MB download.  Default. Tested on real data.
#       48k24       WAV, 48 kHz 24 bit, 1.26 GB download.   Converted to 44.1 kHz 16 bit while building.
#                   Tested only on a copy of the 44k16 set converted to this format, not on the real download.
#       48k24-flac  FLAC, 48 kHz 24 bit, 742 MB download (the 2020-06-02 package, .tar.gz). Experimental:
#                   its folder layout and SFZ name were not inspected; if the SFZ is not found, pass -n.
# -s  Use an already unpacked package instead of downloading (a folder containing the .sfz file).
# -u  Download this URL instead of the one picked by -v (any Salamander SFZ package).
# -n  Name of the SFZ file inside the package (default SalamanderGrandPianoV3Retuned.sfz).
#
# Needs: python3, ffmpeg (with ffprobe), curl, tar with xz/gzip support.
set -euo pipefail

PRESET=full
VERSION=44k16
OUT=./build
SRC=""
URL=""
SFZ_NAME=SalamanderGrandPianoV3Retuned.sfz
BASE='http://freepats.zenvoid.org/Piano/SalamanderGrandPiano'

while getopts "p:v:o:s:u:n:h" opt; do
  case "$opt" in
    p) PRESET="$OPTARG" ;;
    v) VERSION="$OPTARG" ;;
    o) OUT="$OPTARG" ;;
    s) SRC="$OPTARG" ;;
    u) URL="$OPTARG" ;;
    n) SFZ_NAME="$OPTARG" ;;
    h) sed -n '2,22p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) exit 1 ;;
  esac
done

case "$PRESET" in
  lite)     ARGS="--layers 2,5,8,10,13,15 --tail 9 --gamma 0.85 --peak-lo -17 --peak-hi -1.5 --veltrack 55" ;;
  full)     ARGS="--layers 1,2,4,5,6,7,8,9,10,11,12,13,15,16 --tail 12 --gamma 0.85 --peak-lo -17 --peak-hi -1.5 --veltrack 55" ;;
  *) echo "Unknown preset: $PRESET (full | lite)"; exit 1 ;;
esac

case "$VERSION" in
  44k16)      PKG_URL="$BASE/SalamanderGrandPianoV3+20161209_44khz16bit.tar.xz";   PKG_SIZE="412 MB" ;;
  48k24)      PKG_URL="$BASE/SalamanderGrandPianoV3+20161209_48khz24bit.tar.xz";   PKG_SIZE="1.26 GB" ;;
  48k24-flac) PKG_URL="$BASE/SalamanderGrandPiano-SFZ+FLAC-V3+20200602.tar.gz";    PKG_SIZE="742 MB" ;;
  *) echo "Unknown samples version: $VERSION (44k16 | 48k24 | 48k24-flac)"; exit 1 ;;
esac
[ -n "$URL" ] && { PKG_URL="$URL"; PKG_SIZE="unknown size"; }

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
for tool in python3 ffmpeg ffprobe; do
  command -v "$tool" >/dev/null || { echo "$tool not found (brew install ffmpeg / apt install ffmpeg)"; exit 1; }
done
mkdir -p "$OUT"

if [ -z "$SRC" ]; then
  SRC="$OUT/salamander-$VERSION"
  if [ ! -d "$SRC" ]; then
    ARCHIVE="$OUT/salamander-$VERSION.archive"
    echo "==> Downloading $PKG_URL ($PKG_SIZE)"
    curl -L -C - --fail -o "$ARCHIVE" "$PKG_URL"
    mkdir -p "$SRC"
    tar -xf "$ARCHIVE" -C "$SRC"
  fi
fi

SFZ="$(find "$SRC" -name "$SFZ_NAME" -print -quit)"
if [ -z "$SFZ" ]; then
  echo "$SFZ_NAME not found in $SRC. SFZ files there:"
  find "$SRC" -name '*.sfz' | head -10
  echo "Pass the right one with -n NAME."
  exit 1
fi
SFZ_DIR="$(dirname "$SFZ")"
# the sample paths written in the SFZ are relative to the SFZ file: check that the first one exists
FIRST="$(grep -m1 -o 'sample=[^ ]*' "$SFZ" | cut -d= -f2- | tr '\\' '/')"
[ -f "$SFZ_DIR/$FIRST" ] || { echo "The first sample of the SFZ ($FIRST) is missing next to $SFZ"; exit 1; }
echo "==> Source: $SFZ  (first sample: $FIRST)"

echo "==> Mechanics (mech.sf2, ~36 MB)"
python3 -I "$HERE/tools/build_mech_sf2.py" "$SFZ_DIR" "$OUT/mech.sf2" "$SFZ_NAME"

echo "==> Piano, preset $PRESET (a few minutes)"
# shellcheck disable=SC2086
python3 -I "$HERE/tools/build_piano_sf2.py" "$SFZ_DIR" "$OUT/piano-$PRESET.sf2" --sfz "$SFZ_NAME" $ARGS --name "Salamander $PRESET"

echo
ls -lh "$OUT"/mech.sf2 "$OUT/piano-$PRESET.sf2"
GAIN=1.0
echo
echo "Next (on the Pi, from the pi/ folder of the repository):"
echo "  sudo ./install.sh --piano piano-$PRESET.sf2 --mech mech.sf2 --gain $GAIN"
