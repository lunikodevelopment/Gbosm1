#!/bin/bash
# Graphics stage 3o: cross-compile and link, for Android arm64, the AOSP minigbm allocator service
# (cros_gralloc/aidl), the stable-C mapper (cros_gralloc/mapper_stablec) and their dependencies, using the
# stage 3n-d header set. Links against the image's own libraries. Nothing is installed or run; the result
# files are not yet loaded by anything. Uses the stage 3e libminigbm and original shims for liblog/cutils.
set -uo pipefail
[[ $(uname -s) == Linux ]] || { echo 'Run on Fedora Asahi Linux.' >&2; exit 1; }
[[ $(id -u) != 0 ]] || { echo 'Run as your ordinary user, without sudo.' >&2; exit 1; }
for c in clang++ ld.lld readelf; do command -v $c >/dev/null || { echo "Missing $c." >&2; exit 1; }; done
ROOT=$(cd "$(dirname "$0")/.." && pwd)
W=$ROOT/work/asahi
newest() { local d; for d in $(ls -dt $W/$1 2>/dev/null); do [[ -e $d/$2 ]] && { echo "$d"; return; }; done; }
HK=$(newest 'honeykrisp-android-*' vulkan.asahi.so)
SRC=$(newest 'aosp-gralloc-src-*' system_libbase/include)
GEN=$(newest 'aidl-build-*' generated/include/aidl/android/hardware/graphics/allocator/IAllocator.h)
HIDL=$(newest 'hidl-build-*' generated/android/hardware/graphics/mapper/4.0/IMapper.h)
MG=$(ls -d "$W"/minigbm-apple-*/minigbm 2>/dev/null | tail -1)
MGB=$(newest 'minigbm-bionic-*' lib64/libminigbm.so)
IMG=$(newest 'android-root-*' root/system/lib64/libbase.so)
LIBDRM_H=$(find "$HK" -name xf86drm.h -path '*libdrm*' 2>/dev/null | head -1)
for v in HK SRC GEN HIDL MG MGB IMG LIBDRM_H; do [[ -n ${!v} ]] || { echo "Missing earlier result for $v (stages 2c, 3d, 3e, 3i, 3j, 3m and the extracted image are required)." >&2; exit 1; }; done
IMGLIB=$IMG/root/system/lib64
echo 'THIS STEP MODIFIES STORAGE'
echo "Creates one new $W/gralloc-build-* directory: object files, the linked allocator service and mapper library and report.txt."
echo 'Earlier results are only read; nothing is installed or run. Delete the new directory to reverse.'
OUT=$(mktemp -d "$W/gralloc-build-XXXXXXXX")
echo "Results: $OUT"
exec > >(tee "$OUT/report.txt") 2>&1
echo "Image libs: $IMGLIB ; libminigbm: $MGB/lib64/libminigbm.so"
EXTRA=$(ls -d "$W"/aosp-gralloc-extra-* "$W"/aosp-gralloc-more-* 2>/dev/null | tr '\n' ' ')
MORE=$(ls -dt "$W"/aosp-gralloc-more-* 2>/dev/null | head -1)
INCS=$(find "$SRC" $EXTRA -type d \( -name include -o -name include_cpp -o -name include_platform -o -name include_ndk -o -name include_sdk \) -not -path '*/.git/*' 2>/dev/null | sort)
IFLAGS=; for d in $INCS; do IFLAGS="$IFLAGS -I$d"; done
for d in "$W"/aosp-gralloc-more-*/system_libfmq/base; do [[ -d $d ]] && IFLAGS="$IFLAGS -I$d"; done
LIBDRM_SRC=$(dirname "$LIBDRM_H")
TC="--target=aarch64-linux-android35 --sysroot=$HK/ndk/sysroot -resource-dir=$HK/clang-resource"
CXXF="$TC -std=gnu++20 -fPIC -O1 -w -DANDROID -D__ANDROID_VNDK__ -D__ANDROID_VENDOR__ -D_GNU_SOURCE -DDRV_APPLE \
 -I$GEN/generated/include -I$HIDL/generated -I$HIDL/system_libhwbinder/include -I$MORE/generated \
 -I$ROOT/asahi/android-shims -I$LIBDRM_SRC -I$LIBDRM_SRC/include/drm -I$MG -I$MG/cros_gralloc $IFLAGS"
export OUT CXXF
mkdir -p "$OUT/obj"

