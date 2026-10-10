#!/usr/bin/env python3
"""Inspect the image's ART-related .apex files; executes nothing.

Original project script. Copies com.android.runtime, com.google.android.art,
i18n, tzdata6 and conscrypt apex files out of system_a:/system/apex into a new
work/asahi/apex-scan-* directory (read-only access to the image), then reports
each zip's members and the payload filesystem type (ext4 or erofs) from its
header. Nothing is mounted, unpacked or run.
"""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import zipfile

WANT = ("com.android.runtime", "com.google.android.art", "com.android.i18n",
        "com.google.android.tzdata6", "com.google.android.conscrypt")
LIMIT = 400 << 20


def payload_type(zf, info):
    with zf.open(info) as f:
        head = f.read(2048)
    if len(head) >= 1082 and struct.unpack("<H", head[1080:1082])[0] == 0xEF53:
        return "ext4"
    if len(head) >= 1028 and struct.unpack("<I", head[1024:1028])[0] == 0xE0F5E1E2:
        return "erofs"
    return "unknown"


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
    spec = importlib.util.spec_from_file_location("recovery_audit", Path(__file__).with_name("audit-recovery.py"))
    recovery = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(recovery)
    _, logical = recovery.partitions(image)
    print("THIS STEP MODIFIES STORAGE")
    print("Creates one new apex-scan directory under work/asahi holding copies of five apex files")
    print("(a few hundred MB at most). The image is only read; nothing is mounted or run.")
    print("Delete the newly printed result directory to reverse it.")
    parent.mkdir(parents=True, exist_ok=True)
    out = Path(tempfile.mkdtemp(prefix="apex-scan-", dir=parent)).resolve()
    print(f"Results: {out}")
    extents = logical["system_a"]["extents"]
    base = ["dump.erofs", f"--offset={extents[0]['offset']}"]
    report = []
    for name in WANT:
        entry = {"apex": name}
        report.append(entry)
        dest = out / f"{name}.apex"
        cat = subprocess.run(base + [f"--path=/system/apex/{name}.apex", "--cat", str(image)],
                             capture_output=True, timeout=300)
        if cat.returncode or not cat.stdout or len(cat.stdout) > LIMIT:
            entry["error"] = f"extraction failed or empty/over limit (rc={cat.returncode}, bytes={len(cat.stdout)})"
            print(f"{name}: {entry['error']}")
            continue
        dest.write_bytes(cat.stdout)
        entry["bytes"] = len(cat.stdout)
        try:
            with zipfile.ZipFile(dest) as zf:
                entry["members"] = [(i.filename, i.file_size, i.compress_type) for i in zf.infolist()]
                for i in zf.infolist():
                    if i.filename == "apex_payload.img":
                        entry["payload"] = payload_type(zf, i)
                        entry["payload_stored"] = i.compress_type == 0
        except zipfile.BadZipFile:
            entry["error"] = "not a zip (may be a .capex or other format)"
        print(f"{name}: {entry.get('bytes')} bytes, payload={entry.get('payload', '-')}, "
              f"stored={entry.get('payload_stored', '-')}, members={[m[0] for m in entry.get('members', [])]}"
              f"{' ERROR ' + entry['error'] if 'error' in entry else ''}")
    (out / "summary.json").write_text(json.dumps({"image": str(image), "apexes": report}, indent=2))
    print(f"\nSend the terminal output above (summary: {out}/summary.json)")


if __name__ == "__main__":
    main()
