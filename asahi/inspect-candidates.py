#!/usr/bin/env python3
"""Read-only static look at several image executables (default: stage 6 candidates); executes nothing.

Original project script. For each part:path argument it extracts the file
(size-limited) and prints its size, DT_NEEDED libraries and categorized strings:
AIDL-style interface descriptors, service names, file paths, property names, and
file-descriptor-related strings. Strings are hints only, not proof of behavior.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

LIMIT = 64 << 20
DEFAULTS = ["system_a:/system/bin/gsid", "vendor_a:/bin/disk_info_service", "system_a:/system/bin/sensorservice",
            "system_a:/system/bin/mediametrics", "system_a:/system/bin/drmserver"]
CATEGORIES = {
    "descriptors": re.compile(rb"^(?:[a-z][a-z0-9_]*\.)+I[A-Z][A-Za-z0-9_]+$"),
    "paths": re.compile(rb"^/(?:dev|metadata|data|sys|proc|system|vendor|apex|mnt)/[A-Za-z0-9_./%@+-]*$"),
    "properties": re.compile(rb"^(?:ro|persist|init|sys|gsid|ctl|debug|vendor)\.[A-Za-z0-9_.]+$"),
    "fd_related": re.compile(rb"(?i)(file.?descriptor|\bfd\b|memfd|ashmem|dup\b|ParcelFileDescriptor)"),
    "service_names": re.compile(rb"^[a-z][A-Za-z0-9_.-]{2,40}(?:/default)?$"),
    "registration": re.compile(rb"(addService|registerLazyService|joinThreadPool|startThreadPool|instantiate|waitForService|isDeclared)"),
}
LIMIT_PER = 40


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("targets", nargs="*", help="part:path pairs, e.g. system_a:/system/bin/gsid")
    p.add_argument("--previous", help="completed recovery-audit directory (default: latest)")
    args = p.parse_args()
    if os.geteuid() == 0:
        p.error("run as your ordinary user, without sudo")
    if not shutil.which("dump.erofs"):
        p.error("dump.erofs is missing")
    targets = args.targets or DEFAULTS
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
    spec = importlib.util.spec_from_file_location("recovery_audit", Path(__file__).with_name("audit-recovery.py"))
    helpers = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helpers)
    _, logical = helpers.partitions(image)
    print("THIS STEP MODIFIES STORAGE")
    print("Creates one new candidate-inspect report directory under work/asahi. The image is only read.")
    print("Delete the newly printed result directory to reverse it.")
    parent.mkdir(parents=True, exist_ok=True)
    out = Path(tempfile.mkdtemp(prefix="candidate-inspect-", dir=parent)).resolve()
    print(f"Results: {out}")
    report = []
    for target in targets:
        part, _, path = target.partition(":")
        if part not in logical or not re.fullmatch(r"/[A-Za-z0-9_./@+-]+", path):
            print(f"SKIPPED {target}: unknown partition or unsupported path")
            continue
        extents = logical[part]["extents"]
        if len(extents) != 1 or "offset" not in extents[0]:
            print(f"SKIPPED {target}: partition is not one linear extent")
            continue
        run = subprocess.run(["dump.erofs", f"--offset={extents[0]['offset']}", f"--path={path}", "--cat", str(image)],
                             capture_output=True, timeout=120)
        content = run.stdout
        if run.returncode or not content or len(content) > LIMIT:
            print(f"SKIPPED {target}: extraction failed or over size limit")
            continue
        entry = {"target": target, "bytes": len(content)}
        try:
            entry["needed"] = helpers.elf_info(content)["needed"]
        except ValueError as error:
            print(f"SKIPPED {target}: {error}")
            continue
        strings = sorted({m.group(0) for m in re.finditer(rb"[\x20-\x7e]{4,200}", content)})
        print(f"\n=== {target} ({len(content)} bytes)")
        print("DT_NEEDED: " + ", ".join(entry["needed"]))
        for name, pattern in CATEGORIES.items():
            hits = [s.decode() for s in strings if pattern.search(s)]
            entry[name] = hits[:LIMIT_PER]
            if name == "service_names":
                entry[name] = []  # too noisy to print; descriptors and registration lines carry the signal
                continue
            print(f"{name} ({len(hits)}):")
            for s in hits[:LIMIT_PER]:
                print("  " + s)
        report.append(entry)
    (out / "summary.json").write_text(json.dumps({"image": str(image), "candidates": report,
        "limitations": ["Static strings and DT_NEEDED only; nothing was executed.",
                        "A string hit does not show behavior, FD handling or that the program can start without Android init."]}, indent=2))
    print(f"\nSend {out}/summary.json and the terminal output above")


if __name__ == "__main__":
    main()
