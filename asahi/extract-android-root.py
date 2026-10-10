#!/usr/bin/env python3
"""Extract the image's partitions into one root tree under work/asahi/android-root-*.

Original project script. Runs fsck.erofs --extract inside an unprivileged user
namespace that maps your user to root and the /etc/subuid range to ids 1..65535,
so Android owners (system 1000, logd 1036, graphics 1003, ...) are recorded as
sub-ids on disk without any sudo. The image is only read. Nothing from the tree
is executed here. Layout: system_a is the root (it holds /system), vendor_a ->
/vendor, product_a -> /product, system_ext_a -> /system_ext.
"""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

NS = ["unshare", "--user", "--map-auto", "--map-root-user"]
PLACEMENT = [("system_a", ""), ("vendor_a", "vendor"), ("product_a", "product"), ("system_ext_a", "system_ext")]
MIN_FREE = 30 << 30


def ns(command, timeout=3600, check=False):
    done = subprocess.run(NS + command, capture_output=True, text=True, timeout=timeout)
    if check and done.returncode:
        sys.exit(f"FAILED in namespace: {' '.join(command)}\n{done.stdout}{done.stderr}")
    return done


def main():
    if os.geteuid() == 0:
        sys.exit("run as your ordinary user, without sudo")
    print("=== BRING-UP THREAD, STAGE B1b: EXTRACT ROOT TREE (paste in the \"Remaining steps to boot Android\" thread) ===")
    parent = Path("work/asahi")
    data = None
    for path in sorted(parent.glob("recovery-audit-*/summary.json"), key=lambda x: x.stat().st_mtime, reverse=True):
        loaded = json.loads(path.read_text())
        if loaded.get("exit_status") == 0:
            data = loaded
            break
    if data is None:
        sys.exit("no completed recovery-audit summary found")
    image = Path(data["image"]).resolve()
    spec = importlib.util.spec_from_file_location("recovery_audit", Path(__file__).with_name("audit-recovery.py"))
    recovery = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(recovery)
    _, logical = recovery.partitions(image)
    free = shutil.disk_usage(parent).free
    if free < MIN_FREE:
        sys.exit(f"only {free / 2**30:.1f} GiB free under work/asahi; need {MIN_FREE >> 30} GiB")
    probe = ns(["sh", "-c", "cat /proc/self/uid_map; id -u"], 30)
    print("Namespace id map seen inside (inner outer count):")
    print(probe.stdout.strip() or probe.stderr.strip())
    lines = probe.stdout.split()
    if probe.returncode or "65536" not in lines:
        sys.exit("the namespace did not get the 65536-id range; stopping before anything is written")
    print("THIS STEP MODIFIES STORAGE")
    print("Creates work/asahi/android-root-* with the extracted partitions (roughly 4-10 GiB). The image is only read;")
    print("nothing is executed from the tree. Files are owned by sub-ids, so delete the tree with the command printed at the end.")
    parent.mkdir(parents=True, exist_ok=True)
    out = Path(tempfile.mkdtemp(prefix="android-root-", dir=parent)).resolve()
    root = out / "root"
    root.mkdir()
    results = {}
    for part, sub in PLACEMENT:
        if part not in logical:
            print(f"SKIPPED {part}: not in this image")
            continue
        extents = logical[part]["extents"]
        if len(extents) != 1 or "offset" not in extents[0]:
            print(f"SKIPPED {part}: not one linear extent")
            continue
        target = root / sub if sub else root
        if sub:
            if target.is_symlink():
                print(f"{sub}: replacing the image's symlink {os.readlink(target)} in OUR tree with a directory")
                ns(["rm", str(target)], 60, True)
            ns(["mkdir", "-p", str(target)], 60, True)
        start = time.time()
        done = ns(["fsck.erofs", f"--extract={target}", f"--offset={extents[0]['offset']}", str(image)])
        text = (done.stdout + done.stderr)
        (out / f"extract-{part}.log").write_text(text)
        results[part] = {"exit": done.returncode, "seconds": round(time.time() - start)}
        errors = [l for l in text.splitlines() if "rror" in l or "ailed" in l or "arn" in l]
        print(f"{part}: fsck.erofs exit {done.returncode} in {results[part]['seconds']} s, {len(errors)} warning/error lines")
        for line in errors[:6]:
            print(f"    {line[:200]}")
    print("\nTOP LEVEL of the tree (owner uid:gid mode name), as seen in the namespace:")
    print(ns(["sh", "-c", f"cd {root} && ls -ln | head -40"], 120).stdout)
    print("SIZES:")
    print(ns(["sh", "-c", f"cd {root} && du -sh . system vendor product system_ext 2>/dev/null"], 600).stdout)
    print("OWNER COUNTS (uid, number of entries; 0 = root, 1000 = system, 1036 = logd):")
    print(ns(["sh", "-c", f"find {root} -printf '%U\\n' | sort -n | uniq -c | sort -rn | head -12"], 900).stdout)
    print("SAMPLES:")
    print(ns(["sh", "-c", f"cd {root} && ls -lnd system/bin/init system/bin/logd system/bin/servicemanager "
                          f"system/bin/surfaceflinger system/bin/app_process64 system/apex "
                          f"vendor/bin/hw/android.hardware.composer.hwc3-service.drm system/etc/init/hw/init.rc 2>&1"], 120).stdout)
    (out / "summary.json").write_text(json.dumps({"image": str(image), "root": str(root), "results": results}, indent=2))
    print(f"Tree: {root}")
    print(f"To remove it (sub-id owned files need the namespace):\n  unshare --user --map-auto --map-root-user rm -rf {out}")
    print(f"\nSend the terminal output above (summary: {out}/summary.json)")


if __name__ == "__main__":
    main()
