#!/bin/bash
# Graphics stage 1: build and run the GBM to Honeykrisp dma-buf probe; no KMS, no display.
set -euo pipefail
[[ $(uname -s) == Linux ]] || { echo 'Run on Fedora Asahi Linux.' >&2; exit 1; }
[[ $(id -u) != 0 ]] || { echo 'Run as your ordinary user, without sudo.' >&2; exit 1; }
ROOT=$(cd "$(dirname "$0")/.." && pwd)
command -v cc >/dev/null || { echo 'Missing C compiler (Fedora package: gcc).' >&2; exit 1; }
command -v pkg-config >/dev/null || { echo 'Missing pkg-config (Fedora package: pkgconf-pkg-config).' >&2; exit 1; }
for pc in vulkan:vulkan-loader-devel gbm:mesa-libgbm-devel libdrm:libdrm-devel; do
    pkg-config --exists "${pc%%:*}" || { echo "Missing ${pc%%:*} development files (Fedora package: ${pc#*:})." >&2; exit 1; }
done
echo 'THIS STEP MODIFIES STORAGE'
echo "Creates one new $ROOT/work/asahi/gbm-dmabuf-* directory with the probe binary and logs."
echo 'Allocates up to four 256x256 buffers in GPU memory for the run only. Delete the new directory to reverse.'
mkdir -p "$ROOT/work/asahi"
RESULTS=$(mktemp -d "$ROOT/work/asahi/gbm-dmabuf-XXXXXXXX")
echo "Results: $RESULTS"
# pkg-config outputs compiler arguments, intentionally word split.
if cc -std=c11 -D_POSIX_C_SOURCE=200809L -O2 -Wall -Wextra $(pkg-config --cflags vulkan gbm libdrm) \
    "$ROOT/asahi/probe-gbm-dmabuf.c" -o "$RESULTS/probe-gbm-dmabuf" $(pkg-config --libs vulkan gbm libdrm) \
    >"$RESULTS/build.log" 2>&1; then status=0; else status=$?; fi
cat "$RESULTS/build.log"
echo "build exit status: $status" | tee "$RESULTS/status.txt"
[[ $status == 0 ]] || { echo "Stopped at build. Send $RESULTS/build.log"; exit "$status"; }
if "$RESULTS/probe-gbm-dmabuf" >"$RESULTS/probe.log" 2>&1; then status=0; else status=$?; fi
cat "$RESULTS/probe.log"
echo "probe exit status: $status" | tee -a "$RESULTS/status.txt"
echo "Send $RESULTS/probe.log and $RESULTS/status.txt"
exit "$status"
