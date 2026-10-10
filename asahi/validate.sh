#!/bin/bash
# Build/run the focused native GPU milestone and retain output, stopping on failure.
set -euo pipefail
[[ $(uname -s) == Linux ]] || { echo 'Run on Fedora Asahi Linux.' >&2; exit 1; }
[[ $(id -u) != 0 ]] || { echo 'Run as your ordinary user, without sudo.' >&2; exit 1; }
ROOT=$(cd "$(dirname "$0")/.." && pwd)
echo 'THIS STEP MODIFIES STORAGE'
echo "Creates results and builds/replaces only the probe under $ROOT/work/asahi."
echo 'Existing result directories are retained; remove the new generated files to reverse.'
mkdir -p "$ROOT/work/asahi"
RESULTS=$(mktemp -d "$ROOT/work/asahi/results-XXXXXXXX")
echo "Results: $RESULTS"
run_stage() {
    local name=$1 status
    shift
    if "$@" >"$RESULTS/$name.log" 2>&1; then status=0; else status=$?; fi
    cat "$RESULTS/$name.log"
    printf '%s exit status: %s\n' "$name" "$status" | tee -a "$RESULTS/status.txt"
    if [[ $status != 0 ]]; then
        echo "Stopped at $name. Send $RESULTS/$name.log and status.txt."
        exit "$status"
    fi
}
run_stage build bash "$ROOT/asahi/build-probe.sh"
run_stage readback "$ROOT/work/asahi/vulkan-probe"
run_stage capabilities "$ROOT/work/asahi/vulkan-probe" --caps
echo "Validation complete. Send the logs from $RESULTS."
