#!/usr/bin/env python3
"""Recreate the directories and symlinks the image's init .rc files create, inside the test's tmpfs mounts.

Original project script. On a device init runs every `mkdir PATH [MODE [OWNER [GROUP]]]`
and `symlink TARGET LINK` in the `on ...` sections of its .rc files before zygote starts.
The smoke tests have no init, so programs failed one missing directory at a time
(/data/dalvik-cache, /mnt/user/0). This replays all of them at once, but only below the
test's private tmpfs mounts (/dev, /data, /mnt, /linkerconfig, /tmp, /sys); paths elsewhere
are listed, with whether they exist in the read-only tree. Owner and group names are
resolved by the image's own Bionic (`toybox id` run in the tree), so no id table is
copied here. Lines with property expansions (`${...}`) are skipped and counted.
It also creates the cgroup directories named in the image's cgroups.json and
task_profiles.json files, plus <cgroup2 root>/system and /apps (init
mounts them on a device; libprocessgroup needs them to place system_server). Under the
test's /sys tmpfs these are plain directories: nothing is grouped or limited.

Usage (as root inside the test's namespace): replay-init-dirs.py TREE_ROOT [--perms-under PREFIX]
With --perms-under it only applies the init chmod/chown lines for existing paths under PREFIX
(for mounts made later in the run, such as binderfs).
"""
import json
import os
from pathlib import Path
import subprocess
import sys

WRITABLE = ("/dev/", "/data/", "/mnt/", "/linkerconfig/", "/tmp/", "/sys/")


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
        if in_action and words[0] in ("mkdir", "symlink", "chmod", "chown"):
            yield words


def replay_permissions(root, prefix):
    """Apply the image's init `chmod MODE PATH` and `chown OWNER [GROUP] PATH` lines for paths under prefix."""
    import glob
    applied = 0
    for rc in rc_files(root):
        for words in commands(rc):
            if words[0] not in ("chmod", "chown") or len(words) < 3 or not words[-1].startswith(prefix):
                continue
            for host in glob.glob(str(root / words[-1].lstrip("/"))):
                try:
                    if words[0] == "chmod":
                        os.chmod(host, int(words[1], 8))
                    else:
                        uid = ident(root, words[1], "-u")
                        gid = ident(root, words[2], "-g") if len(words) > 3 else -1
                        os.chown(host, -1 if uid is None else uid, -1 if gid is None else gid)
                    applied += 1
                    print(f"  {rc.relative_to(root)}: {' '.join(words)}")
                except (OSError, ValueError) as error:
                    print(f"  FAILED {' '.join(words)}: {error}")
    print(f"init chmod/chown lines applied under {prefix}: {applied}")


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
    if len(sys.argv) == 4 and sys.argv[2] == "--perms-under":
        replay_permissions(root, sys.argv[3])
        return
    made = links = 0
    expanded, outside, failures, unknown = [], {}, [], set()
    files = rc_files(root)
    for rc in files:
        for words in commands(rc):
            if words[0] in ("chmod", "chown"):
                continue
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
    groups, controllers = [], {}
    for relative in ("system/etc/cgroups.json", "vendor/etc/cgroups.json"):
        path = root / relative
        if not path.is_file():
            continue
        data = json.loads(path.read_text())
        for entry in data.get("Cgroups", []):
            controllers[entry.get("Controller", "")] = entry.get("Path", "")
        v2 = data.get("Cgroups2", {})
        if v2.get("Path"):
            groups.append(v2["Path"])
            # libprocessgroup puts system uids under <root>/system and apps under <root>/apps
            groups += [v2["Path"] + "/system", v2["Path"] + "/apps"]
            for c in v2.get("Controllers", []):
                controllers[c.get("Controller", "")] = v2["Path"] + ("" if c.get("Path", ".") == "." else "/" + c["Path"])
    groups += [p for p in controllers.values() if p]
    profiles = [root / "system/etc/task_profiles.json", root / "vendor/etc/task_profiles.json"]
    profiles += sorted((root / "system/etc/task_profiles").glob("*.json")) + sorted((root / "system/etc").glob("task_profiles_*.json"))
    joins = 0
    for path in profiles:
        if not path.is_file():
            continue
        for profile in json.loads(path.read_text()).get("Profiles", []):
            for action in profile.get("Actions", []):
                params = action.get("Params", {})
                base = controllers.get(params.get("Controller", ""))
                if action.get("Name") in ("JoinCgroup", "SetCgroup") and base and params.get("Path", "") not in ("", "."):
                    groups.append(base + "/" + params["Path"])
                    joins += 1
    groups = sorted(set(g for g in groups if g.startswith(WRITABLE)))
    for g in groups:
        (root / g.lstrip("/")).mkdir(parents=True, exist_ok=True)
    print(f"cgroup directories from cgroups.json and {joins} task-profile JoinCgroup actions (stand-ins, no controller behind them): "
          f"{len(groups)}: {' '.join(groups[:30]) or 'none'}")
    print(f"init dirs: {len(files)} .rc files read; {made} mkdir and {links} symlink replayed under /dev /data /mnt /linkerconfig /tmp /sys")
    print(f"  skipped with property expansions: {len(expanded)}; owner/group names Bionic did not know: {sorted(unknown) or 'none'}")
    for line in failures[:15]:
        print(f"  FAILED {line}")
    missing = sorted(p for p, exists in outside.items() if not exists)
    tops = {}
    for p in missing:
        tops["/" + p.strip("/").split("/")[0]] = tops.get("/" + p.strip("/").split("/")[0], 0) + 1
    print(f"  {len(outside)} paths outside the tmpfs mounts (left alone); missing in the read-only tree, by top folder: "
          + (" ".join(f"{t}({n})" for t, n in sorted(tops.items())) or "none"))
    shown = [p for p in missing if not p.startswith(("/config/", "/cache/"))]
    print("  missing, not under /config or /cache: " + (" ".join(shown[:25]) or "none"))


if __name__ == "__main__":
    main()
