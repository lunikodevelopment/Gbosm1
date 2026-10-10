#!/bin/bash
# Graphics stage 2a: what building Honeykrisp for Android needs on this machine.
# Installs nothing and builds nothing; downloads Fedora's mesa source package once.
set -uo pipefail
[[ $(uname -s) == Linux ]] || { echo 'Run on Fedora Asahi Linux.' >&2; exit 1; }
[[ $(id -u) != 0 ]] || { echo 'Run as your ordinary user, without sudo.' >&2; exit 1; }
ROOT=$(cd "$(dirname "$0")/.." && pwd)
echo 'THIS STEP MODIFIES STORAGE'
echo "Creates one new $ROOT/work/asahi/mesa-toolchain-* directory with Fedora's mesa source RPM and notes."
echo 'Nothing is installed or built. Delete the new directory to reverse.'
mkdir -p "$ROOT/work/asahi"
OUT=$(mktemp -d "$ROOT/work/asahi/mesa-toolchain-XXXXXXXX")
echo "Results: $OUT"
exec > >(tee "$OUT/report.txt") 2>&1

echo '== Installed Mesa and kernel'
rpm -q mesa-vulkan-drivers mesa-libgbm kernel-core
uname -r

echo '== Host build tools (installed or not)'
for p in gcc clang lld llvm llvm-devel clang-devel meson ninja-build python3-mako python3-pyyaml \
         python3-packaging python3-pycparser bison flex glslang spirv-tools spirv-llvm-translator \
         spirv-llvm-translator-devel libclc libclc-devel rpm-build dnf-plugins-core; do
    rpm -q "$p" >/dev/null 2>&1 && echo "INSTALLED $p $(rpm -q "$p")" || echo "MISSING $p"
done
echo '== Missing ones available from the repositories'
dnf -q list --available llvm-devel clang-devel lld spirv-llvm-translator-devel spirv-tools-devel \
    libclc-devel glslang meson python3-mako python3-pyyaml 2>/dev/null | sed 's/^/AVAILABLE /'

echo '== Android NDK'
for d in "${ANDROID_NDK_HOME:-}" "${ANDROID_NDK:-}" ~/Android/Sdk/ndk/* ~/android-ndk-*; do
    [[ -n $d && -d $d ]] && echo "NDK $d" && ls "$d/toolchains/llvm/prebuilt" 2>/dev/null
done
echo 'NDK note: Google publishes NDK host tools for x86_64 Linux and macOS only.'

echo '== Fedora mesa source package (same version as the running driver)'
VER=$(rpm -q --qf '%{VERSION}-%{RELEASE}' mesa-vulkan-drivers)
# dnf5 spells the option --srpm; older dnf uses --source.
( cd "$OUT" && { dnf download --srpm "mesa-$VER" 2>/dev/null || dnf download --source "mesa-$VER"; } ) || echo "SOURCE_DOWNLOAD=FAIL"
SRPM=$(ls "$OUT"/mesa-*.src.rpm 2>/dev/null | head -1)
if [[ -n $SRPM ]]; then
    mkdir -p "$OUT/srpm" && ( cd "$OUT/srpm" && if command -v cpio >/dev/null; then rpm2cpio "$SRPM" | cpio -idm --quiet; else rpm2archive - <"$SRPM" | tar -xz; fi )
    echo "SRPM $SRPM"
    ls "$OUT/srpm"
    echo '-- meson options in mesa.spec'
    grep -nE '^\s*-D|%meson|vulkan-drivers|gallium-drivers|mesa-clc|clc' "$OUT/srpm/mesa.spec" | head -80
    TAR=$(ls "$OUT"/srpm/mesa-*.tar.* 2>/dev/null | head -1)
    if [[ -n $TAR ]]; then
        mkdir -p "$OUT/src" && tar -xf "$TAR" -C "$OUT/src" --wildcards '*/meson.build' '*/meson_options.txt' \
            '*/meson.options' '*/src/asahi/*' '*/docs/android.rst' 2>/dev/null
        S=$(ls -d "$OUT"/src/mesa-* | head -1)
        echo "SOURCE $S"
        echo '-- asahi and android build requirements in the source'
        grep -nE "clc|precomp|libclc|llvm" "$S/src/asahi/meson.build" "$S/src/asahi/vulkan/meson.build" 2>/dev/null | head -40
        grep -nE "'(mesa-clc|precomp-compiler|install-mesa-clc|install-precomp-compiler|platform-sdk-version|android-stub|android-libbacktrace)'" \
            "$S/meson_options.txt" "$S/meson.options" 2>/dev/null
        grep -nE "with_android_stub|android.*asahi|asahi.*android" "$S/meson.build" | head -20
        [[ -f $S/docs/android.rst ]] && cp "$S/docs/android.rst" "$OUT/android.rst" && echo "Copied docs/android.rst"
    fi
fi
echo "Send $OUT/report.txt"
