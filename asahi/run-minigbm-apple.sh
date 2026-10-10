#!/bin/bash
# Graphics stage 3d: copy the AOSP minigbm clone from stage 3a, add an `apple` dumb-buffer backend
# (three small anchored edits), build it natively, and run its gbm_bo_create through the
# Honeykrisp round trip. Android/Bionic is not involved yet.
set -uo pipefail
[[ $(uname -s) == Linux ]] || { echo 'Run on Fedora Asahi Linux.' >&2; exit 1; }
[[ $(id -u) != 0 ]] || { echo 'Run as your ordinary user, without sudo.' >&2; exit 1; }
ROOT=$(cd "$(dirname "$0")/.." && pwd)
for c in cc make python3 pkg-config nm; do command -v $c >/dev/null || { echo "Missing $c." >&2; exit 1; }; done
for pc in vulkan:vulkan-loader-devel libdrm:libdrm-devel; do
    pkg-config --exists "${pc%%:*}" || { echo "Missing ${pc%%:*} development files (Fedora package: ${pc#*:})." >&2; exit 1; }
done
SRC=$(ls -d "$ROOT"/work/asahi/minigbm-src-*/aosp 2>/dev/null | tail -1)
[[ -n $SRC ]] || { echo 'Run stage 3a first (asahi/inspect-minigbm-source.sh).' >&2; exit 1; }
echo 'THIS STEP MODIFIES STORAGE'
echo "Creates one new $ROOT/work/asahi/minigbm-apple-* directory: a copy of the AOSP minigbm clone"
echo 'with the patch applied, its native build output, the probe and report.txt. The stage 3a clone is only read.'
echo 'Allocates one 256x256 dumb buffer on the display card node for the run only; no modeset, no DRM master.'
echo 'Delete the new directory to reverse.'
mkdir -p "$ROOT/work/asahi"
OUT=$(mktemp -d "$ROOT/work/asahi/minigbm-apple-XXXXXXXX")
echo "Results: $OUT"
exec > >(tee "$OUT/report.txt") 2>&1
echo "Source: $SRC ($(git -C "$SRC" log -1 --format='%H %cI'))"
cp -a "$SRC" "$OUT/minigbm" && rm -rf "$OUT/minigbm/.git"
cp -a "$OUT/minigbm" "$OUT/minigbm.orig"

python3 - "$OUT/minigbm" <<'PY'
import sys
from pathlib import Path
root = Path(sys.argv[1])
def edit(name, old, new):
    p = root / name
    s = p.read_text()
    if s.count(old) != 1:
        sys.exit(f'PATCH_FAIL {name}: anchor {old!r} found {s.count(old)} times')
    p.write_text(s.replace(old, new))
edit('drv.c', 'extern const struct backend backend_evdi;', 'extern const struct backend backend_apple;\nextern const struct backend backend_evdi;')
edit('drv.c', '&backend_evdi,', '&backend_apple,     &backend_evdi,')
edit('dumb_driver.c', 'INIT_DUMB_DRIVER(evdi)', 'INIT_DUMB_DRIVER(apple)\nINIT_DUMB_DRIVER(evdi)')
print('PATCH_OK: apple dumb backend added to drv.c (2 edits) and dumb_driver.c (1 edit)')
PY
[[ ${PIPESTATUS[0]} == 0 ]] || { echo "Send $OUT/report.txt"; exit 1; }
diff -u -r "$OUT/minigbm.orig" "$OUT/minigbm" > "$OUT/minigbm-apple.patch" || true
cat "$OUT/minigbm-apple.patch"
rm -rf "$OUT/minigbm.orig"

echo '== Makefile (head)'
sed -n 1,60p "$OUT/minigbm/Makefile"
echo '== Build (make -j4)'
if make -C "$OUT/minigbm" -j4 >"$OUT/make.log" 2>&1; then echo 'make exit status: 0'; else echo "make exit status: $?"; fi
tail -40 "$OUT/make.log"
LIB=
while IFS= read -r f; do
    if nm -D "$f" 2>/dev/null | grep -q ' T gbm_create_device'; then LIB=$f; break; fi
done < <(find "$OUT/minigbm" -name '*.so*' -type f)
echo "Built gbm library: ${LIB:-none}"
[[ -n $LIB ]] || { echo "Stopped: no library exporting gbm_create_device. Send $OUT/report.txt"; exit 1; }

echo '== Probe'
cc -std=gnu11 -O2 -Wall -Wextra $(pkg-config --cflags vulkan libdrm) "$ROOT/asahi/probe-linear-dmabuf.c" \
    -o "$OUT/probe-linear-dmabuf" $(pkg-config --libs vulkan libdrm) -ldl 2>&1 || { echo "probe build failed. Send $OUT/report.txt"; exit 1; }
"$OUT/probe-linear-dmabuf" "$LIB" 2>&1
echo "probe exit status: $?"
echo "Send $OUT/report.txt"
