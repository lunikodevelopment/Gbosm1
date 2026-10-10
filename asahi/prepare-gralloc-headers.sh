#!/bin/bash
# Graphics stage 3n-b: fetch the last few header sources stage 3n found missing (libfmq, nativebase) and
# generate android.hidl.manager@1.0 HIDL headers with the hidl-gen built in stage 3m. Builds nothing else.
set -uo pipefail
[[ $(uname -s) == Linux ]] || { echo 'Run on Fedora Asahi Linux.' >&2; exit 1; }
[[ $(id -u) != 0 ]] || { echo 'Run as your ordinary user, without sudo.' >&2; exit 1; }
ROOT=$(cd "$(dirname "$0")/.." && pwd)
W=$ROOT/work/asahi
newest() { local d; for d in $(ls -dt $W/$1 2>/dev/null); do [[ -e $d/$2 ]] && { echo "$d"; return; }; done; }
HB=$(newest 'hidl-build-*' hidl-gen)
SRC=$(newest 'aosp-gralloc-src-*' system_libbase/include)
EXTRA=$(newest 'aosp-gralloc-extra-*' system_libhidl/transport)
for v in HB SRC EXTRA; do [[ -n ${!v} ]] || { echo "Missing earlier result for $v (stages 3i, 3l and 3m are required)." >&2; exit 1; }; done
echo 'THIS STEP MODIFIES STORAGE'
echo "Creates one new $W/aosp-gralloc-more-* directory: three small sparse shallow clones from android.googlesource.com,"
echo 'generated HIDL headers and report.txt. Earlier results are only read. Delete the new directory to reverse.'
OUT=$(mktemp -d "$W/aosp-gralloc-more-XXXXXXXX")
echo "Results: $OUT"
exec > >(tee "$OUT/report.txt") 2>&1
BASE=https://android.googlesource.com/platform
BR=$(git -C "$SRC/system_tools_aidl" rev-parse --abbrev-ref HEAD 2>/dev/null); [[ -z $BR || $BR == HEAD ]] && BR=android16-release
fetch() {
    local repo=$1; shift; local dir=$OUT/${repo//\//_}
    echo "== $repo ($*)"
    local sparse=; [[ $# -gt 0 ]] && sparse=--sparse
    if ! git clone -q --depth 1 --filter=blob:none $sparse --branch "$BR" "$BASE/$repo" "$dir" 2>/dev/null; then
        rm -rf "$dir"; git clone -q --depth 1 --branch "$BR" "$BASE/$repo" "$dir" || { echo "CLONE=FAIL $repo"; return; }
    fi
    [[ $# -gt 0 ]] && git -C "$dir" sparse-checkout set "$@" 2>/dev/null
    git -C "$dir" log -1 --format='COMMIT %H %cI'
}
fetch system/libfmq include base
fetch hardware/libhardware include include_all
fetch frameworks/native libs/nativebase
echo '== Generate android.hidl.manager@1.0 headers'
HI=$SRC/hardware_interfaces; LH=$EXTRA/system_libhidl
"$HB/hidl-gen" -o "$OUT/generated" -L c++-headers -r android.hidl:"$LH/transport" -r android.hardware:"$HI" android.hidl.manager@1.0 2>&1 | head -10
find "$OUT/generated" -type f | sed "s|$OUT/||" | sort
echo '== Where the files named missing live'
for h in hardware/gralloc.h nativebase/nativebase.h fmq/MQDescriptorBase.h android/hidl/manager/1.0/IServiceNotification.h; do
    echo "$h: $(find "$OUT" -path "*/$h" -not -path '*/.git/*' 2>/dev/null | head -2 | sed "s|$OUT/||" | tr '\n' ' ')"
done
echo; echo "Send $OUT/report.txt"
