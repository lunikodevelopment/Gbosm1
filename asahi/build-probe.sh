#!/bin/bash
# Targeted native Linux build only; does not invoke the VM image/build scripts.
set -euo pipefail
[[ $(uname -s) == Linux ]] || { echo 'Run on Fedora Asahi Linux.' >&2; exit 1; }
ROOT=$(cd "$(dirname "$0")/.." && pwd)
OUT="$ROOT/work/asahi"
command -v cc >/dev/null || { echo 'Missing C compiler (Fedora package: gcc).' >&2; exit 1; }
command -v pkg-config >/dev/null || { echo 'Missing pkg-config (Fedora package: pkgconf-pkg-config).' >&2; exit 1; }
pkg-config --exists vulkan || { echo 'Missing Vulkan development files (Fedora package: vulkan-loader-devel).' >&2; exit 1; }
echo 'THIS STEP MODIFIES STORAGE'
echo "Creates/replaces only $OUT/vulkan-probe; source is retained. Remove that file to reverse."
mkdir -p "$OUT"
# pkg-config outputs compiler arguments, intentionally word split.
cc -std=c11 -D_POSIX_C_SOURCE=200809L -O2 -Wall -Wextra $(pkg-config --cflags vulkan) \
    "$ROOT/guest/guest_vulkan_probe.c" -o "$OUT/vulkan-probe" $(pkg-config --libs vulkan)
echo "Built $OUT/vulkan-probe; run as your ordinary user, without sudo."