# Sources: minigbm gralloc front ends, the cros_gralloc core, its gralloc4 helpers, the generated AIDL code,
# and the aidlcommonsupport NativeHandle helper from hardware/interfaces.
{
    for f in aidl/Allocator.cpp aidl/Main.cpp mapper_stablec/Mapper.cpp; do echo "$MG/cros_gralloc/$f svc_$(echo "$f" | tr '/.' '__')"; done
    # gralloc4/ also holds the legacy HIDL allocator and mapper; only its Utils are used by the AIDL allocator and stable-C mapper.
    for f in "$MG"/cros_gralloc/*.cc "$MG"/cros_gralloc/gralloc4/CrosGralloc4Utils.cc; do [[ -e $f ]] && echo "$f core_$(basename "${f%.cc}")"; done
    for f in $(find "$GEN/generated" -name '*.cpp' | sort); do echo "$f gen_$(basename "${f%.cpp}")"; done
    NH=$(find "$SRC/hardware_interfaces/common/support" -name 'NativeHandle.cpp' 2>/dev/null | head -1)
    [[ -n $NH ]] && echo "$NH support_NativeHandle"
} > "$OUT/units.txt"
echo "units: $(wc -l < "$OUT/units.txt")"
echo '== Compile (parallel)'
xargs -P "$(nproc)" -L1 bash -c 'clang++ $CXXF -c "$0" -o "$OUT/obj/$1.o" >"$OUT/obj/$1.log" 2>&1 || echo "FAILED $1"' < "$OUT/units.txt"
FAILED=$(cd "$OUT/obj" && for l in *.log; do n=${l%.log}; [[ -e $n.o ]] || echo "$n"; done)
if [[ -n $FAILED ]]; then
    echo "Compile failures: $(echo $FAILED | tr '\n' ' ')"
    for n in $FAILED; do echo "-- $n"; grep -E 'error|fatal' "$OUT/obj/$n.log" | head -6; done
    echo "Stopped at compile. Send $OUT/report.txt"; exit 1
fi
echo "Compiled $(ls "$OUT"/obj/*.o | wc -l) objects."

echo '== Which libraries the image provides'
LIBS=
for n in base cutils hidlbase gralloctypes utils hwbinder vndksupport sync android.hardware.graphics.mapper@4.0 android.hardware.graphics.common@1.2 android.hardware.graphics.common@1.1 android.hardware.graphics.common@1.0 android.hidl.base@1.0; do
    if [[ -e $IMGLIB/lib$n.so ]]; then echo "found lib$n.so"; LIBS="$LIBS $IMGLIB/lib$n.so"
    elif [[ -e $IMGLIB/$n.so ]]; then echo "found $n.so"; LIBS="$LIBS $IMGLIB/$n.so"
    else echo "NOT IN IMAGE: $n"; fi
done
LDF="$TC -fuse-ld=lld -Wl,-z,max-page-size=16384 -Wl,--allow-shlib-undefined"
SYS="-lbinder_ndk -llog -landroid -lm"
MGLIB=$MGB/lib64/libminigbm.so; DRMLIB=$MGB/lib64/libdrm.so
SVC=$(ls "$OUT"/obj/svc_aidl_*.o "$OUT"/obj/core_*.o "$OUT"/obj/gen_*.o "$OUT"/obj/support_*.o 2>/dev/null)
MAP=$(ls "$OUT"/obj/svc_mapper_*.o "$OUT"/obj/core_*.o "$OUT"/obj/gen_*.o "$OUT"/obj/support_*.o 2>/dev/null)
echo '== Link allocator service'
clang++ $LDF -pie $SVC $MGLIB $DRMLIB $LIBS $SYS -o "$OUT/allocator-service.minigbm" > "$OUT/link-service.log" 2>&1
sort -u "$OUT/link-service.log" | grep -E 'undefined|error' | head -25
echo '== Link mapper library'
clang++ $LDF -shared -Wl,-soname,mapper.minigbm.so $MAP $MGLIB $DRMLIB $LIBS $SYS -o "$OUT/mapper.minigbm.so" > "$OUT/link-mapper.log" 2>&1
sort -u "$OUT/link-mapper.log" | grep -E 'undefined|error' | head -25
for f in allocator-service.minigbm mapper.minigbm.so; do
    if [[ -f $OUT/$f ]]; then echo "BUILT $f"; readelf -h "$OUT/$f" | grep -E 'Class|Machine|Type'; readelf -d "$OUT/$f" | grep NEEDED
         readelf -sW --dyn-syms "$OUT/$f" | grep -E 'AIMapper_loadIMapper|main$' | head -3
    else echo "NOT BUILT $f"; fi
done
echo; echo "Send $OUT/report.txt"
