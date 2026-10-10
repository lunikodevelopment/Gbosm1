#!/bin/bash
# Graphics stage 3c: can the display driver take buffers allocated on the GPU driver?
set -uo pipefail
[[ $(uname -s) == Linux ]] || { echo 'Run on Fedora Asahi Linux.' >&2; exit 1; }
[[ $(id -u) != 0 ]] || { echo 'Run as your ordinary user, without sudo.' >&2; exit 1; }
ROOT=$(cd "$(dirname "$0")/.." && pwd)
command -v cc >/dev/null && command -v pkg-config >/dev/null && pkg-config --exists libdrm || {
    echo 'Need gcc, pkgconf-pkg-config and libdrm-devel.' >&2; exit 1; }
echo 'THIS STEP MODIFIES STORAGE'
echo "Creates one new $ROOT/work/asahi/dumb-crossimport-* directory with the probe binary and report."
echo 'Allocates dumb buffers (up to 16 MB each) for the run only and removes the test framebuffers it creates.'
echo 'No modeset, no commit, no DRM master; GNOME is unaffected. Delete the new directory to reverse.'
mkdir -p "$ROOT/work/asahi"
OUT=$(mktemp -d "$ROOT/work/asahi/dumb-crossimport-XXXXXXXX")
echo "Results: $OUT"
exec > >(tee "$OUT/report.txt") 2>&1
cc -std=gnu11 -O2 -Wall -Wextra $(pkg-config --cflags libdrm) "$ROOT/asahi/probe-dumb-crossimport.c" \
    -o "$OUT/probe-dumb-crossimport" $(pkg-config --libs libdrm) 2>&1 || { echo "build failed"; echo "Send $OUT/report.txt"; exit 1; }
"$OUT/probe-dumb-crossimport" 2>&1
echo "probe exit status: $?"
echo "Send $OUT/report.txt"
