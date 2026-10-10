#!/usr/bin/env python3
"""Read-only listing of the image's GPU driver files and graphics properties; executes nothing.

Lists EGL, Vulkan, ANGLE and HAL library names in vendor_a and system_a and the
graphics-related build properties. Names and properties are hints, not behavior.
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

LIMIT = 4 << 20
DIRS = {"vendor_a": ["/lib64", "/lib64/egl", "/lib64/hw"],
        "system_a": ["/system/lib64"]}
NAME = re.compile(r"(egl|gles|vulkan|angle|gralloc|mapper|allocator|composer|hwc|mesa|adreno|drm)", re.I)
PROPS = {"vendor_a": ["/build.prop"], "system_a": ["/system/build.prop"]}
PROP = re.compile(r"^(ro\.hardware\.(egl|vulkan)|ro\.gfx\.[^=]*|ro\.hwui\.[^=]*|[^=]*angle[^=]*|"
                  r"debug\.(renderengine|hwui)\.[^=]*|ro\.surface_flinger\.[^=]*|ro\.opengles\.[^=]*|"
                  r"ro\.vendor\.hwc[^=]*|ro\.board\.platform)=", re.I)


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
    offsets = {}
    for name in DIRS:
        extents = logical[name]["extents"]
        if len(extents) != 1 or "offset" not in extents[0]:
            p.error(f"{name} is not one linear extent")
        offsets[name] = extents[0]["offset"]
    print("THIS STEP MODIFIES STORAGE")
    print("Creates one new graphics-image report directory under work/asahi. The image is only read.")
    print("Delete the newly printed result directory to reverse it.")
    parent.mkdir(parents=True, exist_ok=True)
    out = Path(tempfile.mkdtemp(prefix="graphics-image-", dir=parent)).resolve()
    print(f"Results: {out}")

    def dump(part, path, mode):
        return subprocess.run(["dump.erofs", f"--offset={offsets[part]}", f"--path={path}",
                               mode, str(image)], capture_output=True, timeout=120)

    result = {"image": str(image), "files": {}, "properties": {}, "missing": [],
              "limitations": ["Static only; nothing executed.", "Names and properties do not show behavior."]}
    for part, dirs in DIRS.items():
        for d in dirs:
            r = dump(part, d, "--ls")
            if r.returncode:
                result["missing"].append(f"{part}:{d}")
                print(f"MISSING {part}:{d}")
                continue
            names = []
            for line in r.stdout.decode(errors="replace").splitlines():
                m = re.match(r"\s*\d+\s+\d+\s+(.+?)\s*$", line)
                if m and m[1] not in (".", "..") and NAME.search(m[1]):
                    names.append(m[1])
            result["files"][f"{part}:{d}"] = sorted(names)
            for n in sorted(names):
                print(f"FILE {part}:{d}/{n}")
    for part, files in PROPS.items():
        for f in files:
            r = dump(part, f, "--cat")
            if r.returncode or len(r.stdout) > LIMIT:
                result["missing"].append(f"{part}:{f}")
                print(f"MISSING {part}:{f}")
                continue
            lines = [l for l in r.stdout.decode(errors="replace").splitlines() if PROP.match(l)]
            result["properties"][f"{part}:{f}"] = lines
            for l in lines:
                print(f"PROP {part}:{f} {l}")
    (out / "summary.json").write_text(json.dumps(result, indent=2) + "\n")
    print(f"Send {out}/summary.json")


if __name__ == "__main__":
    main()
