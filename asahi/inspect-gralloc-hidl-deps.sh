#!/bin/bash
# Graphics stage 3l: fetch the few extra AOSP trees the allocator/mapper sources still name (nativewindow,
# gralloc types, libhidl, dmabufheap, hidl-gen) and report exactly how the minigbm service files use the
# HIDL mapper 4.0 and gralloctypes APIs, to decide between generating HIDL headers and patching them out.
# Builds nothing and runs nothing from the fetched code.
set -uo pipefail
[[ $(uname -s) == Linux ]] || { echo 'Run on Fedora Asahi Linux.' >&2; exit 1; }
[[ $(id -u) != 0 ]] || { echo 'Run as your ordinary user, without sudo.' >&2; exit 1; }
command -v git >/dev/null || { echo 'Missing git.' >&2; exit 1; }
ROOT=$(cd "$(dirname "$0")/.." && pwd)
W=$ROOT/work/asahi
MG=$(ls -d "$W"/minigbm-apple-*/minigbm 2>/dev/null | tail -1)
SRC=$(ls -dt "$W"/aosp-gralloc-src-* 2>/dev/null | head -1)
[[ -n $MG && -n $SRC ]] || { echo 'Need stages 3d and 3i first.' >&2; exit 1; }
echo 'THIS STEP MODIFIES STORAGE'
echo "Creates one new $W/aosp-gralloc-extra-* directory with sparse shallow git clones from android.googlesource.com"
echo '(tens of MB; the report prints the real size) and report.txt. Nothing is built, installed or run. Delete it to reverse.'
OUT=$(mktemp -d "$W/aosp-gralloc-extra-XXXXXXXX")
echo "Results: $OUT"
exec > >(tee "$OUT/report.txt") 2>&1
BASE=https://android.googlesource.com/platform
BRANCH=$(git -C "$SRC/system_tools_aidl" rev-parse --abbrev-ref HEAD 2>/dev/null)
[[ $BRANCH == HEAD || -z $BRANCH ]] && BRANCH=android16-release
echo "Branch: $BRANCH"
fetch() {
    local repo=$1; shift
    local dir=$OUT/${repo//\//_}
    echo "== $repo ($*)"
    local sparse=; [[ $# -gt 0 ]] && sparse=--sparse
    if ! git clone -q --depth 1 --filter=blob:none $sparse --branch "$BRANCH" "$BASE/$repo" "$dir" 2>/dev/null; then
        rm -rf "$dir"; git clone -q --depth 1 --branch "$BRANCH" "$BASE/$repo" "$dir" || { echo "CLONE=FAIL $repo"; return; }
    fi
    [[ $# -gt 0 ]] && git -C "$dir" sparse-checkout set "$@" 2>/dev/null
    git -C "$dir" log -1 --format='COMMIT %H %cI'
}
fetch frameworks/native libs/gralloc libs/nativewindow libs/ui
fetch system/libhidl base transport
fetch system/memory libdmabufheap
fetch system/tools/hidl

echo; echo '######## How the minigbm service files use HIDL mapper 4.0 / gralloctypes'
cd "$MG" || exit 1
for f in cros_gralloc/aidl/Allocator.cpp cros_gralloc/aidl/Main.cpp cros_gralloc/mapper_stablec/Mapper.cpp; do
    echo "-- $f: includes"
    grep -n '^#include' "$f"
    echo "-- $f: hidl/gralloc4/mapper4 uses"
    grep -n -E 'hidl|Hidl|gralloc4::|mapper::V4_0|V4_0|V1_[0-9]::|android::hardware::graphics::(common|mapper)::V|IMapper|hidl_vec|hidl_handle' "$f" | head -40
done
echo; echo '######## Headers that exist in the extra trees'
for h in system/window.h system/graphics.h gralloctypes/Gralloc4.h hidl/HidlSupport.h BufferAllocator/BufferAllocator.h; do
    echo "$h: $(find "$OUT" -path "*/$h" -not -path '*/.git/*' 2>/dev/null | head -3 | sed "s|$OUT/||" | tr '\n' ' ')"
done
echo; echo '######## gralloctypes build definition'
sed -n '1,80p' "$OUT/frameworks_native/libs/gralloc/types/Android.bp" 2>/dev/null | grep -n -E 'name:|srcs:|shared_libs|static_libs|header_libs|"[A-Za-z0-9_./@-]+"' | head -40
echo; echo '######## HIDL interfaces present in hardware/interfaces (inputs for hidl-gen)'
find "$SRC/hardware_interfaces/graphics" \( -path '*mapper/4.0/*.hal' -o -path '*common/1.2/*.hal' -o -path '*common/1.0/types.hal' -o -path '*common/1.1/types.hal' \) 2>/dev/null | sed "s|$SRC/||"
echo; echo '######## hidl-gen sources (size and dependencies)'
ls "$OUT/system_tools_hidl" 2>/dev/null | tr '\n' ' '; echo
grep -n -E 'name: "hidl-gen"|srcs:|static_libs|shared_libs|"lib[a-z0-9_]+"' "$OUT/system_tools_hidl/Android.bp" 2>/dev/null | head -40
du -sh "$OUT" | cut -f1 | sed 's/^/total size /'
echo; echo "Send $OUT/report.txt"
