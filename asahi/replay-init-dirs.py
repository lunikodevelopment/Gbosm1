#!/usr/bin/env python3
"""Recreate the directories and symlinks the image's init .rc files create, inside the test's tmpfs mounts.

Original project script. On a device init runs every `mkdir PATH [MODE [OWNER [GROUP]]]`
and `symlink TARGET LINK` in the `on ...` sections of its .rc files before zygote starts.
The smoke tests have no init, so programs failed one missing directory at a time
(/data/dalvik-cache, /mnt/user/0). This replays all of them at once, but only below the
test's private tmpfs mounts (/dev, /data, /mnt, /linkerconfig, /tmp); paths elsewhere
are listed, with whether they exist in the read-only tree. Owner and group names are
resolved by the image's own Bionic (`toybox id` run in the tree), so no id table is
copied here. Lines with property expansions (`${...}`) are skipped and counted.

Usage (as root inside the test's namespace): replay-init-dirs.py TREE_ROOT
"""
import os
from pathlib import Path
import subprocess
import sys

WRITABLE = ("/dev/", "/data/", "/mnt/", "/linkerconfig/", "/tmp/")


def rc_files(root):
    first = [root / "system/etc/init/hw/init.rc", root / "init.rc"]
    found = [p for p in first if p.is_file()][:1]
    for d in ("system/etc/init", "system_ext/etc/init", "product/etc/init", "vendor/etc/init", "odm/etc/init"):
        found += sorted((root / d).glob("*.rc"))
    found += sorted(root.glob("apex/*/etc/*.rc"))
    return found


def commands(path):
    in_action = False
    for raw in path.read_text(errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        words = line.split()
        if words[0] in ("on", "service", "import"):
            in_action = words[0] == "on"
            continue
        if in_action and words[0] in ("mkdir", "symlink"):
            yield words


resolved = {}


def ident(root, name, flag):
    if name.isdigit():
        return int(name)
    if (name, flag) not in resolved:
        done = subprocess.run(["chroot", str(root), "/system/bin/toybox", "id", flag, name],
                              capture_output=True, text=True, timeout=30)
        resolved[name, flag] = int(done.stdout) if done.returncode == 0 and done.stdout.strip().isdigit() else None
    return resolved[name, flag]


def main():
    root = Path(sys.argv[1])
    made = links = 0
    expanded, outside, failures, unknown = [], {}, [], set()
    files = rc_files(root)
    for rc in files:
        for words in commands(rc):
            target = words[1] if words[0] == "mkdir" else words[2] if len(words) > 2 else ""
            if "$" in " ".join(words):
                expanded.append(" ".join(words))
                continue
            if not target.startswith(WRITABLE):
                outside.setdefault(target, (root / target.lstrip("/")).exists() or (root / target.lstrip("/")).is_symlink())
                continue
            host = root / target.lstrip("/")
            try:
                if words[0] == "symlink":
                    host.parent.mkdir(parents=True, exist_ok=True)
                    if not host.is_symlink() and not host.exists():
                        os.symlink(words[1], host)
                        links += 1
                    continue
                host.mkdir(parents=True, exist_ok=True)
                made += 1
                if len(words) > 2 and words[2].isdigit():
                    os.chmod(host, int(words[2], 8))
                if len(words) > 3:
                    uid = ident(root, words[3], "-u")
                    gid = ident(root, words[4], "-g") if len(words) > 4 else uid
                    for name, value in ((words[3], uid), (words[4] if len(words) > 4 else words[3], gid)):
                        if value is None:
                            unknown.add(name)
                    os.chown(host, -1 if uid is None else uid, -1 if gid is None else gid)
            except OSError as error:
                failures.append(f"{' '.join(words)}: {error}")
    print(f"init dirs: {len(files)} .rc files read; {made} mkdir and {links} symlink replayed under /dev /data /mnt /linkerconfig /tmp")
    print(f"  skipped with property expansions: {len(expanded)}; owner/group names Bionic did not know: {sorted(unknown) or 'none'}")
    for line in failures[:15]:
        print(f"  FAILED {line}")
    missing = sorted(p for p, exists in outside.items() if not exists)
    print(f"  {len(outside)} paths outside the tmpfs mounts (left alone); missing in the read-only tree ({len(missing)}): "
          + (" ".join(missing[:40]) or "none"))


if __name__ == "__main__":
    main()
