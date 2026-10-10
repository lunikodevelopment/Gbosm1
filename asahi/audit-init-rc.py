#!/usr/bin/env python3
"""Read-only scan of the image's init .rc files; executes nothing.

Original project script. Reads /etc/init/*.rc from system_a and vendor_a (and
system_ext/product if present as logical partitions), then lists each
`service` with its executable, user, class and options, and counts lines that
would matter for a private-root start (exec_start, mount, load_system_props,
write to /sys/fs/selinux, reboot). The listing is a plan input, not proof that
any service can start outside Android init.
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

LIMIT = 4 << 20
PARTS = {"system_a": "/system/etc/init", "vendor_a": "/etc/init", "system_ext_a": "/etc/init", "product_a": "/etc/init"}
FLAGS = ("disabled", "oneshot", "lazy", "critical", "restart_period")
RISKY = re.compile(r"^\s*(exec_start|exec |exec_background|mount |mount_all|load_system_props|load_persist_props|"
                   r"write\s+/sys/fs/selinux|reboot|setprop sys.powerctl|insmod|swapon|umount|symlink|mkdir)")


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
    spec = importlib.util.spec_from_file_location("graphics_audit", Path(__file__).with_name("audit-graphics.py"))
    graphics = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(graphics)
    _, logical = recovery.partitions(image)
    print("THIS STEP MODIFIES STORAGE")
    print("Creates one new init-rc report directory under work/asahi. The image is only read; nothing is executed.")
    print("Delete the newly printed result directory to reverse it.")
    parent.mkdir(parents=True, exist_ok=True)
    out = Path(tempfile.mkdtemp(prefix="init-rc-", dir=parent)).resolve()
    print(f"Results: {out}")
    services, risky, files = [], [], []
    for part, directory in PARTS.items():
        if part not in logical:
            print(f"SKIPPED {part}: not a logical partition in this image")
            continue
        extents = logical[part]["extents"]
        if len(extents) != 1 or "offset" not in extents[0]:
            print(f"SKIPPED {part}: not one linear extent")
            continue
        base = ["dump.erofs", f"--offset={extents[0]['offset']}"]
        ls = subprocess.run(base + [f"--path={directory}", "--ls", str(image)], capture_output=True, timeout=120)
        if ls.returncode:
            print(f"SKIPPED {part}:{directory}: not listable")
            continue
        for kind, name in graphics.listing_names(ls.stdout.decode(errors="replace")):
            if kind != 1 or not name.endswith(".rc"):
                continue
            cat = subprocess.run(base + [f"--path={directory}/{name}", "--cat", str(image)], capture_output=True, timeout=120)
            if cat.returncode or len(cat.stdout) > LIMIT:
                print(f"SKIPPED {part}:{directory}/{name}: extraction failed or over limit")
                continue
            source = f"{part}:{directory}/{name}"
            files.append(source)
            current = None
            for number, raw in enumerate(cat.stdout.decode(errors="replace").splitlines(), 1):
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                words = line.split()
                if words[0] == "service" and len(words) >= 3:
                    current = {"name": words[1], "exec": words[2], "args": words[3:], "source": source, "line": number,
                               "user": "", "class": "", "flags": [], "sockets": [], "caps": [], "interfaces": []}
                    services.append(current)
                    continue
                if words[0] in ("on", "import"):
                    current = None
                if RISKY.match(raw) and current is None:
                    risky.append({"source": source, "line": number, "text": line[:160]})
                if current is None:
                    continue
                if words[0] == "user" and len(words) > 1:
                    current["user"] = words[1]
                elif words[0] == "class" and len(words) > 1:
                    current["class"] = " ".join(words[1:])
                elif words[0] in FLAGS:
                    current["flags"].append(line[:60])
                elif words[0] == "socket" and len(words) > 1:
                    current["sockets"].append(words[1])
                elif words[0] == "capabilities":
                    current["caps"] = words[1:]
                elif words[0] == "interface" and len(words) > 2:
                    current["interfaces"].append(words[2])
    print(f"\nFiles read: {len(files)}; services: {len(services)}; risky non-service lines: {len(risky)}")
    print("\nSERVICES (name  exec  user  class  flags  sockets  interfaces):")
    for s in sorted(services, key=lambda x: x["name"]):
        print(f"  {s['name']:28} {s['exec']:42} {s['user'] or '-':10} {s['class'] or '-':12} "
              f"{','.join(f.split()[0] for f in s['flags']) or '-':16} {','.join(s['sockets']) or '-'} "
              f"{','.join(s['interfaces']) or '-'}")
    counts = {}
    for item in risky:
        key = item["text"].split()[0]
        counts[key] = counts.get(key, 0) + 1
    print("\nRISKY LINE KINDS in action blocks: " + (", ".join(f"{k}={v}" for k, v in sorted(counts.items())) or "none"))
    selinux = [r for r in risky if "selinux" in r["text"]]
    print(f"Lines touching selinux in action blocks: {len(selinux)}")
    for r in selinux[:10]:
        print(f"  {r['source']}:{r['line']}: {r['text']}")
    (out / "summary.json").write_text(json.dumps({
        "image": str(image), "files": files, "services": services, "risky_lines": risky,
        "limitations": ["Static text of .rc files only; nothing was executed.",
                        "A service entry does not show it can start outside Android init (properties, sockets and mounts may be required)."]},
        indent=2))
    print(f"\nSend {out}/summary.json and the terminal output above")


if __name__ == "__main__":
    main()
