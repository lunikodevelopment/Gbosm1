#!/bin/bash
# Graphics stage 3j: build AOSP's `aidl` compiler natively (libbase + libaidl-common + main.cpp, no Soong)
# and use it to generate the C++ NDK-backend code for the frozen allocator V2 and graphics-common AIDL.
# Uses the stage 3i source fetch; downloads nothing. Does not build the allocator service or mapper.
set -uo pipefail
[[ $(uname -s) == Linux ]] || { echo 'Run on Fedora Asahi Linux.' >&2; exit 1; }
[[ $(id -u) != 0 ]] || { echo 'Run as your ordinary user, without sudo.' >&2; exit 1; }
for c in clang++ bison flex python3; do command -v $c >/dev/null || { echo "Missing $c." >&2; exit 1; }; done
[[ -f /usr/include/gtest/gtest.h ]] || { echo 'Missing gtest headers (aidl_checkapi.cpp uses gtest assertions). Install with: sudo dnf install gtest-devel' >&2; exit 1; }
[[ -f /usr/include/fmt/chrono.h ]] || { echo 'Missing fmt headers (libbase needs them). Install with: sudo dnf install fmt-devel' >&2; exit 1; }
ROOT=$(cd "$(dirname "$0")/.." && pwd)
W=$ROOT/work/asahi
SRC=$(ls -dt "$W"/aosp-gralloc-src-* 2>/dev/null | head -1)
[[ -n $SRC && -d $SRC/system_tools_aidl && -d $SRC/system_libbase && -d $SRC/hardware_interfaces ]] \
    || { echo 'Run stage 3i first (asahi/fetch-aosp-gralloc-sources.sh).' >&2; exit 1; }
A=$SRC/system_tools_aidl; B=$SRC/system_libbase; HI=$SRC/hardware_interfaces
echo 'THIS STEP MODIFIES STORAGE'
echo "Creates one new $W/aidl-build-* directory: object files, the built aidl binary, the generated C++ code and report.txt."
echo 'The stage 3i sources are only read. Delete the new directory to reverse.'
OUT=$(mktemp -d "$W/aidl-build-XXXXXXXX")
echo "Results: $OUT"
exec > >(tee "$OUT/report.txt") 2>&1
echo "Sources: $SRC"
git -C "$A" log -1 --format='aidl COMMIT %H %cI'
git -C "$B" log -1 --format='libbase COMMIT %H %cI'
git -C "$HI" log -1 --format='hardware/interfaces COMMIT %H %cI'
mkdir -p "$OUT/obj" "$OUT/gen-parser" "$OUT/stubs/gtest"

echo '== Lexer and parser'
bison -d -o "$OUT/gen-parser/aidl_language_y.cpp" "$A/aidl_language_y.yy" 2>&1 | tail -5
flex -o "$OUT/gen-parser/aidl_language_l.cpp" "$A/aidl_language_l.ll" 2>&1 | tail -5
ls "$OUT/gen-parser"
# The sources decide which header name they expect from bison.
WANT=$(cd "$A" && grep -h -o -E '#include "aidl_language_y[^"]*"' *.ll *.yy *.cpp *.h 2>/dev/null | sort -u | sed -E 's/#include "(.*)"/\1/')
echo "Parser header names the sources include: ${WANT:-none}"
for h in $WANT; do
    for cand in "$OUT"/gen-parser/aidl_language_y.hpp "$OUT"/gen-parser/aidl_language_y.hh "$OUT"/gen-parser/aidl_language_y.h; do
        [[ -e $cand && ! -e $OUT/gen-parser/$h ]] && cp "$cand" "$OUT/gen-parser/$h"
    done
done

