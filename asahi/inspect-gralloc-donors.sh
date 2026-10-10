#!/bin/bash
# Graphics stage 3h (read only): are prebuilt Android minigbm allocator/mapper binaries already on this
# machine (the inherited VM port used Cuttlefish ones), and could one of them take an `apple` backend?
# Searches the checkout for them and reports ELF facts; downloads, builds and runs nothing.
set -uo pipefail
[[ $(uname -s) == Linux ]] || { echo 'Run on Fedora Asahi Linux.' >&2; exit 1; }
[[ $(id -u) != 0 ]] || { echo 'Run as your ordinary user, without sudo.' >&2; exit 1; }
for c in readelf strings nm; do command -v $c >/dev/null || { echo "Missing $c (Fedora package: binutils)." >&2; exit 1; }; done
ROOT=$(cd "$(dirname "$0")/.." && pwd)
W=$ROOT/work/asahi
echo 'THIS STEP MODIFIES STORAGE'
echo "Creates one new $W/gralloc-donors-* directory holding only report.txt. Everything else is only read."
echo 'Delete the new directory to reverse.'
mkdir -p "$W"
OUT=$(mktemp -d "$W/gralloc-donors-XXXXXXXX")
echo "Results: $OUT"
exec > >(tee "$OUT/report.txt") 2>&1

echo '== artifacts/ at the checkout root (untracked in git status)'
ls -la "$ROOT/artifacts" 2>&1 | head -30
find "$ROOT/artifacts" -maxdepth 3 2>/dev/null | head -60

echo '== Prebuilt minigbm / graphics HAL files anywhere in the checkout (own stage 3 builds excluded)'
find "$ROOT" \( -path "$W/minigbm-*" -o -path "$W/honeykrisp-*" -o -path "$W/gfx-bionic-*" \) -prune -o -type f \
    \( -name 'libminigbm*.so*' -o -name 'mapper.minigbm*' -o -name 'gralloc.*.so' \
       -o -name 'android.hardware.graphics.allocator*' -o -name 'libvm_minigbm_align.so' \) -print 2>/dev/null | head -40

DONORS=$(find "$ROOT" \( -path "$W/minigbm-*" -o -path "$W/honeykrisp-*" -o -path "$W/gfx-bionic-*" \) -prune -o -type f \
    \( -name 'libminigbm_gralloc.so' -o -name 'mapper.minigbm.so' -o -name 'android.hardware.graphics.allocator-service.minigbm' \) -print 2>/dev/null | head -6)
[[ -n $DONORS ]] || echo 'No prebuilt minigbm donor binaries found in the checkout.'
for f in $DONORS; do
    echo
    echo "######## $f ($(stat -c %s "$f") bytes)"
    readelf -h "$f" | grep -E 'Class|Machine|Type'
    echo '-- PT_LOAD alignment:'; readelf -lW "$f" | grep -E ' LOAD ' | awk '{print $NF}' | sort | uniq -c
    echo '-- dynamic:'; readelf -d "$f" | grep -E 'NEEDED|SONAME'
    echo '-- DRM driver name strings (offset string):'
    strings -a -t x "$f" | grep -E ' (amdgpu|i915|xe|msm|vc4|evdi|komeda|marvell|mediatek|meson|nouveau|radeon|rockchip|sun4i-drm|synaptics|udl|vkms|virtgpu|apple|asahi)$' | head -30
    echo '-- cros_gralloc_driver symbols:'
    nm -D --defined-only "$f" 2>/dev/null | c++filt | grep -E 'cros_gralloc_driver|cros_gralloc_buffer' | head -12
    echo '-- version-ish strings:'
    strings -a "$f" | grep -i -E 'minigbm|android 1[0-9]|ro\.(hardware|vendor)\.[a-z_.]+' | sort -u | head -12
done
for j in $(find "$ROOT/artifacts" -maxdepth 4 -name 'manifest.json' 2>/dev/null | head -3); do echo "-- $j"; head -c 1500 "$j"; echo; done
echo
echo "Send $OUT/report.txt"
