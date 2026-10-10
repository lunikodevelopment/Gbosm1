#!/bin/bash
# Graphics stage 3a: read minigbm's source to choose how the Android allocator and mapper
# reach the Asahi GPU and display. Clones two public repositories (shallow), builds nothing,
# runs nothing from them and touches no device.
set -uo pipefail
[[ $(uname -s) == Linux ]] || { echo 'Run on Fedora Asahi Linux.' >&2; exit 1; }
[[ $(id -u) != 0 ]] || { echo 'Run as your ordinary user, without sudo.' >&2; exit 1; }
command -v git >/dev/null || { echo 'Missing git.' >&2; exit 1; }
ROOT=$(cd "$(dirname "$0")/.." && pwd)
echo 'THIS STEP MODIFIES STORAGE'
echo "Creates one new $ROOT/work/asahi/minigbm-src-* directory with two shallow git clones and a report."
echo 'Nothing is built, installed or executed from them. Delete the new directory to reverse.'
mkdir -p "$ROOT/work/asahi"
OUT=$(mktemp -d "$ROOT/work/asahi/minigbm-src-XXXXXXXX")
echo "Results: $OUT"
exec > >(tee "$OUT/report.txt") 2>&1

# AOSP's copy is what Android builds; ChromiumOS is minigbm's upstream.
declare -A REPOS=([aosp]=https://android.googlesource.com/platform/external/minigbm
                  [cros]=https://chromium.googlesource.com/chromiumos/platform/minigbm)
for name in aosp cros; do
    echo "== $name: ${REPOS[$name]}"
    if ! git clone -q --depth 1 "${REPOS[$name]}" "$OUT/$name"; then echo "CLONE=FAIL $name"; continue; fi
    git -C "$OUT/$name" log -1 --format='COMMIT %H %cI %s'
done

for name in aosp cros; do
    S=$OUT/$name
    [[ -d $S ]] || continue
    echo
    echo "######## $name"
    echo '== Top-level files'
    ls "$S" | tr '\n' ' '; echo
    echo '== License files'
    ls "$S" | grep -i -E 'license|notice|copying' || echo 'none at top level'
    echo '== Mentions of asahi or apple (any file)'
    grep -rn -i -E '\basahi\b|"apple"' "$S" --include='*.c' --include='*.cc' --include='*.h' \
        --include='*.bp' --include='*.mk' | head -20 || true
    echo '== Backend selection in drv.c'
    grep -n -E 'backend|DRV_[A-Z0-9_]+|drmGetVersion|->name' "$S/drv.c" | head -80
    echo '== Generic dumb-buffer backends (dumb_driver.c)'
    [[ -f $S/dumb_driver.c ]] && grep -n -E 'INIT_DUMB_DRIVER|struct backend|\.name|"' "$S/dumb_driver.c" | head -60
    echo '== How the gralloc layer opens DRM nodes'
    grep -rn -E 'renderD|/dev/dri|card%|DRM_NODE|drmOpen|minigbm.*node|getenv|property_get' \
        "$S/cros_gralloc" 2>/dev/null | grep -v -E '/tests?/' | head -40
    echo '== Android build targets and their backend flags'
    for bp in "$S/Android.bp" "$S"/cros_gralloc/*/Android.bp; do
        [[ -f $bp ]] || continue
        echo "-- ${bp#$S/}"
        grep -n -E '^\s*(cc_[a-z_]+|name:|cflags|"-D|shared_libs|static_libs|vendor|relative_install_path|srcs)' "$bp" | head -120
    done
    echo '== Mapper and allocator front ends'
    find "$S/cros_gralloc" -maxdepth 2 -type d | sed "s|$S/||" | sort
    ls "$S"/cros_gralloc/mapper_stablec 2>/dev/null
    ls "$S"/cros_gralloc/aidl 2>/dev/null
    echo '== Uses of Mesa GBM or DRI from inside minigbm'
    grep -rln -E 'gbm_create_device|__DRI|dri_interface|libgallium|dlopen' "$S" --include='*.c' --include='*.cc' \
        --include='*.h' | sed "s|$S/||" | head -20
    [[ -f $S/dri.c ]] && grep -n -E 'dlopen|DRI_PATH|_dri\.so|gallium|__DRI[A-Z_]+' "$S/dri.c" | head -40
done
echo
echo "Send $OUT/report.txt"
