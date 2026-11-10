#!/usr/bin/env bash
# Convert an app-icon design (SVG or a single ≥1024 px PNG) into the macOS
# `.icns` bundle consumed by `scripts/build_nuitka.sh --icon`.
#
# Author the icon in Inkscape (or any vector editor) at 1024×1024 px, then:
#
#   scripts/make_app_icon.sh build/fea_toolkit.svg     # → build/fea_toolkit.icns
#   scripts/make_app_icon.sh build/fea_toolkit.png     # a single ≥1024 px PNG
#
#   ICON_OUT=build/FeaToolkit.icns scripts/make_app_icon.sh icon.svg
#   INKSCAPE=/path/to/inkscape scripts/make_app_icon.sh icon.svg
#
# macOS-only: uses the built-in `sips` + `iconutil`.  SVG input is rasterised
# by Inkscape's CLI; the script looks for `inkscape` on PATH and falls back to
# the standard /Applications/Inkscape.app bundle location.
set -euo pipefail

INPUT="${1:?usage: make_app_icon.sh <icon.svg|icon.png>}"
OUT="${ICON_OUT:-build/fea_toolkit.icns}"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

MASTER="$WORK/master.png"
# Lowercase the extension portably (macOS ships Bash 3.2, which lacks ${var,,}).
EXT="$(printf '%s' "${INPUT##*.}" | tr '[:upper:]' '[:lower:]')"
if [[ "$EXT" == "svg" ]]; then
    INKSCAPE="${INKSCAPE:-}"
    if [[ -z "$INKSCAPE" ]]; then
        if command -v inkscape >/dev/null 2>&1; then
            INKSCAPE="inkscape"
        elif [[ -x /Applications/Inkscape.app/Contents/MacOS/inkscape ]]; then
            INKSCAPE="/Applications/Inkscape.app/Contents/MacOS/inkscape"
        else
            echo "make_app_icon.sh: inkscape not found on PATH or in /Applications/Inkscape.app." >&2
            echo "Install Inkscape or set INKSCAPE=/path/to/inkscape." >&2
            exit 1
        fi
    fi
    "$INKSCAPE" --export-type=png --export-filename="$MASTER" \
        --export-width=1024 --export-height=1024 "$INPUT"
else
    cp "$INPUT" "$MASTER"
fi

# `sips` upscales silently; warn against a blurry master.
WIDTH="$(sips -g pixelWidth "$MASTER" | awk '/pixelWidth/ {print $2}')"
HEIGHT="$(sips -g pixelHeight "$MASTER" | awk '/pixelHeight/ {print $2}')"
if [[ "$WIDTH" -lt 1024 || "$HEIGHT" -lt 1024 ]]; then
    echo "make_app_icon.sh: warning — master is ${WIDTH}x${HEIGHT}px; 1024×1024 or larger is recommended." >&2
fi

ICONSET="$WORK/fea_toolkit.iconset"
mkdir -p "$ICONSET"

# `iconutil` wants one PNG per logical size plus its @2x retina variant.
sips -z 16   16   "$MASTER" --out "$ICONSET/icon_16x16.png"      >/dev/null
sips -z 32   32   "$MASTER" --out "$ICONSET/icon_16x16@2x.png"   >/dev/null
sips -z 32   32   "$MASTER" --out "$ICONSET/icon_32x32.png"      >/dev/null
sips -z 64   64   "$MASTER" --out "$ICONSET/icon_32x32@2x.png"   >/dev/null
sips -z 128  128  "$MASTER" --out "$ICONSET/icon_128x128.png"    >/dev/null
sips -z 256  256  "$MASTER" --out "$ICONSET/icon_128x128@2x.png" >/dev/null
sips -z 256  256  "$MASTER" --out "$ICONSET/icon_256x256.png"    >/dev/null
sips -z 512  512  "$MASTER" --out "$ICONSET/icon_256x256@2x.png" >/dev/null
sips -z 512  512  "$MASTER" --out "$ICONSET/icon_512x512.png"    >/dev/null
sips -z 1024 1024 "$MASTER" --out "$ICONSET/icon_512x512@2x.png" >/dev/null

mkdir -p "$(dirname "$OUT")"
iconutil -c icns "$ICONSET" -o "$OUT"
echo "make_app_icon.sh: wrote $OUT"
