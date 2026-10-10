#!/usr/bin/env python3
"""Read-only, focused Android recovery userspace audit; writes reports only."""
import argparse
import hashlib
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
import zlib


GRAPHICS = ("graphics", "display", "mapper", "allocator", "composer",
            "vulkan", "gralloc", "adreno", "mali", "gbm", "egl")


def partitions(image):
    """Validate primary GPT and LP metadata; resolve single-device extents."""
    total = image.stat().st_size
    with image.open("rb") as f:
        def read(offset, size):
            if offset < 0 or size < 0 or offset + size > total:
                raise ValueError("metadata read outside image")
            f.seek(offset)
            data = f.read(size)
            if len(data) != size:
                raise ValueError("truncated metadata")
            return data

        h = read(512, 512)
        if h[:8] != b"EFI PART":
            raise ValueError("GPT signature missing")
        hs = struct.unpack_from("<I", h, 12)[0]
        if not 92 <= hs <= 512:
            raise ValueError("invalid GPT header size")
        header = bytearray(h[:hs])
        header[16:20] = bytes(4)
        if zlib.crc32(header) & 0xffffffff != struct.unpack_from("<I", h, 16)[0]:
            raise ValueError("GPT header checksum mismatch")
        lba, count, size = struct.unpack_from("<QII", h, 72)
        if not 0 < count <= 4096 or not 128 <= size <= 4096:
            raise ValueError("invalid GPT entry dimensions")
        table = read(lba * 512, count * size)
        if zlib.crc32(table) & 0xffffffff != struct.unpack_from("<I", h, 88)[0]:
            raise ValueError("GPT entries checksum mismatch")
        gpt = {}
        for i in range(count):
            e = table[i * size:(i + 1) * size]
            if any(e[:16]):
                name = e[56:128].decode("utf-16le").rstrip("\0")
                first, last = struct.unpack_from("<QQ", e, 32)
                offset, length = first * 512, (last - first + 1) * 512
                if length <= 0 or offset + length > total:
                    raise ValueError(f"invalid GPT extent: {name}")
                gpt[name] = {"offset": offset, "bytes": length}
        sup = gpt["super"]
        base = sup["offset"]
        g = read(base + 4096, 4096)
        magic, gs = struct.unpack_from("<II", g)
        if magic != 0x616c4467 or not 52 <= gs <= 4096:
            raise ValueError("invalid LP geometry")
        geometry = bytearray(g[:gs])
        geometry[8:40] = bytes(32)
        if hashlib.sha256(geometry).digest() != g[8:40]:
            raise ValueError("LP geometry checksum mismatch")
        max_size = struct.unpack_from("<I", g, 40)[0]
        if not 128 <= max_size <= 64 * 1024 * 1024 or 12288 + max_size > sup["bytes"]:
            raise ValueError("invalid LP metadata size")
        m = read(base + 12288, max_size)
        hs = struct.unpack_from("<I", m, 8)[0]
        if struct.unpack_from("<I", m)[0] != 0x414c5030 or not 128 <= hs <= len(m):
            raise ValueError("invalid LP header")
        header = bytearray(m[:hs])
        header[12:44] = bytes(32)
        if hashlib.sha256(header).digest() != m[12:44]:
            raise ValueError("LP header checksum mismatch")
        ts = struct.unpack_from("<I", m, 44)[0]
        tables = m[hs:hs + ts]
        if len(tables) != ts or hashlib.sha256(tables).digest() != m[48:80]:
            raise ValueError("LP table checksum mismatch")

        def entries(descriptor, minimum):
            start, n, es = struct.unpack_from("<III", m, descriptor)
            if es < minimum or start + n * es > len(tables):
                raise ValueError("invalid LP table descriptor")
            return [tables[start + i * es:start + (i + 1) * es] for i in range(n)]

        extents = entries(92, 24)
        devices = entries(116, 64)
        logical = {}
        for p in entries(80, 52):
            name = p[:36].split(b"\0")[0].decode()
            _, first, n, _ = struct.unpack_from("<IIII", p, 36)
            if first + n > len(extents):
                raise ValueError("invalid LP extent index")
            mapped = []
            for e in extents[first:first + n]:
                sectors, kind, data, source = struct.unpack_from("<QIQI", e)
                if source >= len(devices):
                    raise ValueError("invalid LP device index")
                device = devices[source][24:60].split(b"\0")[0].decode()
                length = sectors * 512
                item = {"kind": kind, "device": device, "bytes": length}
                if kind == 0 and device == "super":
                    if (data + sectors) * 512 > sup["bytes"]:
                        raise ValueError("LP extent outside super")
                    item["offset"] = base + data * 512
                mapped.append(item)
            logical[name] = {"bytes": sum(e["bytes"] for e in mapped), "extents": mapped}
        return gpt, logical


