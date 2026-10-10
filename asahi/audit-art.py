#!/usr/bin/env python3
"""Read-only scan of the image's Java-side runtime files; executes nothing.

Original project script, same method as audit-init-rc.py. Lists, from the
logical partitions of the recovery image: /system/apex, /system/framework
(boot images and jars), app_process/dalvikvm binaries, zygote init scripts
(including init/hw), and the BOOTCLASSPATH-related lines of any .rc file
found. The result is a plan input for an ART/zygote stage, not proof that
anything starts.
"""
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

DIRS = [("system_a", "/system/apex"), ("system_a", "/system/framework"), ("system_a", "/system/bin"),
        ("system_a", "/system/etc/init"), ("system_a", "/system/etc/init/hw"),
        ("system_ext_a", "/apex"), ("system_ext_a", "/framework"), ("system_ext_a", "/etc/init"),
        ("vendor_a", "/etc/init"), ("product_a", "/framework")]
BINARIES = re.compile(r"^(app_process\d*|dalvikvm\d*|dex2oat\w*|dexopt\w*|zygote\w*|art\w*|odrefresh|"
                      r"boot\w*|installd|linker\w*)$")
CLASSPATH = re.compile(r"(BOOTCLASSPATH|SYSTEMSERVERCLASSPATH|DEX2OATBOOTCLASSPATH|zygote|app_process)", re.I)
LIMIT = 4 << 20


def main():
    if os.geteuid() == 0:
        sys.exit("run as your ordinary user, without sudo")
    if not shutil.which("dump.erofs"):
        sys.exit("dump.erofs is missing")
    parent = Path("work/asahi")
    data = None
    for path in sorted(parent.glob("recovery-audit-*/summary.json"), key=lambda x: x.stat().st_mtime, reverse=True):
        loaded = json.loads(path.read_text())
        if loaded.get("exit_status") == 0:
            data = loaded
            break
    if data is None:
        sys.exit("no completed recovery-audit summary found")
    image = Path(data["image"])
    here = Path(__file__)
    spec = importlib.util.spec_from_file_location("recovery_audit", here.with_name("audit-recovery.py"))
    recovery = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(recovery)
    spec = importlib.util.spec_from_file_location("graphics_audit", here.with_name("audit-graphics.py"))
    graphics = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(graphics)
    _, logical = recovery.partitions(image)
    print("THIS STEP MODIFIES STORAGE")
    print("Creates one new art-scan report directory under work/asahi. The image is only read; nothing is executed.")
    print("Delete the newly printed result directory to reverse it.")
    parent.mkdir(parents=True, exist_ok=True)
    out = Path(tempfile.mkdtemp(prefix="art-scan-", dir=parent)).resolve()
    print(f"Results: {out}")
    listings, rc_hits, notes = {}, [], []
    for part, directory in DIRS:
        key = f"{part}:{directory}"
        if part not in logical:
            notes.append(f"SKIPPED {key}: partition not in this image")
            continue
        extents = logical[part]["extents"]
        if len(extents) != 1 or "offset" not in extents[0]:
            notes.append(f"SKIPPED {key}: not one linear extent")
            continue
        base = ["dump.erofs", f"--offset={extents[0]['offset']}"]
        ls = subprocess.run(base + [f"--path={directory}", "--ls", str(image)], capture_output=True, timeout=120)
        if ls.returncode:
            notes.append(f"SKIPPED {key}: not listable")
            continue
        entries = [(k, n) for k, n in graphics.listing_names(ls.stdout.decode(errors="replace"))]
        listings[key] = [{"kind": k, "name": n} for k, n in entries]
        for kind, name in entries:
            if kind == 1 and name.endswith(".rc") and "init" in directory:
                cat = subprocess.run(base + [f"--path={directory}/{name}", "--cat", str(image)],
                                     capture_output=True, timeout=120)
                if cat.returncode or len(cat.stdout) > LIMIT:
                    continue
                for number, raw in enumerate(cat.stdout.decode(errors="replace").splitlines(), 1):
                    if raw.strip() and not raw.strip().startswith("#") and CLASSPATH.search(raw):
                        rc_hits.append({"source": f"{key}/{name}", "line": number, "text": raw.strip()[:200]})
    for note in notes:
        print(note)
    print("\nDIRECTORY LISTINGS (entry counts):")
    for key, items in listings.items():
        print(f"  {key}: {len(items)} entries")
    apex = [i["name"] for k, v in listings.items() if k.endswith("/apex") for i in v]
    print(f"\nAPEX entries ({len(apex)}):")
    for name in sorted(apex):
        print(f"  {name}")
    jars = [i["name"] for k, v in listings.items() if "framework" in k for i in v]
    print(f"\nframework entries: {len(jars)} (boot images: "
          f"{sum(1 for j in jars if j.endswith(('.art', '.oat', '.vdex')))}, jars: {sum(1 for j in jars if j.endswith('.jar'))})")
    print("\nRUNTIME-RELATED BINARIES in /system/bin:")
    for k, v in listings.items():
        if k.endswith("/system/bin"):
            for i in v:
                if BINARIES.match(i["name"]):
                    print(f"  {i['name']}")
    print(f"\nZYGOTE / CLASSPATH LINES in .rc files ({len(rc_hits)}):")
    for hit in rc_hits[:60]:
        print(f"  {hit['source']}:{hit['line']}: {hit['text']}")
    (out / "summary.json").write_text(json.dumps({
        "image": str(image), "listings": listings, "rc_lines": rc_hits, "notes": notes,
        "limitations": ["Directory names and .rc text only; nothing was executed or unpacked.",
                        "Listed apex files are not shown to be valid, mountable or complete."]}, indent=2))
    print(f"\nSend {out}/summary.json and the terminal output above")


if __name__ == "__main__":
    main()
