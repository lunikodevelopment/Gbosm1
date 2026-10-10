#!/bin/bash
# Graphics stage 2c: cross-build Honeykrisp (Mesa's Asahi Vulkan driver) for Android arm64
# with Fedora's clang, the NDK's target sysroot and the stage 2b host compilers.
# Options follow the upstream Android Mesa build in build-guest.sh (same Mesa 26.2.4).
# Produces one library under work/asahi; nothing is installed or run.
set -euo pipefail
[[ $(uname -s) == Linux ]] || { echo 'Run on Fedora Asahi Linux.' >&2; exit 1; }
[[ $(id -u) != 0 ]] || { echo 'Run as your ordinary user, without sudo.' >&2; exit 1; }
ROOT=$(cd "$(dirname "$0")/.." && pwd)
W="$ROOT/work/asahi"
NDK=android-ndk-r28c                       # = 28.2.13676358, NDK_TESTED in lib.sh
NDK_URL="https://dl.google.com/android/repository/$NDK-linux.zip"
MESA_SHA256=bce5f7fbebb934373b86c999a064d52fb5065878dc57f287f95346648ec832e9   # lib.sh pin
API=35
TAR=$(ls -t "$W"/mesa-toolchain-*/srpm/mesa-26.2.4.tar.xz 2>/dev/null | head -1 || true)
HOSTBIN=$(ls -td "$W"/mesa-host-*/prefix/bin 2>/dev/null | head -1 || true)
[[ -n $TAR ]] || { echo 'No mesa source; run stage 2a first.' >&2; exit 1; }
[[ -x $HOSTBIN/mesa_clc && -x $HOSTBIN/asahi_clc && -x $HOSTBIN/vtn_bindgen2 ]] \
    || { echo 'No stage 2b host tools; run asahi/build-mesa-host-tools.sh first.' >&2; exit 1; }
for t in clang clang++ llvm-ar llvm-strip ld.lld meson ninja python3 readelf; do
    command -v "$t" >/dev/null || { echo "Missing $t (stage 2b packages)." >&2; exit 1; }
done
echo 'THIS STEP MODIFIES STORAGE'
echo "Downloads $NDK-linux.zip once into $W/downloads (several hundred MB; kept for reuse)."
echo "Creates one new $W/honeykrisp-android-* directory (NDK sysroot extract, source, build; roughly 2 GB, an estimate)."
echo 'Nothing is installed outside these paths. Delete them to reverse.'
mkdir -p "$W/downloads"
OUT=$(mktemp -d "$W/honeykrisp-android-XXXXXXXX")
echo "Results: $OUT"
exec > >(tee "$OUT/report.txt") 2>&1

echo '== Source provenance'
echo "Fedora tarball: $TAR"
GOT=$(sha256sum "$TAR" | cut -d' ' -f1)
echo "sha256 $GOT"
[[ $GOT == "$MESA_SHA256" ]] && echo 'MESA_SOURCE matches the upstream mesa-26.2.4 pin in lib.sh' \
    || echo 'MESA_SOURCE differs from the lib.sh pin; continuing with the Fedora tarball (report this)'
echo "Host tools: $HOSTBIN"

echo '== NDK sysroot'
ZIP="$W/downloads/$NDK-linux.zip"
if [[ ! -s $ZIP ]]; then
    curl -fL --retry 3 -o "$ZIP.part" "$NDK_URL"
    mv "$ZIP.part" "$ZIP"
fi
echo "NDK zip sha256 $(sha256sum "$ZIP" | cut -d' ' -f1) (compare with Google's published checksum)"
# Only the target sysroot and the clang runtime archives are needed; the x86_64 host tools are not.
python3 -I - "$ZIP" "$OUT/ndk" <<'PY'
import sys, zipfile, os
z = zipfile.ZipFile(sys.argv[1]); dest = sys.argv[2]; n = 0
for m in z.infolist():
    p = m.filename
    if "/toolchains/llvm/prebuilt/linux-x86_64/" not in p: continue
    rel = p.split("/toolchains/llvm/prebuilt/linux-x86_64/", 1)[1]
    if not (rel.startswith("sysroot/") or (rel.startswith("lib/clang/") and "/lib/linux/" in rel)): continue
    if ".." in rel.split("/") or m.is_dir(): continue
    target = os.path.join(dest, rel); os.makedirs(os.path.dirname(target), exist_ok=True)
    with z.open(m) as s, open(target, "wb") as d: d.write(s.read())
    if (m.external_attr >> 16) & 0o111: os.chmod(target, 0o755)
    n += 1
