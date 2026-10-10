#!/bin/bash
# Graphics stage 3g (read only): what building minigbm's Android allocator service and mapper needs.
# Reads the patched minigbm copy and the NDK sysroot from earlier results; downloads nothing, builds nothing.
set -uo pipefail
[[ $(uname -s) == Linux ]] || { echo 'Run on Fedora Asahi Linux.' >&2; exit 1; }
[[ $(id -u) != 0 ]] || { echo 'Run as your ordinary user, without sudo.' >&2; exit 1; }
ROOT=$(cd "$(dirname "$0")/.." && pwd)
W=$ROOT/work/asahi
SRC=$(ls -dt "$W"/minigbm-apple-*/minigbm 2>/dev/null | head -1)
HK=$(ls -dt "$W"/honeykrisp-android-* 2>/dev/null | while read -r d; do [[ -d $d/ndk/sysroot ]] && { echo "$d"; break; }; done)
[[ -n $SRC && -n $HK ]] || { echo 'Needs the stage 3d copy of minigbm and the stage 2c NDK sysroot.' >&2; exit 1; }
echo 'THIS STEP MODIFIES STORAGE'
echo "Creates one new $W/gralloc-survey-* directory holding only report.txt. Everything else is only read."
echo 'Delete the new directory to reverse.'
OUT=$(mktemp -d "$W/gralloc-survey-XXXXXXXX")
echo "Results: $OUT"
exec > >(tee "$OUT/report.txt") 2>&1
INC=$HK/ndk/sysroot/usr/include
G=$SRC/cros_gralloc
echo "minigbm copy: $SRC"
echo "NDK include dir: $INC"

echo '== Files (lines) in the Android-facing directories'
(cd "$G" && wc -l *.cc *.h aidl/* mapper_stablec/* gralloc4/* gralloc0/*.cc 2>/dev/null)

echo '== Every <header> included by those sources, and whether the NDK sysroot has it'
(cd "$G" && grep -h -o -E '^#\s*include\s*<[^>]+>' *.cc *.h aidl/* mapper_stablec/* gralloc4/* 2>/dev/null \
    | sed -E 's/^#\s*include\s*<([^>]+)>/\1/' | sort -u) | while read -r h; do
    if [[ -e $INC/$h || -e $INC/c++/v1/$h || -e $INC/aarch64-linux-android/$h ]]; then echo "FOUND   $h"; else echo "MISSING $h"; fi
done

echo '== Quoted (project) includes that do not exist in the minigbm tree'
(cd "$G" && grep -h -o -E '^#\s*include\s*"[^"]+"' *.cc *.h aidl/* mapper_stablec/* gralloc4/* 2>/dev/null \
    | sed -E 's/^#\s*include\s*"([^"]+)"/\1/' | sort -u) | while read -r h; do
    [[ -e $G/$h || -e $SRC/$h || -e $G/aidl/$h || -e $G/mapper_stablec/$h || -e $G/gralloc4/$h ]] || echo "MISSING-LOCAL $h"
done

echo '== AIDL / mapper / service entry points'
(cd "$G" && grep -n -E 'IAllocator|IMapper|AIMapper|BnAllocator|BnMapper|AServiceManager|ABinderProcess|AIBinder|dlopen|registerAs|addService|aidl::|ANDROID_HAL_STABLE|_V[0-9]' aidl/* mapper_stablec/* 2>/dev/null | head -60)

echo '== aidl/Main.cpp (whole file)'
cat "$G/aidl/Main.cpp" 2>/dev/null | head -80
echo '== aidl/Android.bp'
cat "$G/aidl/Android.bp" 2>/dev/null | head -80
echo '== mapper_stablec/Android.bp'
cat "$G/mapper_stablec/Android.bp" 2>/dev/null | head -60
echo '== allocator.rc, allocator.xml, mapper.minigbm.xml'
cat "$G/aidl/allocator.rc" "$G/aidl/allocator.xml" "$G/mapper_stablec/mapper.minigbm.xml" 2>/dev/null
echo '== sepolicy directory'
ls -l "$G/sepolicy" 2>/dev/null

echo '== Is an AIDL compiler available here?'
command -v aidl || echo 'aidl: not on PATH'
dnf -q list --available '*aidl*' 2>/dev/null | head -5 || true
rpm -qa 2>/dev/null | grep -i aidl || echo 'no aidl rpm installed'
find "$W" -maxdepth 4 \( -name 'aidl' -o -name 'aidl-*' \) -type f 2>/dev/null | head -5
echo '== NDK binder and buffer headers present'
ls "$INC/android" 2>/dev/null | grep -E 'binder|hardware_buffer|native_window|sync|log' | tr '\n' ' '; echo
ls -d "$INC"/aidl "$INC"/cutils "$INC"/hardware "$INC"/sync "$INC"/vndk 2>&1 | head
echo
echo "Send $OUT/report.txt"
