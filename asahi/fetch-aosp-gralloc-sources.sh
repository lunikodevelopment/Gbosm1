#!/bin/bash
# Graphics stage 3i: fetch the AOSP sources an allocator service and mapper build would need, sparsely,
# and report what building AOSP's `aidl` compiler natively on Fedora would depend on. Builds nothing.
set -uo pipefail
[[ $(uname -s) == Linux ]] || { echo 'Run on Fedora Asahi Linux.' >&2; exit 1; }
[[ $(id -u) != 0 ]] || { echo 'Run as your ordinary user, without sudo.' >&2; exit 1; }
command -v git >/dev/null || { echo 'Missing git.' >&2; exit 1; }
ROOT=$(cd "$(dirname "$0")/.." && pwd)
W=$ROOT/work/asahi
echo 'THIS STEP MODIFIES STORAGE'
echo "Creates one new $W/aosp-gralloc-src-* directory with sparse shallow git clones from android.googlesource.com"
echo '(a few hundred MB at most; an estimate, the report prints the real size) and report.txt.'
echo 'Nothing is built, installed or run from them. Delete the new directory to reverse.'
mkdir -p "$W"
OUT=$(mktemp -d "$W/aosp-gralloc-src-XXXXXXXX")
echo "Results: $OUT"
exec > >(tee "$OUT/report.txt") 2>&1
BASE=https://android.googlesource.com/platform
# Prefer a release branch near the image's Android version; fall back to main.
BRANCH=main
for b in android16-release android15-release; do
    git ls-remote --exit-code --heads $BASE/system/tools/aidl "$b" >/dev/null 2>&1 && { BRANCH=$b; break; }
done
echo "Branch used: $BRANCH"
# repo : sparse paths (space separated)
fetch() {
    local repo=$1; shift
    local dir=$OUT/${repo//\//_}
    echo "== $repo ($*)"
    local sparse=
    [[ $# -gt 0 ]] && sparse=--sparse
    if ! git clone -q --depth 1 --filter=blob:none $sparse --branch "$BRANCH" "$BASE/$repo" "$dir" 2>/dev/null; then
        echo "partial clone failed, retrying a plain shallow clone"
        rm -rf "$dir"
        git clone -q --depth 1 --branch "$BRANCH" "$BASE/$repo" "$dir" || { echo "CLONE=FAIL $repo"; return; }
    fi
    [[ $# -gt 0 ]] && git -C "$dir" sparse-checkout set "$@" 2>/dev/null
    git -C "$dir" log -1 --format='COMMIT %H %cI'
    du -sh "$dir" | cut -f1 | sed 's/^/size /'
}
fetch system/tools/aidl
fetch system/libbase
fetch hardware/interfaces graphics common
fetch frameworks/native libs/binder/ndk
fetch system/core libcutils libsync libsystem libutils
fetch hardware/libhardware include
fetch system/logging liblog

A=$OUT/system_tools_aidl
echo; echo '######## aidl: build definition'
ls "$A" | tr '\n' ' '; echo
sed -n '1,400p' "$A/Android.bp" | grep -n -E '^(cc_|genrule|filegroup)|name:|srcs:|static_libs|shared_libs|header_libs|"lib[a-z_0-9-]+"|"[A-Za-z_/.0-9-]+\.(cpp|cc|ll|yy)"' | head -150
echo '-- host tools'
for t in gcc g++ bison flex make cmake ninja meson; do command -v $t >/dev/null && echo "have $t" || echo "MISSING $t"; done
for p in jsoncpp-devel libxml2-devel protobuf-devel gtest-devel; do rpm -q $p >/dev/null 2>&1 && echo "have $p" || echo "MISSING $p"; done
echo '-- includes used by the aidl sources that are not C++ standard or aidl-local'
(cd "$A" && grep -h -o -E '^#include <[^>]+>' *.cpp *.h 2>/dev/null | sort | uniq -c | sort -rn | grep -v -E '<[a-z_0-9]+>$' | head -50)
echo '-- libbase sources'
sed -n '1,200p' "$OUT/system_libbase/Android.bp" | grep -n -E 'name: "libbase"|srcs:|"[a-z_]+\.cpp"' | head -50
echo '-- graphics AIDL definitions (allocator and common)'
find "$OUT/hardware_interfaces/graphics" "$OUT/hardware_interfaces/common" -name '*.aidl' -path '*aidl*' -not -path '*aidl_api*' 2>/dev/null | sed "s|$OUT/||" | head -60
echo '-- frozen allocator versions'
ls "$OUT/hardware_interfaces/graphics/allocator/aidl/aidl_api/android.hardware.graphics.allocator" 2>/dev/null
echo '-- stable-C mapper headers'
find "$OUT/hardware_interfaces/graphics/mapper/stable-c" -name '*.h' 2>/dev/null | sed "s|$OUT/||"
echo '-- Binder NDK C++ headers'
find "$OUT/frameworks_native/libs/binder/ndk" -name '*.h' -path '*include*' 2>/dev/null | sed "s|$OUT/||" | head -30
echo '-- total size'
du -sh "$OUT" | cut -f1
echo
echo "Send $OUT/report.txt"
