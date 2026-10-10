#!/usr/bin/env python3
"""Read-only static scan: which image executables link Android Binder libraries.

Reads the recovery image only; writes a new report directory under work/asahi.
No Android binary is executed. Linking libbinder does not show a program can
read a file descriptor from a reply; it only narrows candidates for review.
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
LIMIT_ENTRIES = 600
BINDER_LIBS = ("libbinder.so", "libbinder_ndk.so")


def load_helpers():
    path = Path(__file__).with_name("audit-recovery.py")
    spec = importlib.util.spec_from_file_location("recovery_audit", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def listing_names(text):
    result = []
    for line in text.splitlines():
        m = re.match(r"\s*\d+\s+(\d+)\s+([A-Za-z0-9_.@+-]+)\s*$", line)
        if m and m[2] not in (".", ".."):
            result.append(m[2])
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--previous", help="completed recovery-audit directory (default: latest)")
    args = p.parse_args()
    if os.geteuid() == 0:
        p.error("run as your ordinary user, without sudo")
    if not shutil.which("dump.erofs"):
        p.error("dump.erofs is missing")
    parent = Path("work/asahi")
    candidates = [Path(args.previous)] if args.previous else sorted(
        parent.glob("recovery-audit-*/summary.json"), key=lambda x: x.stat().st_mtime, reverse=True)
    data = None
    for candidate in candidates:
        path = candidate / "summary.json" if candidate.is_dir() else candidate
        loaded = json.loads(path.read_text())
        if loaded.get("exit_status") == 0:
            data = loaded
            break
    if data is None:
        p.error("no completed recovery-audit summary found")
    image = Path(data["image"])
    helpers = load_helpers()
    _, logical = helpers.partitions(image)
    offsets = {n: v["extents"][0]["offset"] for n, v in logical.items()
               if len(v["extents"]) == 1 and "offset" in v["extents"][0]}
    print("THIS STEP MODIFIES STORAGE")
    print("Creates one new binder-clients report directory under work/asahi. The image is only read.")
    print("Delete the newly printed result directory to reverse it.")
    parent.mkdir(parents=True, exist_ok=True)
    out = Path(tempfile.mkdtemp(prefix="binder-clients-", dir=parent)).resolve()
    print(f"Results: {out}")
    found, skipped = [], []

    def dump(part, path, mode):
        return subprocess.run(["dump.erofs", f"--offset={offsets[part]}", f"--path={path}", mode, str(image)],
                              capture_output=True, timeout=120)

    for part, directory in (("system_a", "/system/bin"), ("vendor_a", "/bin")):
        if part not in offsets:
            skipped.append(f"{part}: not a single linear extent or absent")
            continue
        listing = dump(part, directory, "--ls")
        if listing.returncode:
            skipped.append(f"{part}:{directory}: listing failed")
            continue
        for name in listing_names(listing.stdout.decode(errors="replace"))[:LIMIT_ENTRIES]:
            content = dump(part, f"{directory}/{name}", "--cat").stdout
            if len(content) > LIMIT_BYTES:
                skipped.append(f"{part}:{directory}/{name}: over size limit")
                continue
            try:
                info = helpers.elf_info(content)
            except (ValueError, KeyError, UnicodeError, struct.error):
                continue  # scripts, symlinks and non-ELF files
            if any(lib in info["needed"] for lib in BINDER_LIBS):
                found.append({"path": f"{part}:{directory}/{name}", "needed": info["needed"]})
                print(f"BINDER_CLIENT {part}:{directory}/{name}: " + ", ".join(info["needed"]))
    summary = {"image": str(image), "binder_clients": found, "skipped": skipped,
               "limitations": ["Static DT_NEEDED only; nothing was executed.",
                               "Only /system/bin and the vendor /bin listing were scanned, "
                               f"up to {LIMIT_ENTRIES} entries each.",
                               "Linking libbinder does not show a program reads an FD from a reply."]}
    (out / "summary.json").write_text(json.dumps(summary, indent=2))
    print(f"{len(found)} Binder-linked executables; skipped: {len(skipped)}")
    for item in skipped:
        print("SKIPPED " + item)
    print(f"Send {out}/summary.json")


if __name__ == "__main__":
    main()
