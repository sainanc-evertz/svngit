#!/bin/sh
# Record the README demos. Run from the repository root:
#
#     sh docs/demo/make.sh
#
# Needs svn, vhs and ffmpeg.
#
# vhs captures the frames but does not encode them here: its own ffmpeg call
# fails silently against ffmpeg 9, reporting success and writing nothing. So
# the tape asks for a frame directory and this script does the encode, which
# also lets us tune the palette rather than take vhs's defaults.

set -eu

ROOT=$(cd -- "$(dirname -- "$0")/../.." && pwd)
DEMO_ROOT=/tmp/svngit-demo
FRAMES="$DEMO_ROOT/frames"
OUT="$ROOT/docs/images/svngit-demo.gif"

command -v vhs >/dev/null || { echo "vhs not found (brew install vhs)" >&2; exit 1; }
command -v ffmpeg >/dev/null || { echo "ffmpeg not found" >&2; exit 1; }

echo "Building the demo repository..."
DEMO_ROOT="$DEMO_ROOT" sh "$ROOT/docs/demo/setup.sh"

echo "Recording..."
rm -rf "$FRAMES"
(cd "$ROOT" && vhs docs/demo/demo.tape >/dev/null 2>&1)

frames=$(ls "$FRAMES"/frame-text-*.png 2>/dev/null | wc -l | tr -d ' ')
[ "$frames" -gt 0 ] || { echo "no frames captured" >&2; exit 1; }
echo "Encoding $frames frames..."

# The cursor is a separate layer; overlay it, then build a palette from the
# whole clip so text stays legible after quantisation.
ffmpeg -y -loglevel error \
    -framerate 50 -i "$FRAMES/frame-text-%05d.png" \
    -framerate 50 -i "$FRAMES/frame-cursor-%05d.png" \
    -filter_complex "[0][1]overlay,fps=18,split[a][b];\
[a]palettegen=max_colors=128:stats_mode=diff[p];\
[b][p]paletteuse=dither=bayer:bayer_scale=4" \
    "$OUT"

echo "Wrote $OUT ($(du -h "$OUT" | cut -f1))"
