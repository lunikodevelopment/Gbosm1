#!/usr/bin/env python3
"""Read-only static ranking: which Binder-linked image executables could serve a test call.

Original project script. Reads the recovery image and an earlier binder-clients
scan only; writes a new report directory under work/asahi. Nothing is executed.
String hits are hints, not proof of behavior. Question being narrowed: which
image program registers a Binder service and could read a file descriptor from
a call, so that Android *receiving* an FD can be tested without a native server.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import tempfile

LIMIT_BYTES = 64 << 20
# Libraries already in the prepared runtime (earlier extractions); anything else is extra work.
KNOWN = {"libc.so", "libm.so", "libdl.so", "libc++.so", "liblog.so", "libbase.so", "libcutils.so", "libutils.so",
         "libbinder.so", "libselinux.so", "libpcre2.so", "libpackagelistparser.so", "libvndksupport.so",
         "libapexsupport.so", "libdl_android.so", "libperfetto_c.so", "libvintf.so", "libtinyxml2.so", "libz.so"}
PATTERNS = {
    "adds_service": rb"(addService|AServiceManager_addService|AServiceManager_registerLazyService|ABinderProcess_joinThreadPool|joinThreadPool)",
    "register_lazy": rb"(registerLazyService|LazyServiceRegistrar)",
    "reads_fd": rb"(readFileDescriptor|readParcelFileDescriptor|AParcel_readParcelFileDescriptor|AParcel_readRequiredParcelFileDescriptor|ParcelFileDescriptor)",
    "dump": rb"(dumpsys|::dump\b|onDump|dump_transaction|_DMP)",
    "vintf": rb"(IServiceManager::isDeclared|vintf|VintfObject|\.rc\b)",
    "hal": rb"(android\.hardware\.|hidl|HwBinder|/dev/hwbinder|/dev/vndbinder)",
    "properties": rb"(__system_property|ro\.[a-z_.]+|persist\.[a-z_.]+|init\.svc)",
}


def load_helpers():
    spec = importlib.util.spec_from_file_location("recovery_audit", Path(__file__).with_name("audit-recovery.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--previous", help="completed binder-clients directory (default: latest)")
    args = p.parse_args()
    if os.geteuid() == 0:
        p.error("run as your ordinary user, without sudo")
    if not shutil.which("dump.erofs"):
        p.error("dump.erofs is missing")
    parent = Path("work/asahi")
    candidates = [Path(args.previous)] if args.previous else sorted(
        parent.glob("binder-clients-*/summary.json"), key=lambda x: x.stat().st_mtime, reverse=True)
    data = None
    for candidate in candidates:
        path = candidate / "summary.json" if candidate.is_dir() else candidate
        loaded = json.loads(path.read_text())
        if loaded.get("binder_clients"):
            data = loaded
            break
    if data is None:
        p.error("no binder-clients summary found; run asahi/audit-binder-clients.py first")
    image = Path(data["image"])
    helpers = load_helpers()
    _, logical = helpers.partitions(image)
    offsets = {n: v["extents"][0]["offset"] for n, v in logical.items()
               if len(v["extents"]) == 1 and "offset" in v["extents"][0]}
    print("THIS STEP MODIFIES STORAGE")
    print("Creates one new binder-servers report directory under work/asahi. The image is only read.")
    print("Delete the newly printed result directory to reverse it.")
    parent.mkdir(parents=True, exist_ok=True)
    out = Path(tempfile.mkdtemp(prefix="binder-servers-", dir=parent)).resolve()
    print(f"Results: {out}")
    rows, skipped = [], []
    for item in data["binder_clients"]:
        part, path = item["path"].split(":", 1)
        if part not in offsets or not re.fullmatch(r"/[A-Za-z0-9_./@+-]+", path):
            skipped.append(item["path"] + ": unsupported partition or path")
            continue
        run = subprocess.run(["dump.erofs", f"--offset={offsets[part]}", f"--path={path}", "--cat", str(image)],
                             capture_output=True, timeout=120)
        content = run.stdout
        if run.returncode or len(content) > LIMIT_BYTES:
            skipped.append(item["path"] + ": extraction failed or over size limit")
            continue
        try:
            info = helpers.elf_info(content)
        except (ValueError, KeyError, UnicodeError, struct.error):
            skipped.append(item["path"] + ": not parseable as ELF")
            continue
        hits = {k: bool(re.search(v, content)) for k, v in PATTERNS.items()}
        extra = sorted(set(info["needed"]) - KNOWN - {"ld-android.so"})
        rows.append({"path": item["path"], "bytes": len(content), "needed": info["needed"],
                     "extra_libraries": extra, **hits})
    # Rank: registers a service, reads FDs, no HAL, fewest extra libraries, smallest.
    rows.sort(key=lambda r: (not r["adds_service"], not r["reads_fd"], r["hal"], len(r["extra_libraries"]), r["bytes"]))
    for r in rows:
        flags = ",".join(k for k in PATTERNS if r[k]) or "-"
        print(f"{r['path']} size={r['bytes']} extra_libs={len(r['extra_libraries'])} hints={flags}")
    for s in skipped:
        print("SKIPPED " + s)
    summary = {"image": str(image), "previous": data.get("image"), "candidates": rows, "skipped": skipped,
               "limitations": ["Static strings and DT_NEEDED only; nothing was executed.",
                               "A string hit does not show a program serves a call, reads an FD, or can start without Android init.",
                               "extra_libraries lists libraries outside the set already prepared for the servicemanager runtime."]}
    (out / "summary.json").write_text(json.dumps(summary, indent=2))
    print(f"{len(rows)} executables ranked; skipped: {len(skipped)}")
    print(f"Send {out}/summary.json")


if __name__ == "__main__":
    main()
