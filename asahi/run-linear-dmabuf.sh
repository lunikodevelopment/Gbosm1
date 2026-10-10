#!/bin/bash
# Graphics stage 3b: do non-GBM linear allocations work with Honeykrisp, and what does
# minigbm's existing "dumb" backend and node enumeration look like? No KMS, no master.
set -uo pipefail
[[ $(uname -s) == Linux ]] || { echo 'Run on Fedora Asahi Linux.' >&2; exit 1; }
[[ $(id -u) != 0 ]] || { echo 'Run as your ordinary user, without sudo.' >&2; exit 1; }
ROOT=$(cd "$(dirname "$0")/.." && pwd)
command -v cc >/dev/null || { echo 'Missing C compiler (Fedora package: gcc).' >&2; exit 1; }
command -v pkg-config >/dev/null || { echo 'Missing pkg-config (Fedora package: pkgconf-pkg-config).' >&2; exit 1; }
for pc in vulkan:vulkan-loader-devel libdrm:libdrm-devel; do
    pkg-config --exists "${pc%%:*}" || { echo "Missing ${pc%%:*} development files (Fedora package: ${pc#*:})." >&2; exit 1; }
done
echo 'THIS STEP MODIFIES STORAGE'
echo "Creates one new $ROOT/work/asahi/linear-dmabuf-* directory with the probe binary and logs."
echo 'Allocates a few 256x256 buffers (dumb buffers on the display and GPU card nodes, dma-heap buffers) for the run only.'
echo 'Delete the new directory to reverse. No modeset, no DRM master; GNOME is unaffected.'
mkdir -p "$ROOT/work/asahi"
OUT=$(mktemp -d "$ROOT/work/asahi/linear-dmabuf-XXXXXXXX")
echo "Results: $OUT"
exec > >(tee "$OUT/report.txt") 2>&1

echo '== Device node permissions'
ls -l /dev/dri /dev/dma_heap 2>&1
id

echo '== Build'
if cc -std=gnu11 -O2 -Wall -Wextra $(pkg-config --cflags vulkan libdrm) "$ROOT/asahi/probe-linear-dmabuf.c" \
    -o "$OUT/probe-linear-dmabuf" $(pkg-config --libs vulkan libdrm) 2>&1; then echo 'build exit status: 0'; else
    echo "build exit status: $?"; echo "Send $OUT/report.txt"; exit 1; fi

echo '== Probe'
"$OUT/probe-linear-dmabuf" 2>&1
echo "probe exit status: $?"

echo
echo '== minigbm source excerpts (from the stage 3a clones; read only)'
SRC=$(ls -d "$ROOT"/work/asahi/minigbm-src-*/aosp 2>/dev/null | tail -1)
if [[ -z $SRC ]]; then echo 'no stage 3a clone found; skipping'; else
    echo "-- $SRC/dumb_driver.c (lines 1-66)"
    sed -n 1,66p "$SRC/dumb_driver.c"
    echo "-- $SRC/cros_gralloc/cros_gralloc_driver.cc (lines 95-215)"
    sed -n 95,215p "$SRC/cros_gralloc/cros_gralloc_driver.cc"
fi
echo
echo "Send $OUT/report.txt"
