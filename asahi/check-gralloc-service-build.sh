#!/bin/bash
# Graphics stage 3k: syntax-check the AOSP minigbm allocator service (cros_gralloc/aidl), the stable-C
# mapper (mapper_stablec) and the cros_gralloc core for Android arm64, using the stage 2c NDK sysroot,
# the stage 3i source trees and the stage 3j generated AIDL code. Compiles nothing to object files and
# runs nothing; it lists which headers are still missing and where each one exists in the fetched trees.
set -uo pipefail
[[ $(uname -s) == Linux ]] || { echo 'Run on Fedora Asahi Linux.' >&2; exit 1; }
[[ $(id -u) != 0 ]] || { echo 'Run as your ordinary user, without sudo.' >&2; exit 1; }
command -v clang++ >/dev/null || { echo 'Missing clang++.' >&2; exit 1; }
ROOT=$(cd "$(dirname "$0")/.." && pwd)
W=$ROOT/work/asahi
newest() { local d; for d in $(ls -dt $W/$1 2>/dev/null); do [[ -e $d/$2 ]] && { echo "$d"; return; }; done; }
HK=$(newest 'honeykrisp-android-*' vulkan.asahi.so)
SRC=$(newest 'aosp-gralloc-src-*' system_libbase/include)
GEN=$(newest 'aidl-build-*' generated/include/aidl/android/hardware/graphics/allocator/IAllocator.h)
MG=$(ls -d "$W"/minigbm-apple-*/minigbm 2>/dev/null | tail -1)
LIBDRM_H=$(find "$HK" -name xf86drm.h -path '*libdrm*' 2>/dev/null | head -1)
for v in HK SRC GEN MG LIBDRM_H; do [[ -n ${!v} ]] || { echo "Missing earlier result for $v (stages 2c, 3d, 3i and 3j are required)." >&2; exit 1; }; done
echo 'THIS STEP MODIFIES STORAGE'
echo "Creates one new $W/gralloc-check-* directory holding per-file compiler logs and report.txt."
echo 'Earlier results are only read; nothing is built into objects, installed or run. Delete the directory to reverse.'
OUT=$(mktemp -d "$W/gralloc-check-XXXXXXXX")
echo "Results: $OUT"
exec > >(tee "$OUT/report.txt") 2>&1
echo "NDK/HK: $HK"; echo "AOSP sources: $SRC"; echo "Generated AIDL: $GEN"; echo "minigbm: $MG"
EXTRA=$(ls -dt "$W"/aosp-gralloc-extra-* 2>/dev/null | head -1)
# Every include-like directory in the fetched trees, so missing headers show up as "found here, not on the path".
INCS=$(find "$SRC" $EXTRA -type d \( -name include -o -name include_cpp -o -name include_platform -o -name include_ndk -o -name include_sdk \) \
        -not -path '*/.git/*' 2>/dev/null | sort)
echo "Include directories found in the fetched trees: $(echo "$INCS" | wc -l)"
IFLAGS=
for d in $INCS; do IFLAGS="$IFLAGS -I$d"; done
LIBDRM_SRC=$(dirname "$LIBDRM_H")
TOOL="clang++ --target=aarch64-linux-android35 --sysroot=$HK/ndk/sysroot -resource-dir=$HK/clang-resource -std=gnu++20 -fsyntax-only -ferror-limit=0 -w"
TOOL_PP="clang++ --target=aarch64-linux-android35 --sysroot=$HK/ndk/sysroot -resource-dir=$HK/clang-resource -std=gnu++20 -w"
HIDL=$(newest 'hidl-build-*' generated/android/hardware/graphics/mapper/4.0/IMapper.h)
[[ -n $HIDL ]] && echo "Generated HIDL headers: $HIDL"
HIDLINC=; [[ -n $HIDL ]] && HIDLINC="-I$HIDL/generated -I$HIDL/system_libhwbinder/include"
FLAGS="-I$GEN/generated/include $HIDLINC -I$ROOT/asahi/android-shims -I$LIBDRM_SRC -I$LIBDRM_SRC/include/drm -I$MG -I$MG/cros_gralloc $IFLAGS"
FILES="cros_gralloc/aidl/Allocator.cpp cros_gralloc/aidl/Main.cpp cros_gralloc/mapper_stablec/Mapper.cpp"
for f in $(cd "$MG" && ls cros_gralloc/*.cc 2>/dev/null); do FILES="$FILES $f"; done
for f in $FILES; do
    n=$(echo "$f" | tr '/' '_')
    [[ -f $MG/$f ]] || { echo "NOFILE $f"; continue; }
    if $TOOL $FLAGS -D_GNU_SOURCE -DDRV_APPLE "$MG/$f" >"$OUT/$n.log" 2>&1; then echo "SYNTAX_OK $f"
    else echo "SYNTAX_FAIL $f ($(grep -c 'error' "$OUT/$n.log") error lines)"; fi
done
echo
echo '== Missing headers (preprocessor pass that keeps going past missing headers), with where each exists in the fetched trees'
: > "$OUT/missing.txt"
for f in $FILES; do
    [[ -f $MG/$f ]] || continue
    $TOOL_PP $FLAGS -D_GNU_SOURCE -DDRV_APPLE -MM -MG "$MG/$f" 2>/dev/null | tr ' \\' '\n\n' | grep -v -E '^$|:$|\.o$' \
        | grep -v '^/' | sed "s|^|$f |" >> "$OUT/missing.txt"
done
awk '{print $2}' "$OUT/missing.txt" | sort | uniq -c | sort -rn | while read -r cnt h; do
    where=$(find "$SRC" $EXTRA -path "*/$h" -not -path '*/.git/*' 2>/dev/null | head -3 | sed "s|$SRC/||" | tr '\n' ' ')
    echo "$h (x$cnt): ${where:-NOT IN FETCHED TREES}"
done
echo
echo '== Other first errors per file'
for f in $FILES; do
    n=$(echo "$f" | tr '/' '_'); [[ -f $OUT/$n.log ]] || continue
    grep -E 'error' "$OUT/$n.log" | grep -v 'file not found' | head -5 | sed "s|^|$f: |"
done
echo
echo "Send $OUT/report.txt"
