#!/bin/bash
# Graphics stage 3m: build AOSP's `hidl-gen` natively (its own sources + libbase, no Soong) and use it to
# generate the C++ headers for the HIDL packages the allocator service names: android.hidl.base@1.0,
# android.hardware.graphics.common@1.0/1.1/1.2 and android.hardware.graphics.mapper@4.0. Headers only: the
# Android build links the image's own libhidlbase and libgralloctypes. Builds nothing for the service yet.
set -uo pipefail
[[ $(uname -s) == Linux ]] || { echo 'Run on Fedora Asahi Linux.' >&2; exit 1; }
[[ $(id -u) != 0 ]] || { echo 'Run as your ordinary user, without sudo.' >&2; exit 1; }
for c in clang++ bison flex; do command -v $c >/dev/null || { echo "Missing $c." >&2; exit 1; }; done
[[ -f /usr/include/openssl/sha.h ]] || { echo 'Missing OpenSSL headers. Install with: sudo dnf install openssl-devel' >&2; exit 1; }
[[ -f /usr/include/json/json.h || -f /usr/include/jsoncpp/json/json.h ]] || { echo 'Missing jsoncpp headers. Install with: sudo dnf install jsoncpp-devel' >&2; exit 1; }
ROOT=$(cd "$(dirname "$0")/.." && pwd)
W=$ROOT/work/asahi
newest() { local d; for d in $(ls -dt $W/$1 2>/dev/null); do [[ -e $d/$2 ]] && { echo "$d"; return; }; done; }
SRC=$(newest 'aosp-gralloc-src-*' system_libbase/include)
EXTRA=$(newest 'aosp-gralloc-extra-*' system_tools_hidl/main.cpp)
AB=$(newest 'aidl-build-*' aidl)
for v in SRC EXTRA AB; do [[ -n ${!v} ]] || { echo "Missing earlier result for $v (stages 3i, 3j and 3l are required)." >&2; exit 1; }; done
H=$EXTRA/system_tools_hidl; B=$SRC/system_libbase; HI=$SRC/hardware_interfaces; LH=$EXTRA/system_libhidl
echo 'THIS STEP MODIFIES STORAGE'
echo "Creates one new $W/hidl-build-* directory: object files, the built hidl-gen, generated C++ headers and report.txt."
echo 'Earlier results are only read. Delete the new directory to reverse.'
OUT=$(mktemp -d "$W/hidl-build-XXXXXXXX")
echo "Results: $OUT"
exec > >(tee "$OUT/report.txt") 2>&1
git -C "$H" log -1 --format='hidl COMMIT %H %cI'
mkdir -p "$OUT/obj" "$OUT/gen-parser"
echo '== Lexer and parser'
bison -d -o "$OUT/gen-parser/hidl-gen_y.cpp" "$H/hidl-gen_y.yy" 2>&1 | tail -5
flex -o "$OUT/gen-parser/hidl-gen_l.cpp" "$H/hidl-gen_l.ll" 2>&1 | tail -5
ls "$OUT/gen-parser"
WANT=$(cd "$H" && grep -h -o -E '#include "hidl-gen_y[^"]*"' *.ll *.yy *.cpp *.h 2>/dev/null | sort -u | sed -E 's/#include "(.*)"/\1/')
echo "Parser header names the sources include: ${WANT:-none}"
for h in $WANT; do
    for cand in "$OUT"/gen-parser/hidl-gen_y.hpp "$OUT"/gen-parser/hidl-gen_y.hh "$OUT"/gen-parser/hidl-gen_y.h; do
        [[ -e $cand && ! -e $OUT/gen-parser/$h ]] && cp "$cand" "$OUT/gen-parser/$h"
    done
done
echo '== Sources: top level plus utils, host_utils, hashing, metadata'
{
    for f in "$H"/*.cpp; do echo "$f hidl_$(basename "${f%.cpp}")"; done
    for d in utils host_utils hashing metadata; do
        for f in "$H"/$d/*.cpp; do [[ -e $f && $f != */test.cpp && $f != *_test.cpp ]] && echo "$f hidl_${d}_$(basename "${f%.cpp}")"; done
    done
    for s in hidl-gen_y hidl-gen_l; do echo "$OUT/gen-parser/$s.cpp gen_$s"; done
    for s in abi_compatibility chrono_utils cmsg file hex logging mapped_file parsebool parsenetaddress posix_strerror_r process result stringprintf strings threads errors_unix; do
        echo "$B/$s.cpp base_$s"; done
    echo "$ROOT/asahi/liblog-host-shim.cpp shim_liblog"
} > "$OUT/units.txt"
echo "units: $(wc -l < "$OUT/units.txt")"
# Soong exports each library's include dirs; here every directory that holds a header goes on the path.
IFL=; for d in $(find "$H" -name '*.h' -not -path '*/test/*' -not -path '*/hidl2aidl/*' -not -path '*/c2hal/*' -not -path '*/lint/*' -printf '%h\n' | sort -u); do IFL="$IFL -I$d"; done
FLAGS="-std=gnu++20 -O1 -w -D_GNU_SOURCE -pthread -include cstdint -I$H -I$OUT/gen-parser $IFL -I$B/include -I$SRC/system_logging/liblog/include -I$SRC/system_core/libutils/include -I/usr/include/jsoncpp"
export OUT FLAGS
echo '== Compile (parallel)'
xargs -P "$(nproc)" -L1 bash -c 'clang++ $FLAGS -c "$0" -o "$OUT/obj/$1.o" >"$OUT/obj/$1.log" 2>&1 || echo "FAILED $1"' < "$OUT/units.txt"
FAILED=$(cd "$OUT/obj" && for l in *.log; do n=${l%.log}; [[ -e $n.o ]] || echo "$n"; done)
if [[ -n $FAILED ]]; then
    echo "Compile failures: $(echo $FAILED | tr '\n' ' ')"
    for n in $FAILED; do echo "-- $n"; grep -E 'error|fatal' "$OUT/obj/$n.log" | head -6; done
    echo "Stopped at compile. Send $OUT/report.txt"; exit 1
fi
echo "Compiled $(ls "$OUT"/obj/*.o | wc -l) objects."
echo '== Link'
clang++ -pthread "$OUT"/obj/*.o -lcrypto -ljsoncpp -lfmt -o "$OUT/hidl-gen" 2>&1 | head -30
[[ -x $OUT/hidl-gen ]] || { echo "Stopped at link. Send $OUT/report.txt"; exit 1; }
"$OUT/hidl-gen" -h 2>&1 | head -8
echo '== Generate C++ headers'
for p in android.hidl.base@1.0 android.hardware.graphics.common@1.0 android.hardware.graphics.common@1.1 \
         android.hardware.graphics.common@1.2 android.hardware.graphics.mapper@4.0; do
    echo "-- $p"
    "$OUT/hidl-gen" -o "$OUT/generated" -L c++-headers -r android.hidl:"$LH/transport" -r android.hardware:"$HI" "$p" 2>&1 | head -10
done
echo '-- generated headers'
find "$OUT/generated" -type f 2>/dev/null | sed "s|$OUT/||" | sort | head -60
echo "count: $(find "$OUT/generated" -type f 2>/dev/null | wc -l)"
echo; echo "Send $OUT/report.txt"
