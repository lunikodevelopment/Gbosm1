#!/usr/bin/env python3
"""List the erofs payloads inside the previously copied apex files; runs nothing.

Original project script. Reads the newest work/asahi/apex-scan-*/ copies,
locates each stored apex_payload.img inside its zip, and lists selected
directories with dump.erofs --offset. Nothing is mounted, extracted or run.
"""
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import zipfile

DIRS = ["/", "/bin", "/lib64", "/javalib", "/etc", "/etc/tz", "/etc/icu", "/etc/security"]
KEY = {"com.android.runtime": ["bin/linker64", "lib64/bionic/libc.so", "bin/dalvikvm64"],
       "com.google.android.art": ["bin/dalvikvm64", "bin/dex2oat64", "lib64/libart.so", "javalib/core-oj.jar",
                                  "javalib/core-libart.jar", "javalib/okhttp.jar"],
       "com.android.i18n": ["lib64/libicu.so", "etc/icu"],
       "com.google.android.tzdata6": ["etc/tz/tzdata"],
       "com.google.android.conscrypt": ["lib64/libjavacrypto.so", "javalib/conscrypt.jar"]}


def payload_offset(zf, info):
    raw = zf.fp
    raw.seek(info.header_offset)
    header = raw.read(30)
    name_len, extra_len = struct.unpack("<HH", header[26:30])
    return info.header_offset + 30 + name_len + extra_len


def main():
    if os.geteuid() == 0:
        sys.exit("run as your ordinary user, without sudo")
    if not shutil.which("dump.erofs"):
        sys.exit("dump.erofs is missing")
    scans = sorted(Path("work/asahi").glob("apex-scan-*/summary.json"), key=lambda x: x.stat().st_mtime, reverse=True)
    if not scans:
        sys.exit("no apex-scan result found; run audit-apex.py first")
    src = scans[0].parent
    print("THIS STEP MODIFIES STORAGE")
    print("Creates one new apex-payload report directory under work/asahi (text only). Nothing is mounted or run.")
    print("Delete the newly printed result directory to reverse it.")
    out = Path(tempfile.mkdtemp(prefix="apex-payload-", dir="work/asahi")).resolve()
    print(f"Results: {out}  (from {src})")
    report = {}
    for name, keys in KEY.items():
        apex = src / f"{name}.apex"
        if not apex.exists():
            print(f"{name}: copy missing")
            continue
        with zipfile.ZipFile(apex) as zf:
            info = zf.getinfo("apex_payload.img")
            off = payload_offset(zf, info)
        entry = report[name] = {"offset": off, "dirs": {}}
        print(f"\n{name} (payload offset {off}):")
        for d in DIRS:
            r = subprocess.run(["dump.erofs", f"--offset={off}", f"--path={d}", "--ls", str(apex)],
                               capture_output=True, timeout=120)
            if r.returncode:
                continue
            lines = [l.split()[-1] for l in r.stdout.decode(errors="replace").splitlines() if l.strip()]
            names = [n for n in lines if n not in (".", "..")]
            entry["dirs"][d] = names
            print(f"  {d}: {len(names)} entries" + (f": {' '.join(names[:40])}" if d in ("/", "/bin", "/javalib") else ""))
        for k in keys:
            parent_dir, base = ("/" + k).rsplit("/", 1)
            present = base in entry["dirs"].get(parent_dir or "/", [])
            print(f"  key {k}: {'present' if present else 'NOT FOUND'}")
    (out / "summary.json").write_text(json.dumps(report, indent=2))
    print(f"\nSend the terminal output above (summary: {out}/summary.json)")


if __name__ == "__main__":
    main()