def elf_info(data):
    if len(data) < 64 or data[:6] != b"\x7fELF\x02\x01":
        raise ValueError("not little-endian ELF64 (possibly a symlink)")
    machine = struct.unpack_from("<H", data, 18)[0]
    phoff = struct.unpack_from("<Q", data, 32)[0]
    es, n = struct.unpack_from("<HH", data, 54)
    if es < 56 or phoff + es * n > len(data):
        raise ValueError("truncated ELF program headers")
    headers = [struct.unpack_from("<IIQQQQQQ", data, phoff + i * es) for i in range(n)]
    loads = [{"offset": p[2], "vaddr": p[3], "align": p[7],
              "aligned_16k": p[7] >= 16384 and (p[3] - p[2]) % 16384 == 0}
             for p in headers if p[0] == 1]
    tags = []
    for p in headers:
        if p[0] == 2:
            if p[2] + p[5] > len(data):
                raise ValueError("truncated dynamic segment")
            for pos in range(p[2], p[2] + p[5] - 15, 16):
                tag, value = struct.unpack_from("<qQ", data, pos)
                if tag == 0:
                    break
                tags.append((tag, value))
    needed = []
    values = dict(tags)
    if any(t == 1 for t, _ in tags):
        addr, length = values[5], values[10]
        for p in headers:
            if p[0] == 1 and p[3] <= addr and addr + length <= p[3] + p[5]:
                start = p[2] + addr - p[3]
                strings = data[start:start + length]
                if len(strings) != length:
                    raise ValueError("truncated dynamic string table")
                for tag, value in tags:
                    if tag == 1:
                        end = strings.find(b"\0", value)
                        if value >= length or end < 0:
                            raise ValueError("invalid DT_NEEDED string")
                        needed.append(strings[value:end].decode())
                break
        else:
            raise ValueError("dynamic strings outside load segments")
    return {"machine": machine, "loads": loads, "needed": needed,
            "aligned_16k": bool(loads) and all(p["aligned_16k"] for p in loads)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", nargs="?", default="artifacts/mica-recovery.raw")
    parser.add_argument("--output-parent", default="work/asahi")
    args = parser.parse_args()
    image = Path(args.image).resolve()
    if not image.is_file():
        parser.error(f"image not found: {image}")
    if os.geteuid() == 0:
        parser.error("run as your ordinary user, without sudo")
    if not shutil.which("dump.erofs"):
        parser.error("dump.erofs is missing; install Fedora erofs-utils")
    print("THIS STEP MODIFIES STORAGE")
    print("Creates a new report directory only; image is opened read-only.")
    print("Existing reports are retained. Delete the new directory to reverse these writes.")
    parent = Path(args.output_parent)
    parent.mkdir(parents=True, exist_ok=True)
    out = Path(tempfile.mkdtemp(prefix="recovery-audit-", dir=parent)).resolve()
    report = (out / "report.txt").open("w")
    result = {"image": str(image), "scope": "focused static userspace audit",
              "properties": {}, "vintf": [], "elf": [], "issues": [],
              "limitations": ["APEX payload contents are not audited.",
                  "DT_NEEDED lists do not prove dependency resolution.",
                  "VINTF declarations do not prove runtime compatibility.",
                  "ELF alignment samples do not prove complete userspace compatibility.",
                  "No Android binaries are executed; no mounts or boot changes."]}

    def log(message):
        print(message, flush=True)
        print(message, file=report, flush=True)

    def issue(message):
        result["issues"].append(message)
        log("UNRESOLVED: " + message)

    def dump(offset, path, mode="--cat"):
        r = subprocess.run(["dump.erofs", f"--offset={offset}", f"--path={path}",
                            mode, str(image)], capture_output=True, timeout=120)
        if r.stderr:
            with (out / "tool-diagnostics.txt").open("ab") as f:
                f.write(f"\n{offset} {path} {mode} status={r.returncode}\n".encode())
                f.write(r.stderr)
        return r

    def directory(offset, path):
        r = dump(offset, path, "--ls")
        if r.returncode:
            return None
        items = []
        for line in r.stdout.decode(errors="replace").splitlines():
            match = re.match(r"\s*\d+\s+(\d+)\s+(.+?)\s*$", line)
            if match and match[2] not in (".", ".."):
                name = match[2]
                if "/" in name or "\0" in name:
                    raise ValueError("unexpected directory name")
                items.append((int(match[1]), name))
        return items

    try:
        log(f"Results: {out}")
        gpt, logical = partitions(image)
        result.update(gpt=gpt, logical_partitions=logical)
        log("Primary GPT and LP metadata checksums: valid")
        for name, part in logical.items():
            log(f"PARTITION {name}: bytes={part['bytes']} extents={part['extents']}")
            extents = part["extents"]
            if not extents:
                continue
            if len(extents) != 1 or "offset" not in extents[0]:
                issue(f"{name}: not a single mapped super extent; skipped")
                continue
            offset = extents[0]["offset"]
            with image.open("rb") as f:
                f.seek(offset + 1024)
                magic = f.read(4)
            if magic != bytes.fromhex("e2e1f5e0"):
                issue(f"{name}: not EROFS; skipped")
                continue

            found_props = False
            for path in ("/build.prop", "/etc/build.prop", "/system/build.prop", "/system/etc/build.prop"):
                r = dump(offset, path)
                props = {}
                if not r.returncode:
                    for line in r.stdout.decode(errors="replace").splitlines():
                        if line.startswith("ro.") and "=" in line:
                            key, value = line.split("=", 1)
                            props[key] = value
                if not props:
                    continue
                result["properties"][name] = {"path": path, "values": props}
                found_props = True
                log(f"PROPERTIES {name} {path}")
                for key, value in props.items():
                    if (".build.version." in key and "codenames" not in key or
                        key.endswith((".build.fingerprint", ".build.security_patch")) or
                        key in ("ro.board.api_level", "ro.vendor.api_level", "ro.product.first_api_level")):
                        log(f"  {key}={value}")
                break
            if not found_props and name.startswith(("system_", "vendor_", "product_")) and "dlkm" not in name:
                issue(f"{name}: no build properties at candidate paths; root inventory saved")
            inventories = out / "inventory" / name
            inventories.mkdir(parents=True)
            for path in ("/", "/apex", "/system/apex", "/bin/hw", "/lib64/hw", "/lib64/egl"):
                r = dump(offset, path, "--ls")
                if not r.returncode:
                    (inventories / (path.strip("/").replace("/", "_") or "root")).with_suffix(".txt").write_bytes(r.stdout)

            # Walk regular XML files below both possible VINTF roots.
            xml_count = 0
            for rootpath in ("/etc/vintf", "/system/etc/vintf"):
                stack = [(rootpath, 0)]
                while stack:
                    path, depth = stack.pop()
                    listing = directory(offset, path)
                    if listing is None:
                        continue
                    for kind, leaf in listing:
                        target = path + "/" + leaf
                        if kind == 2:
                            if depth >= 8:
                                issue(f"{name}:{target}: VINTF depth limit reached")
                            else:
                                stack.append((target, depth + 1))
                        elif kind == 1 and leaf.endswith(".xml"):
                            xml_count += 1
                            if xml_count > 512:
                                raise ValueError("unexpectedly large VINTF tree")
                            r = dump(offset, target)
                            if r.returncode:
                                issue(f"{name}:{target}: could not read XML")
                                continue
                            save = out / "vintf" / name / target.lstrip("/")
                            save.parent.mkdir(parents=True, exist_ok=True)
                            save.write_bytes(r.stdout)
                            try:
                                doc = ET.fromstring(r.stdout)
                            except ET.ParseError as e:
                                issue(f"{name}:{target}: {e}")
                                continue
                            hals = [h for h in doc.findall("hal") if any(
                                term in (h.findtext("name") or "").lower() for term in GRAPHICS)]
                            result["vintf"].append({"partition": name, "path": target,
                                "root": doc.tag, "attributes": doc.attrib,
                                "graphics_hals": [ET.tostring(h, encoding="unicode") for h in hals]})
                            log(f"VINTF {name}:{target} {doc.tag} {doc.attrib}")
                            for h in hals:
                                log(ET.tostring(h, encoding="unicode"))
                        elif kind == 7 and leaf.endswith(".xml"):
                            issue(f"{name}:{target}: VINTF symlink not resolved")

            targets = set()
            if name == "system_a":
                targets.update(("/system/bin/init", "/system/bin/surfaceflinger",
                                "/system/lib64/libbinder.so", "/system/lib64/libgui.so"))
            for path in ("/bin/hw", "/lib64/hw", "/lib64/egl"):
                for kind, leaf in directory(offset, path) or []:
                    if kind == 1 and (path.endswith("/egl") or any(t in leaf.lower() for t in GRAPHICS)):
                        targets.add(path + "/" + leaf)
            for path in sorted(targets):
                r = dump(offset, path)
                if r.returncode:
                    issue(f"{name}:{path}: ELF read failed")
                    continue
                try:
                    info = elf_info(r.stdout)
                except (ValueError, KeyError, struct.error, UnicodeError) as e:
                    issue(f"{name}:{path}: {e}")
                    continue
                info.update(partition=name, path=path)
                result["elf"].append(info)
                log(f"ELF {name}:{path}: machine={info['machine']} aligned_16k={info['aligned_16k']}")
                log("  DT_NEEDED: " + (", ".join(info["needed"]) or "none"))
                if info["machine"] != 183 or not info["aligned_16k"]:
                    issue(f"{name}:{path}: ARM64/16 KiB alignment requirement not met")

        log("Focused static audit complete; this is not a full Android compatibility pass.")
        for limitation in result["limitations"]:
            log("LIMITATION: " + limitation)
        log(f"Unresolved findings: {len(result['issues'])}")
        log(f"Send {out / 'report.txt'} and {out / 'summary.json'}")
        code = 0
    except Exception as e:
        issue(f"audit stopped: {type(e).__name__}: {e}")
        code = 1
    finally:
        result["exit_status"] = code
        (out / "summary.json").write_text(json.dumps(result, indent=2) + "\n")
        report.close()
    return code


if __name__ == "__main__":
    sys.exit(main())
