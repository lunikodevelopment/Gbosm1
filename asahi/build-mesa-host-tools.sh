#!/bin/bash
# Graphics stage 2b: native Fedora build of Mesa's host compilers (mesa_clc and the
# precompiled-shader compiler) from the exact Fedora mesa source, plus native Honeykrisp
# as a sanity check. Nothing is installed system-wide; output stays under work/asahi.
set -euo pipefail
[[ $(uname -s) == Linux ]] || { echo 'Run on Fedora Asahi Linux.' >&2; exit 1; }
[[ $(id -u) != 0 ]] || { echo 'Run as your ordinary user, without sudo.' >&2; exit 1; }
ROOT=$(cd "$(dirname "$0")/.." && pwd)
SRC_DIR=$(ls -td "$ROOT"/work/asahi/mesa-toolchain-*/ 2>/dev/null | head -1 || true)
TAR=$(ls "$SRC_DIR"srpm/mesa-*.tar.xz 2>/dev/null | head -1 || true)
[[ -n $TAR ]] || { echo 'No mesa source from stage 2a; run asahi/check-mesa-android-toolchain.sh first.' >&2; exit 1; }
for tool in meson ninja clang pkg-config; do
    command -v "$tool" >/dev/null || { echo "Missing $tool; run the dnf command in NEXT_STEPS.md first." >&2; exit 1; }
done
echo 'THIS STEP MODIFIES STORAGE'
echo "Creates one new $ROOT/work/asahi/mesa-host-* directory (source, build tree, tools; roughly 1-2 GB, an estimate)."
echo 'Nothing is installed outside it. Delete the new directory to reverse.'
OUT=$(mktemp -d "$ROOT/work/asahi/mesa-host-XXXXXXXX")
echo "Results: $OUT"
echo "Source: $TAR"
exec > >(tee "$OUT/report.txt") 2>&1
tar -xf "$TAR" -C "$OUT"
S=$(ls -d "$OUT"/mesa-*/ | head -1)
echo "== Configuring (native, Asahi Vulkan only, host compilers installed into $OUT/prefix)"
status=0
meson setup "$OUT/build" "$S" --prefix="$OUT/prefix" --libdir=lib --buildtype=release \
    -Dplatforms= -Dvulkan-drivers=asahi -Dgallium-drivers= -Dopengl=false -Degl=disabled \
    -Dgles1=disabled -Dgles2=disabled -Dglx=disabled -Dgbm=disabled -Dxmlconfig=disabled \
    -Dvulkan-layers= -Dllvm=enabled -Dshared-llvm=enabled -Dmesa-clc=enabled -Dinstall-mesa-clc=true \
    -Dprecomp-compiler=enabled -Dinstall-precomp-compiler=true -Dbuild-tests=false \
    -Dandroid-libbacktrace=disabled -Dvalgrind=disabled -Dlibunwind=disabled || status=$?
echo "configure exit status: $status"
if [[ $status == 0 ]]; then
    echo '== Building (this takes a while)'
    ninja -C "$OUT/build" install || status=$?
    echo "build exit status: $status"
fi
echo '== Host tools installed'
ls -l "$OUT/prefix/bin" 2>/dev/null || echo 'none'
echo '== Native Honeykrisp ICD built (not installed or used by the system)'
find "$OUT/prefix" -name 'libvulkan_asahi.so' -o -name 'asahi_icd*.json' 2>/dev/null
grep -E 'mesa-clc|precomp|llvm|clc|spirv' "$OUT/build/meson-logs/meson-log.txt" 2>/dev/null | grep -iE 'found|version|program' | head -30
echo "Send $OUT/report.txt"
exit "$status"