echo '== Compile (clang++ -std=gnu++20, parallel)'
AIDL_SRCS="aidl_checkapi aidl_const_expressions aidl_dumpapi aidl_language aidl_to_common aidl_to_cpp_common aidl_to_cpp aidl_to_java aidl_to_ndk aidl_to_rust aidl_typenames aidl ast_java check_valid code_writer comments diagnostics generate_aidl_mappings generate_cpp generate_cpp_analyzer generate_java_binder generate_java generate_ndk generate_rust import_resolver io_delegate location logging options parser permission preprocess main"
BASE_SRCS="abi_compatibility chrono_utils cmsg file hex logging mapped_file parsebool parsenetaddress posix_strerror_r process result stringprintf strings threads errors_unix"
FLAGS="-std=gnu++20 -O1 -w -D_GNU_SOURCE -pthread -I$A -I$A/include -I$A/build -I$OUT/gen-parser -I$B/include -I$SRC/system_logging/liblog/include -I$OUT/stubs"
{
    for s in $AIDL_SRCS; do echo "$A/$s.cpp aidl_$s"; done
    for s in aidl_language_y aidl_language_l; do echo "$OUT/gen-parser/$s.cpp gen_$s"; done
    for s in $BASE_SRCS; do echo "$B/$s.cpp base_$s"; done
} > "$OUT/units.txt"
export OUT FLAGS
cat "$OUT/units.txt" | xargs -P "$(nproc)" -L1 bash -c 'clang++ $FLAGS -c "$0" -o "$OUT/obj/$1.o" >"$OUT/obj/$1.log" 2>&1 || echo "FAILED $1"'
FAILED=$(cd "$OUT/obj" && for l in *.log; do n=${l%.log}; [[ -e $n.o ]] || echo "$n"; done)
if [[ -n $FAILED ]]; then
    echo "Compile failures: $(echo $FAILED | tr '\n' ' ')"
    for n in $FAILED; do echo "-- $n"; grep -E 'error|fatal' "$OUT/obj/$n.log" | head -6; done
    echo "Stopped at compile. Send $OUT/report.txt"; exit 1
fi
echo "Compiled $(ls "$OUT"/obj/*.o | wc -l) objects."
echo '== Link'
if clang++ -pthread "$OUT"/obj/*.o -lgtest -o "$OUT/aidl" 2>&1 | head -30; [[ -x $OUT/aidl ]]; then
    "$OUT/aidl" --help 2>&1 | head -6
else
    echo "Stopped at link. Send $OUT/report.txt"; exit 1
fi

echo '== Generate allocator V2 and graphics-common C++ (NDK backend) from the frozen API snapshots'
latest() { ls "$1" 2>/dev/null | grep -E '^[0-9]+$' | sort -n | tail -1; }
AL=$HI/graphics/allocator/aidl/aidl_api/android.hardware.graphics.allocator
CO=$HI/graphics/common/aidl/aidl_api/android.hardware.graphics.common
HC=$HI/common/aidl/aidl_api/android.hardware.common
echo "frozen versions: allocator [$(ls $AL | tr '\n' ' ')] common [$(ls $CO | tr '\n' ' ')] hardware.common [$(ls $HC | tr '\n' ' ')]"
gen() { # name dir version
    local dir=$2/$3
    [[ -d $dir ]] || { echo "GEN_SKIP $1 version $3: $dir missing"; return; }
    local hash=; [[ -f $dir/.hash ]] && hash=$(cat "$dir/.hash")
    echo "-- $1 version $3 hash ${hash:-none}"
    "$OUT/aidl" --lang=ndk --structured --stability=vintf --version "$3" ${hash:+--hash "$hash"} \
        -I "$AL/2" -I "$CO/$(latest "$CO")" -I "$HC/$(latest "$HC")" \
        -o "$OUT/generated" -h "$OUT/generated/include" $(find "$dir" -name '*.aidl') 2>&1 | head -10
}
gen hardware.common "$HC" "$(latest "$HC")"
gen graphics.common "$CO" "$(latest "$CO")"
gen graphics.allocator "$AL" 2
echo '-- generated files'
find "$OUT/generated" -type f 2>/dev/null | sed "s|$OUT/||" | sort | head -80
echo "count: $(find "$OUT/generated" -type f 2>/dev/null | wc -l)"
echo
echo "Send $OUT/report.txt"
