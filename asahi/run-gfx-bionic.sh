#!/bin/bash
# Graphics stage 3f: the Android-built patched minigbm and the Android-built Honeykrisp in one
# Bionic process: minigbm allocates, Honeykrisp imports, CPU and GPU check each other's writes.
# Reuses the stage 3e run directory's linker and libraries; downloads and builds nothing but the probe.
set -uo pipefail
[[ $(uname -s) == Linux ]] || { echo 'Run on Fedora Asahi Linux.' >&2; exit 1; }
[[ $(id -u) != 0 ]] || { echo 'Run as your ordinary user, without sudo.' >&2; exit 1; }
ROOT=$(cd "$(dirname "$0")/.." && pwd)
W=$ROOT/work/asahi
command -v clang >/dev/null && command -v ld.lld >/dev/null || { echo 'Missing clang or ld.lld.' >&2; exit 1; }
HK=$(ls -dt "$W"/honeykrisp-android-* 2>/dev/null | while read -r d; do [[ -e $d/vulkan.asahi.so ]] && { echo "$d"; break; }; done)
RUN3E=$(ls -dt "$W"/minigbm-bionic-* 2>/dev/null | while read -r d; do
    [[ -e $d/lib64/libminigbm.so && -e $d/lib64/vulkan.asahi.so && -e $d/linker64 ]] \
        && grep -q 'MGBM_BIONIC cpu_readback=PASS' "$d/report.txt" 2>/dev/null && { echo "$d"; break; }; done)
for v in HK RUN3E; do [[ -n ${!v} ]] || { echo "Missing earlier result for $v (stage 2c and a passed stage 3e run are required)." >&2; exit 1; }; done
LIBDRM_H=$(find "$HK" -name xf86drm.h -path '*libdrm*' 2>/dev/null | head -1)
LIBDRM_SO=$(ls "$HK"/build/subprojects/libdrm-*/libdrm.so 2>/dev/null | head -1)
[[ -n $LIBDRM_H && -n $LIBDRM_SO ]] || { echo "libdrm headers or library not found under $HK." >&2; exit 1; }
LIBDRM_SRC=$(dirname "$LIBDRM_H")
echo 'THIS STEP MODIFIES STORAGE'
echo "Creates one new $W/gfx-bionic-* directory: a copy of the stage 3e library set, the probe and report.txt."
echo 'Earlier results are only read. One 256x256 dumb buffer is allocated on the display card node for the run only.'
echo 'No modeset, no DRM master. Delete the new directory to reverse.'
OUT=$(mktemp -d "$W/gfx-bionic-XXXXXXXX")
echo "Results: $OUT"
exec > >(tee "$OUT/report.txt") 2>&1
echo "Stage 3e run (libraries): $RUN3E"
TOOL="clang --target=aarch64-linux-android35 --sysroot=$HK/ndk/sysroot -resource-dir=$HK/clang-resource -fuse-ld=lld -Wl,-z,max-page-size=16384"
echo '== Build probe'
if $TOOL -O2 -Wall -Wextra -I"$LIBDRM_SRC" -I"$LIBDRM_SRC/include/drm" "$ROOT/asahi/probe-gfx-bionic.c" \
        -o "$OUT/probe-gfx-bionic" -L"$(dirname "$LIBDRM_SO")" -ldrm -ldl 2>&1; then echo 'probe build exit status: 0'; else
    echo "probe build failed. Send $OUT/report.txt"; exit 1; fi
cp -a "$RUN3E/lib64" "$OUT/lib64"
cp -a "$RUN3E/linker64" "$OUT/linker64"
chmod 700 "$OUT/linker64" "$OUT/probe-gfx-bionic"
echo '== Run under the image Bionic linker'
(cd "$OUT" && env -i PATH=/usr/bin:/bin LANG=C LD_LIBRARY_PATH="$OUT/lib64" "$OUT/linker64" "$OUT/probe-gfx-bionic" \
    "$OUT/lib64/libminigbm.so" "$OUT/lib64/vulkan.asahi.so" 2>&1 </dev/null)
echo "probe exit status: $?"
echo "Send $OUT/report.txt"
