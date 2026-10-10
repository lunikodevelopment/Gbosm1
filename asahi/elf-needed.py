#!/usr/bin/env python3
"""List the unresolved DT_NEEDED libraries of ELF files inside the extracted tree.

Original project script. Usage: elf-needed.py TREE_ROOT LD_LIBRARY_PATH_DIRS FILE_IN_TREE...
Follows DT_NEEDED breadth-first through TREE_ROOT, looking in the given colon-separated
directories (inside the tree), each file's own directory, /system/lib64 and /vendor/lib64.
Prints each library that cannot be found, with one file that needs it. Only reads files.
"""
import importlib.util
from pathlib import Path
import sys


def main():
    root = Path(sys.argv[1])
    dirs = [d for d in sys.argv[2].split(":") if d] + ["/system/lib64", "/vendor/lib64"]
    spec = importlib.util.spec_from_file_location("recovery_audit", Path(__file__).with_name("audit-recovery.py"))
    recovery = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(recovery)
    queue = list(sys.argv[3:])
    seen, missing, resolved = set(), {}, 0
    while queue:
        current = queue.pop(0)
        if current in seen:
            continue
        seen.add(current)
        path = root / current.lstrip("/")
        try:
            needed = recovery.elf_info(path.read_bytes())["needed"]
        except (OSError, ValueError) as error:
            print(f"cannot read {current}: {error}")
            continue
        for name in needed:
            found = None
            for directory in [str(Path(current).parent)] + dirs:
                candidate = root / directory.lstrip("/") / name
                if candidate.exists():
                    found = f"{directory.rstrip('/')}/{name}"
                    break
            if found:
                resolved += 1
                queue.append(found)
            else:
                missing.setdefault(name, current)
    print(f"closure: {len(seen)} files read, {resolved} needed entries resolved, {len(missing)} libraries unresolved")
    for name, user in sorted(missing.items()):
        print(f"  UNRESOLVED {name}   (needed by {user})")


if __name__ == "__main__":
    main()