print(f"extracted {n} files")
PY
SYSROOT="$OUT/ndk/sysroot"
NDK_RT=$(ls -d "$OUT"/ndk/lib/clang/*/lib | head -1)
# Fedora clang's own headers with the NDK's Android runtime archives (builtins, libunwind).
RES="$OUT/clang-resource"
mkdir -p "$RES"
cp -r "$(clang -print-resource-dir)/include" "$RES/include"
cp -r "$NDK_RT" "$RES/lib"
ls "$RES/lib/linux" | head

echo '== Cross configuration'
cat > "$OUT/android-aarch64.cross" <<CROSS
[constants]
common = ['--target=aarch64-linux-android$API', '--sysroot=$SYSROOT', '-resource-dir=$RES']
[binaries]
c = ['clang'] + common
cpp = ['clang++'] + common + ['-fno-exceptions', '-fno-unwind-tables', '-fno-asynchronous-unwind-tables', '--start-no-unused-arguments', '-static-libstdc++', '--end-no-unused-arguments']
ar = 'llvm-ar'
strip = 'llvm-strip'
c_ld = 'lld'
cpp_ld = 'lld'
pkg-config = '$(command -v pkg-config)'
[built-in options]
c_link_args = ['-Wl,-z,max-page-size=16384']
cpp_link_args = ['-Wl,-z,max-page-size=16384']
[host_machine]
system = 'android'
cpu_family = 'aarch64'
cpu = 'armv8'
endian = 'little'
[properties]
needs_exe_wrapper = true
pkg_config_libdir = '$OUT/android-pkgconfig'
CROSS
mkdir -p "$OUT/android-pkgconfig"
cat "$OUT/android-aarch64.cross"
tar -xf "$TAR" -C "$OUT"
S="$OUT/mesa-26.2.4"
export PATH="$HOSTBIN:$PATH"
status=0
meson setup "$OUT/build" "$S" --cross-file "$OUT/android-aarch64.cross" --buildtype=release \
    -Dforce_fallback_for=libdrm,expat -Dallow-fallback-for=libdrm -Dplatforms=android \
    -Dplatform-sdk-version=$API -Dandroid-stub=true -Dandroid-libbacktrace=disabled -Dllvm=disabled \
    -Dglx=disabled -Dgbm=disabled -Dvideo-codecs= -Dgallium-va=disabled -Dzstd=disabled \
    -Dgallium-drivers= -Dvulkan-drivers=asahi -Degl=disabled -Dgles1=disabled -Dgles2=disabled \
    -Dopengl=false -Dmesa-clc=system -Dprecomp-compiler=system || status=$?
echo "configure exit status: $status"
if [[ $status == 0 ]]; then
    echo '== Building'
    ninja -C "$OUT/build" src/asahi/vulkan/libvulkan_asahi.so || status=$?
    echo "build exit status: $status"
fi
LIB="$OUT/build/src/asahi/vulkan/libvulkan_asahi.so"
if [[ $status == 0 && -f $LIB ]]; then
    llvm-strip --strip-unneeded -o "$OUT/vulkan.asahi.so" "$LIB"
    echo '== Result (static facts only; not loaded or run)'
    ls -l "$OUT/vulkan.asahi.so"
    readelf -hW "$OUT/vulkan.asahi.so" | grep -E 'Class|Machine'
    readelf -lW "$OUT/vulkan.asahi.so" | awk '$1=="LOAD"{print "LOAD align", $NF}'
    readelf -dW "$OUT/vulkan.asahi.so" | grep -E 'NEEDED|SONAME'
    readelf -sW --dyn-syms "$OUT/vulkan.asahi.so" | grep -E ' (HMI|vk_icdGetInstanceProcAddr|vkGetInstanceProcAddr)$' || echo 'no HAL/ICD entry symbols found'
    grep -aoE 'VK_ANDROID_[a-z_]+' "$OUT/vulkan.asahi.so" | sort -u
fi
echo "Send $OUT/report.txt"
exit "$status"
