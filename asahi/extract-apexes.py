#!/usr/bin/env python3
"""Extract the apex payloads of the extracted root tree into its /apex/<name> directories.

Original project script. Reads each *.apex (a zip whose stored, uncompressed
apex_payload.img is an erofs image) from system/apex, system_ext/apex, vendor/apex
and product/apex of the newest work/asahi/android-root-* tree, finds the apex
name in its apex_manifest.pb, and runs fsck.erofs --extract into
root/apex/<name>, inside the same unprivileged user namespace as
extract-android-root.py. Only that tree is written; nothing is executed from it.
"""
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import zipfile

NS = ["unshare", "--user", "--map-auto", "--map-root-user"]
SOURCES = ["system/apex", "system_ext/apex", "vendor/apex", "product/apex"]
CHECKS = ["apex/com.android.runtime/bin/linker64", "apex/com.android.runtime/bin/linkerconfig",
          "apex/com.android.runtime/lib64/bionic/libc.so", "apex/com.android.art/bin/dalvikvm64",
          "apex/com.android.art/javalib/core-oj.jar", "apex/com.android.i18n/etc/icu",
          "apex/com.android.tzdata/etc/tz/tzdata", "apex/com.android.conscrypt/lib64/libjavacrypto.so",
          "apex/com.android.sdkext/bin/derive_classpath", "apex/com.android.sdkext/bin/derive_sdk"]


def ns(command, timeout=900):
    return subprocess.run(NS + command, capture_output=True, text=True, timeout=timeout)


def payload_span(path):
    """(offset, size) of a stored apex_payload.img inside the zip, or None."""
    with zipfile.ZipFile(path) as z:
        try:
            info = z.getinfo("apex_payload.img")
        except KeyError:
            return None
        if info.compress_type != 0:
            return None
        with open(path, "rb") as f:
            f.seek(info.header_offset)
            header = f.read(30)
        name_len, extra_len = struct.unpack_from("<HH", header, 26)
        return info.header_offset + 30 + name_len + extra_len, info.file_size


def manifest_name(path, offset):
    raw = subprocess.run(NS + ["dump.erofs", f"--offset={offset}", "--path=/apex_manifest.pb", "--cat", str(path)],
                         capture_output=True, timeout=60).stdout
    if len(raw) > 2 and raw[0] == 0x0A and raw[1] < 0x80 and 2 + raw[1] <= len(raw):
        return raw[2:2 + raw[1]].decode(errors="replace")
    return None


def main():
    if os.geteuid() == 0:
        sys.exit("run as your ordinary user, without sudo")
    print("=== BRING-UP THREAD, STAGE B2a: EXTRACT APEX PAYLOADS (paste in the \"Remaining steps to boot Android\" thread) ===")
    trees = sorted(Path("work/asahi").glob("android-root-*/root"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not trees:
        sys.exit("no work/asahi/android-root-* tree; run extract-android-root.py first")
    root = trees[0].resolve()
    print(f"Tree: {root}")
    print("THIS STEP MODIFIES STORAGE")
    print("Writes only under that tree's apex/ directory (a few hundred MB to 2 GiB). The .apex files are only read.")
    print("Reverse: unshare --user --map-auto --map-root-user sh -c 'rm -rf " + str(root) + "/apex/*'")
    results, names = [], {}
    for source in SOURCES:
        directory = root / source
        if not directory.is_dir():
            print(f"(no {source})")
            continue
        for path in sorted(directory.iterdir()):
            if path.suffix not in (".apex", ".capex"):
                continue
            entry = {"file": f"{source}/{path.name}", "bytes": path.stat().st_size}
            span = payload_span(path) if path.suffix == ".apex" else None
            if span is None:
                entry["status"] = "SKIPPED: compressed apex or no stored payload"
                results.append(entry)
                continue
            offset, size = span
            name = manifest_name(path, offset)
            if not name or "/" in name or name.startswith("."):
                entry["status"] = f"SKIPPED: no readable apex name (payload at {offset}, {size} bytes)"
                results.append(entry)
                continue
            if name in names:
                entry["status"] = f"SKIPPED: name {name} already extracted from {names[name]}"
                results.append(entry)
                continue
            names[name] = entry["file"]
            target = root / "apex" / name
            ns(["mkdir", "-p", str(target)], 60)
            done = ns(["fsck.erofs", f"--extract={target}", f"--offset={offset}", str(path)])
            entry.update(name=name, status="ok" if done.returncode == 0 else f"fsck.erofs exit {done.returncode}",
                         detail=(done.stdout + done.stderr).strip()[:300])
            results.append(entry)
    ok = [r for r in results if r["status"] == "ok"]
    print(f"\nAPEXES: {len(ok)} extracted, {len(results) - len(ok)} not")
    for r in results:
        print(f"  {r['status'][:60]:60} {r.get('name', '-'):45} {r['file']}")
        if r["status"].startswith("fsck") and r.get("detail"):
            print(f"      {r['detail'][:200]}")
    print("\nKEY FILES:")
    for check in CHECKS:
        print(f"  {'present' if (root / check).exists() else 'missing':8} {check}")
    print("\nSYMLINKS the tree relies on:")
    for link in ("system/bin/linker64", "system/lib64/libc.so", "system/bin/hwservicemanager"):
        p = root / link
        print(f"  {link}: {os.readlink(p) if p.is_symlink() else ('file, ' + str(p.stat().st_size) + ' bytes' if p.exists() else 'missing')}")
    print("\nSIZES:")
    print(ns(["sh", "-c", f"du -sh {root}/apex {root}"], 600).stdout)
    (root.parent / "apexes.json").write_text(json.dumps(results, indent=2))
    print(f"Send the terminal output above (details: {root.parent}/apexes.json)")


if __name__ == "__main__":
    main()
