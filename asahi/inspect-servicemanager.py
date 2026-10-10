#!/usr/bin/env python3
"""Read-only static look at the image's servicemanager; executes nothing.

Copies /system/bin/servicemanager (size-limited) into a new report directory,
prints its DT_NEEDED libraries and selected strings. Strings are hints only.
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
HINTS = re.compile(rb"(servicemanager|ctl\.|selinux|ready|dump|vintf|perfetto|lazy|binder|/dev/|/system/|/vendor/|\.xml|\.rc)", re.I)


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
    spec = importlib.util.spec_from_file_location("recovery_audit", Path(__file__).with_name("audit-recovery.py"))
    helpers = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helpers)
    _, logical = helpers.partitions(image)
    extents = logical["system_a"]["extents"]
    if len(extents) != 1 or "offset" not in extents[0]:
        p.error("system_a is not one linear extent")
    print("THIS STEP MODIFIES STORAGE")
    print("Creates one new servicemanager-inspect report directory under work/asahi. The image is only read.")
    print("Delete the newly printed result directory to reverse it.")
    parent.mkdir(parents=True, exist_ok=True)
    out = Path(tempfile.mkdtemp(prefix="servicemanager-inspect-", dir=parent)).resolve()
    print(f"Results: {out}")
    r = subprocess.run(["dump.erofs", f"--offset={extents[0]['offset']}", "--path=/system/bin/servicemanager",
                        "--cat", str(image)], capture_output=True, timeout=120)
    if r.returncode or len(r.stdout) > LIMIT:
        raise SystemExit("could not read servicemanager within the size limit")
    binary = out / "servicemanager"
    binary.write_bytes(r.stdout)
    info = helpers.elf_info(r.stdout)
    print("machine=%s aligned_16k=%s size=%d" % (info["machine"], info["aligned_16k"], len(r.stdout)))
    print("DT_NEEDED: " + ", ".join(info["needed"]))
    hints = sorted({m.group(0).decode(errors="replace") for m in re.finditer(rb"[ -~]{6,160}", r.stdout)
                    if HINTS.search(m.group(0))})
    (out / "strings-hints.txt").write_text("\n".join(hints))
    for line in hints[:150]:
        print("STR " + line)
    (out / "summary.json").write_text(json.dumps(
        {"image": str(image), "elf": info, "string_hints": len(hints),
         "limitations": ["Static only; nothing executed.", "String hints do not show behavior."]}, indent=2))
    print(f"{len(hints)} string hints (first 150 shown). Send {out}/summary.json")


if __name__ == "__main__":
    main()
