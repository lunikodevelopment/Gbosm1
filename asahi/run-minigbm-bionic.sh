#!/bin/bash
# Graphics stage 3e: cross-build the patched minigbm for Android with the stage 2c toolchain and run
# one allocation under the image's Bionic linker. Reuses earlier results; downloads nothing.
set -uo pipefail
[[ $(uname -s) == Linux ]] || { echo 'Run on Fedora Asahi Linux.' >&2; exit 1; }
[[ $(id -u) != 0 ]] || { echo 'Run as your ordinary user, without sudo.' >&2; exit 1; }
ROOT=$(cd "$(dirname "$0")/.." && pwd)
W=$ROOT/work/asahi
for c in clang ld.lld make python3 readelf; do command -v $c >/dev/null || { echo "Missing $c." >&2; exit 1; }; done
# Newest earlier result directory matching a glob that also contains a marker file.
newest() { local d; for d in $(ls -dt $W/$1 2>/dev/null); do [[ -e $d/$2 ]] && { echo "$d"; return; }; done; }
HK=$(newest 'honeykrisp-android-*' vulkan.asahi.so)
RUN2D=$(newest 'honeykrisp-bionic-*' linker64)
PATCHED=$(newest 'minigbm-apple-*' minigbm/dumb_driver.c)
for v in HK RUN2D PATCHED; do [[ -n ${!v} ]] || { echo "Missing earlier result for $v (stages 2c, 2d and 3d are required)." >&2; exit 1; }; done
python3 - "$RUN2D/summary.json" <<'PY' || { echo 'The newest stage 2d result did not pass; rerun stage 2d first.' >&2; exit 1; }
import json, sys
sys.exit(0 if json.load(open(sys.argv[1])).get('exit_status') == 0 else 1)
PY
LIBDRM_H=$(find "$HK" -name xf86drm.h -path '*libdrm*' 2>/dev/null | head -1)
LIBDRM_SO=$(ls "$HK"/build/subprojects/libdrm-*/libdrm.so 2>/dev/null | head -1)
[[ -n $LIBDRM_H && -n $LIBDRM_SO ]] || { echo "libdrm headers or library not found under $HK." >&2; exit 1; }
LIBDRM_SRC=$(dirname "$LIBDRM_H")
echo 'THIS STEP MODIFIES STORAGE'
echo "Creates one new $W/minigbm-bionic-* directory: a copy of the patched minigbm source, its Android"
echo 'build output, a run directory with the image linker and libraries, the probe and report.txt.'
echo 'Earlier results are only read. One 256x256 dumb buffer is allocated on the display card node for the run only.'
echo 'No modeset, no DRM master. Delete the new directory to reverse.'
OUT=$(mktemp -d "$W/minigbm-bionic-XXXXXXXX")
echo "Results: $OUT"
exec > >(tee "$OUT/report.txt") 2>&1
echo "Stage 2c build: $HK"
echo "Stage 2d run:   $RUN2D"
echo "Patched source: $PATCHED/minigbm"
echo "libdrm headers: $LIBDRM_SRC ; library: $LIBDRM_SO"
cp -a "$PATCHED/minigbm" "$OUT/minigbm"
TOOL="clang --target=aarch64-linux-android35 --sysroot=$HK/ndk/sysroot -resource-dir=$HK/clang-resource -fuse-ld=lld -Wl,-z,max-page-size=16384"
INC="-I$LIBDRM_SRC -I$LIBDRM_SRC/include/drm -I$ROOT/asahi/android-shims"

echo '== Android-specific includes and calls in the minigbm sources (to size the shim work)'
(cd "$OUT/minigbm" && grep -n -E '^#\s*include\s*<(cutils|android|log|hardware|system|utils|sync|vndk|sys/system_properties)' *.c *.h cros_gralloc/*.c cros_gralloc/*.cc 2>/dev/null | head -40
 echo '-- identifiers:'; grep -ohE '\b(ALOG[A-Z_]*|LOG_[A-Z_]+|property_[a-z0-9_]+|__android_[a-z_]+|__system_property_[a-z_]+|sync_[a-z_]+)\b' *.c *.h 2>/dev/null | sort | uniq -c)
echo '== Build libminigbm for Android (make -k, serial so errors stay readable)'
# Flags go inside CC: variables given on the make command line would replace the Makefile's own += flags.
# PKG_CONFIG=true makes every pkg-config probe in the Makefile print nothing, so no host paths leak in.
# -Qunused-arguments: common.mk compiles with -Werror, and clang flags the linker-only -Wl option on compile steps.
if (cd "$OUT/minigbm" && make -k OUT="$OUT/minigbm-build" CC="$TOOL -Qunused-arguments" PKG_CONFIG=true \
        PC_CFLAGS="$INC" PC_LIBS="-L$(dirname "$LIBDRM_SO") -ldrm") >"$OUT/make.log" 2>&1; then
    echo 'make exit status: 0'; else echo "make exit status: $?"; fi
grep -E 'error|Error' "$OUT/make.log" | sort -u | head -40
tail -8 "$OUT/make.log"
LIB=$(find "$OUT/minigbm-build" "$OUT/minigbm" -name 'libminigbm.so*' -type f 2>/dev/null | head -1)
[[ -n $LIB ]] || { echo "Stopped: no libminigbm built. Send $OUT/report.txt"; exit 1; }
echo "Built: $LIB"
readelf -h "$LIB" | grep -E 'Class|Machine'
readelf -lW "$LIB" | grep -E 'LOAD' 
readelf -d "$LIB" | grep -E 'NEEDED|SONAME'
readelf -sW --dyn-syms "$LIB" | grep -E ' (gbm_create_device|gbm_bo_create|gbm_bo_get_fd)$' || true

echo '== Build probe'
if $TOOL -O2 -Wall -Wextra $INC "$ROOT/asahi/probe-minigbm-bionic.c" -o "$OUT/probe-minigbm-bionic" \
        -L"$(dirname "$LIBDRM_SO")" -ldrm -ldl 2>&1; then echo 'probe build exit status: 0'; else
    echo "probe build failed. Send $OUT/report.txt"; exit 1; fi

echo '== Run under the image Bionic linker'
cp -a "$RUN2D/lib64" "$OUT/lib64"
cp -a "$RUN2D/linker64" "$OUT/linker64"
cp "$LIB" "$OUT/lib64/libminigbm.so"
cp "$LIBDRM_SO" "$OUT/lib64/libdrm.so"
for n in $(readelf -d "$OUT/lib64/libminigbm.so" | sed -n 's/.*NEEDED.*\[\(.*\)\]/\1/p'); do
    [[ -e $OUT/lib64/$n || $n == ld-android.so ]] || echo "WARNING: libminigbm needs $n, not in the stage 2d library set"
done
chmod 700 "$OUT/linker64" "$OUT/probe-minigbm-bionic"
(cd "$OUT" && env -i PATH=/usr/bin:/bin LANG=C LD_LIBRARY_PATH="$OUT/lib64" "$OUT/linker64" "$OUT/probe-minigbm-bionic" "$OUT/lib64/libminigbm.so" 2>&1 </dev/null)
echo "probe exit status: $?"
echo "Send $OUT/report.txt"
