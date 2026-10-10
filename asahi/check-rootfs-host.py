#!/usr/bin/env python3
"""Read-only host check before extracting the image's partitions into a root tree.

Original project script. Prints, and writes nothing:
- the image's logical partition sizes (from the already-validated LP metadata);
- free space where the tree would go;
- whether the host can map more than one user id into an unprivileged user
  namespace (/etc/subuid, /etc/subgid, newuidmap/newgidmap, unshare options),
  which Android services that run as non-root users (logd, system, graphics)
  need; the current probes map only your own user to root;
- which erofs tools exist (fsck.erofs can extract a whole tree), and whether the
  kernel lists erofs.
Nothing is mounted, extracted or executed from the image.
"""
import getpass
import importlib.util
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys

PARTS = ("system_a", "system_ext_a", "product_a", "vendor_a", "odm_a")


def run(command):
    try:
        done = subprocess.run(command, capture_output=True, text=True, timeout=30)
        return (done.stdout + done.stderr).strip()
    except Exception as error:  # report, do not fail the check
        return f"({command[0]} failed: {error})"


def main():
    if os.geteuid() == 0:
        sys.exit("run as your ordinary user, without sudo")
    print("=== BRING-UP THREAD, STAGE B1a: ROOT-TREE HOST CHECK (paste in the \"Remaining steps to boot Android\" thread) ===")
    print("This step modifies no storage; it only reads and prints.")
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
    print("\nIMAGE PARTITION SIZES (logical, bytes; an extracted tree is usually smaller because erofs compresses):")
    total = 0
    for name in PARTS:
        if name in logical:
            size = logical[name]["bytes"]
            total += size
            print(f"  {name}: {size} ({size / 2**30:.2f} GiB), {len(logical[name]['extents'])} extent(s)")
        else:
            print(f"  {name}: not in this image")
    print(f"  sum: {total / 2**30:.2f} GiB of partition space (extracted size depends on erofs compression and is not known yet)")
    free = shutil.disk_usage(parent if parent.exists() else ".").free
    print(f"\nFREE SPACE under work/asahi: {free / 2**30:.1f} GiB")

    user = getpass.getuser()
    print(f"\nUSER ID MAPPING for {user} (uid {os.getuid()}):")
    for name in ("/etc/subuid", "/etc/subgid"):
        try:
            lines = [l.strip() for l in Path(name).read_text().splitlines() if l.startswith((user + ":", str(os.getuid()) + ":"))]
        except OSError as error:
            lines = [f"unreadable: {error}"]
        print(f"  {name}: {lines or 'no entry for this user'}")
    for tool in ("newuidmap", "newgidmap"):
        found = shutil.which(tool)
        if found:
            mode = os.stat(found).st_mode
            print(f"  {tool}: {found}, setuid={bool(mode & stat.S_ISUID)}, file caps={run(['getcap', found]) if shutil.which('getcap') else 'getcap missing'}")
        else:
            print(f"  {tool}: not found")
    help_text = run(["unshare", "--help"])
    print(f"  unshare supports --map-users: {'--map-users' in help_text}; --map-groups: {'--map-groups' in help_text}; "
          f"--map-auto: {'--map-auto' in help_text}")
    print(f"  unshare version: {run(['unshare', '--version'])}")
    for name in ("/proc/sys/user/max_user_namespaces", "/proc/sys/kernel/unprivileged_userns_clone"):
        try:
            print(f"  {name}: {Path(name).read_text().strip()}")
        except OSError:
            print(f"  {name}: not present")

    print("\nEROFS TOOLS:")
    for tool in ("dump.erofs", "fsck.erofs", "erofsfuse", "mkfs.erofs"):
        print(f"  {tool}: {shutil.which(tool) or 'not found'}")
    if shutil.which("fsck.erofs"):
        help_text = run(["fsck.erofs", "--help"])
        print("  fsck.erofs version line: " + (run(["fsck.erofs", "-V"]).splitlines() or ["?"])[0])
        for option in ("--extract", "--offset", "--preserve", "--no-preserve", "--force"):
            print(f"    option {option}: {option in help_text}")
    try:
        filesystems = Path("/proc/filesystems").read_text()
        print(f"  kernel lists erofs in /proc/filesystems: {'erofs' in filesystems}")
    except OSError:
        pass
    print("\nDone. Nothing was written.")


if __name__ == "__main__":
    main()
