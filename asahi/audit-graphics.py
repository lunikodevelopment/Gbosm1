#!/usr/bin/env python3
"""Follow-up static graphics/runtime audit; creates reports and temporary files."""
import argparse
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
import zipfile


def load_helpers():
    path = Path(__file__).with_name("audit-recovery.py")
    spec = importlib.util.spec_from_file_location("recovery_audit", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def listing_names(text):
    result = []
    for line in text.splitlines():
        m = re.match(r"\s*\d+\s+(\d+)\s+([A-Za-z0-9_.@+-]+)\s*$", line)
        if m and m[2] not in (".", ".."):
            result.append((int(m[1]), m[2]))
    return result


def extract_payload(package, destination, depth=0):
    """Read only named ZIP members; never extract archive-controlled paths."""
    with zipfile.ZipFile(package) as outer:
        if "original_apex" in outer.namelist():
            if depth >= 1:
                raise ValueError("nested compressed APEX is unsupported")
            info = outer.getinfo("original_apex")
            if info.file_size > 512 * 1024 * 1024:
                raise ValueError("compressed APEX exceeds 512 MiB audit limit")
            package = io.BytesIO(outer.read(info))
            return extract_payload(package, destination, depth + 1)
        info = outer.getinfo("apex_payload.img")
        if info.file_size > 512 * 1024 * 1024:
            raise ValueError("APEX payload exceeds 512 MiB audit limit")
        with outer.open(info) as inp, destination.open("xb") as out:
            shutil.copyfileobj(inp, out, 8 << 20)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--previous", help="previous recovery-audit directory (default: latest completed report)")
    args = p.parse_args()
    if os.geteuid() == 0:
        p.error("run as your ordinary user, without sudo")
    if not shutil.which("dump.erofs"):
        p.error("dump.erofs is missing")
    parent = Path("work/asahi")
    candidates = [Path(args.previous)] if args.previous else sorted(
        parent.glob("recovery-audit-*/summary.json"), key=lambda x: x.stat().st_mtime, reverse=True)
    previous = None
    for candidate in candidates:
        path = candidate / "summary.json" if candidate.is_dir() else candidate
        data = json.loads(path.read_text())
        if data.get("exit_status") == 0:
            previous = path
            break
    if previous is None:
        p.error("no completed recovery-audit summary found")
    image = Path(data["image"])
    if not image.is_file():
        p.error(f"image missing: {image}")
    helpers = load_helpers()
    _, logical = helpers.partitions(image)
    offsets = {n: v["extents"][0]["offset"] for n, v in logical.items()
               if len(v["extents"]) == 1 and "offset" in v["extents"][0]}
    print("THIS STEP MODIFIES STORAGE")
    print("Creates a new graphics report directory and temporary APEX copies under work/asahi.")
    print("Temporary copies are removed automatically. Source image and earlier reports are retained.")
    print("Delete the newly printed result directory to reverse generated report writes.")
    parent.mkdir(parents=True, exist_ok=True)
    out = Path(tempfile.mkdtemp(prefix="graphics-audit-", dir=parent)).resolve()
    report = (out / "report.txt").open("w")
    result = {"previous": str(previous.resolve()), "image": str(image), "elf": [],
              "inventories": {}, "init_files": [], "apex": [], "issues": [],
              "limitations": ["Static candidate locations do not prove Android linker namespace resolution.",
                  "Selected APEX packages only; this is not exhaustive APEX or library coverage.",
                  "No Android binaries are executed; HAL operation and full VINTF compatibility remain untested."]}

    def log(text):
        print(text, flush=True)
        print(text, file=report, flush=True)

    def issue(text):
        result["issues"].append(text)
        log("UNRESOLVED: " + text)

    def run(command, stdout=None):
        r = subprocess.run(command, stdout=stdout or subprocess.PIPE,
                           stderr=subprocess.PIPE, timeout=120)
        if r.stderr:
            with (out / "tool-diagnostics.txt").open("ab") as f:
                f.write(("\n" + repr(command) + f" status={r.returncode}\n").encode())
                f.write(r.stderr)
        return r

    def command(part, path, mode="--cat"):
        return ["dump.erofs", f"--offset={offsets[part]}", f"--path={path}", mode, str(image)]

    def read(part, path):
        r = run(command(part, path))
        return r.stdout if r.returncode == 0 else b""

    def inventory(part, path):
        r = run(command(part, path, "--ls"))
        if r.returncode:
            return []
        text = r.stdout.decode(errors="replace")
        key = part + ":" + path
        result["inventories"][key] = text
        return listing_names(text)

    def inspect_elf(label, content):
        try:
            info = helpers.elf_info(content)
        except (ValueError, KeyError, UnicodeError, struct.error) as e:
            issue(f"{label}: {e}")
            return
        info["location"] = label
        result["elf"].append(info)
        log(f"ELF {label}: machine={info['machine']} aligned_16k={info['aligned_16k']}")
        log("  DT_NEEDED: " + (", ".join(info["needed"]) or "none"))
        if info["machine"] != 183 or not info["aligned_16k"]:
            issue(f"{label}: ARM64/16 KiB alignment requirement not met")

    code = 0
    try:
        log(f"Results: {out}")
        log(f"Previous completed audit: {previous}")
        if "system_b" in offsets:
            inventory("system_b", "/")
            log("SYSTEM_B ROOT INVENTORY:\n" + result["inventories"].get("system_b:/", "unavailable"))

        # Inspect direct hardware-specific dependencies, using static file candidates only.
        vendor_files = {leaf: kind for kind, leaf in inventory("vendor_a", "/lib64")}
        wanted = set()
        for elf in data.get("elf", []):
            if elf["partition"] == "vendor_a":
                wanted.update(n for n in elf["needed"] if any(t in n.lower() for t in
                    ("qti", "sdm", "gralloc", "mapper", "adreno", "gsl", "llvm-glnext", "libdrm", "hwcomposer")))
        visited = set()
        while wanted:
            name = sorted(wanted)[0]
            wanted.remove(name)
            if name in visited:
                continue
            if len(visited) >= 64:
                issue("vendor dependency inspection reached its 64-file limit")
                break
            visited.add(name)
            if vendor_files.get(name) not in (1, 7):
                issue(f"vendor dependency {name}: no /lib64 candidate")
                continue
            path = "/lib64/" + name
            content = read("vendor_a", path)
            before = len(result["elf"])
            inspect_elf("vendor_a:" + path, content)
            if len(result["elf"]) > before:
                wanted.update(n for n in result["elf"][-1]["needed"] if any(t in n.lower() for t in
                    ("qti", "sdm", "gralloc", "mapper", "adreno", "gsl", "llvm-glnext")) and n not in visited)

        for kind, leaf in inventory("vendor_a", "/etc/init"):
            if kind == 1 and leaf.endswith(".rc") and any(t in leaf.lower() for t in
                    ("composer", "allocator", "graphics", "display")):
                content = read("vendor_a", "/etc/init/" + leaf)
                if not content:
                    issue(f"vendor init {leaf}: could not read")
                    continue
                save = out / "init" / leaf
                save.parent.mkdir(exist_ok=True)
                save.write_bytes(content)
                result["init_files"].append(leaf)
                log("INIT " + leaf + "\n" + content.decode(errors="replace"))

        # Runtime bionic/linker and graphics APEX packages; package inventory covers both slots.
        selected = []
        for part in offsets:
            for root in ("/apex", "/system/apex"):
                entries = inventory(part, root)
                if entries:
                    log(f"APEX INVENTORY {part}:{root}: " + ", ".join(n for _, n in entries))
                for kind, leaf in entries:
                    if any(t in leaf.lower() for t in ("com.android.runtime", "graphics", "gralloc", "composer", "mesa")):
                        selected.append((part, root + "/" + leaf, kind))
        if not selected:
            issue("no runtime or graphics APEX candidate found in inspected roots")

        for index, (part, path, kind) in enumerate(selected):
            label = part + ":" + path
            log("APEX CANDIDATE " + label)
            result["apex"].append(label)
            with tempfile.TemporaryDirectory(prefix="apex-scratch-", dir=out) as scratch:
                scratch = Path(scratch)
                if kind == 2:
                    reader = lambda member: read(part, path + member)
                elif kind == 1:
                    package = scratch / "package.zip"
                    with package.open("xb") as f:
                        r = run(command(part, path), stdout=f)
                    if r.returncode or package.stat().st_size > 512 * 1024 * 1024:
                        issue(f"{label}: package read failed or exceeds 512 MiB limit")
                        continue
                    payload = scratch / "payload.img"
                    try:
                        extract_payload(package, payload)
                    except (ValueError, KeyError, zipfile.BadZipFile) as e:
                        issue(f"{label}: {e}")
                        continue
                    with payload.open("rb") as f:
                        f.seek(1024)
                        sb = f.read(1024)
                    if sb[:4] == bytes.fromhex("e2e1f5e0"):
                        def reader(member):
                            r = run(["dump.erofs", f"--path={member}", "--cat", str(payload)])
                            return r.stdout if r.returncode == 0 else b""
                    elif sb[56:58] == bytes.fromhex("53ef"):
                        if not shutil.which("debugfs"):
                            issue(f"{label}: ext4 payload; debugfs is missing")
                            continue
                        def reader(member):
                            r = run(["debugfs", "-R", "cat " + member, str(payload)])
                            return r.stdout if r.returncode == 0 else b""
                    else:
                        issue(f"{label}: unrecognized APEX payload filesystem")
                        continue
                else:
                    issue(f"{label}: symlink/unsupported APEX entry type {kind}")
                    continue

                manifest = reader("/apex_manifest.json")
                if manifest:
                    try:
                        log("  APEX manifest: " + json.dumps(json.loads(manifest)))
                    except (ValueError, UnicodeError):
                        issue(f"{label}: apex_manifest.json could not be parsed")
                if "com.android.runtime" in path:
                    for member in ("/bin/linker64", "/lib64/bionic/libc.so", "/lib64/bionic/libm.so", "/lib64/bionic/libdl.so"):
                        inspect_elf(label + member, reader(member))
                for member in ("/etc/vintf/manifest.xml", "/etc/vintf/compatibility_matrix.xml"):
                    content = reader(member)
                    if not content:
                        continue
                    try:
                        root = ET.fromstring(content)
                    except ET.ParseError:
                        continue
                    dest = out / "apex-vintf" / str(index) / Path(member).name
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(content)
                    log(f"APEX VINTF {label}{member}: {root.tag} {root.attrib}")
                    for hal in root.findall("hal"):
                        log(ET.tostring(hal, encoding="unicode"))

        log("Focused follow-up complete; exit 0 means collection completed, not compatibility.")
        for limitation in result["limitations"]:
            log("LIMITATION: " + limitation)
        log(f"Unresolved findings: {len(result['issues'])}")
    except Exception as e:
        issue(f"audit stopped: {type(e).__name__}: {e}")
        code = 1
    finally:
        result["exit_status"] = code
        (out / "summary.json").write_text(json.dumps(result, indent=2) + "\n")
        log(f"Send {out / 'report.txt'} and {out / 'summary.json'}")
        report.close()
    return code


if __name__ == "__main__":
    sys.exit(main())
