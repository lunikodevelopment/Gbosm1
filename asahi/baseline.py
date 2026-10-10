#!/usr/bin/env python3
"""Read Linux bring-up evidence; print JSON without changing target state."""
import gzip
import json
import os
import platform
import re
from pathlib import Path

CONFIGS = (
    "ARM64_16K_PAGES", "ANDROID_BINDER_IPC", "ANDROID_BINDERFS",
    "ANDROID_BINDER_DEVICES", "DMA_SHARED_BUFFER", "DMABUF_HEAPS",
    "DMABUF_HEAPS_SYSTEM", "DRM", "DRM_ASAHI", "IOMMU_SUPPORT",
    "NAMESPACES", "USER_NS", "PID_NS", "NET_NS", "CGROUPS", "MEMCG",
    "SECURITY_SELINUX", "PM", "PM_SLEEP", "INPUT", "INPUT_EVDEV",
    "EXT4_FS", "EROFS_FS", "F2FS_FS", "BLK_DEV_DM", "DM_VERITY", "NET",
)


def read_text(path):
    try:
        return {"value": path.read_text().strip()}
    except (OSError, UnicodeError) as error:
        return {"error": str(error)}


def parse_config(text):
    result = {}
    for line in text.splitlines():
        match = re.fullmatch(r"CONFIG_(\w+)=(.*)", line)
        disabled = re.fullmatch(r"# CONFIG_(\w+) is not set", line)
        if match:
            result[match[1]] = match[2]
        elif disabled:
            result[disabled[1]] = "n"
    return {"CONFIG_" + key: result.get(key, "unknown") for key in CONFIGS}


def kernel_config(root, release):
    attempts = []
    for name in ("proc/config.gz", f"boot/config-{release}",
                 f"lib/modules/{release}/build/.config"):
        path = root / name
        try:
            text = gzip.decompress(path.read_bytes()).decode() if name.endswith(".gz") else path.read_text()
            if not any(line.startswith("CONFIG_") for line in text.splitlines()):
                raise ValueError("no kernel configuration entries")
            return {"source": "/" + name, "symbols": parse_config(text), "attempts": attempts}
        except (OSError, UnicodeError, ValueError) as error:
            attempts.append({"path": "/" + name, "error": str(error)})
    return {"source": None, "symbols": parse_config(""), "attempts": attempts}


def collect(root=Path("/"), release=None):
    release = release or platform.release()
    drm = []
    for node in sorted((root / "sys/class/drm").glob("*")):
        record = {"name": node.name}
        for name in ("status", "modes", "device/uevent"):
            if (node / name).exists():
                record[name] = read_text(node / name)
        driver = node / "device/driver"
        if driver.is_symlink():
            record["driver"] = driver.resolve().name
        drm.append(record)
    return {
        "kernel_release": release,
        "page_size": os.sysconf("SC_PAGE_SIZE"),
        "kernel_config": kernel_config(root, release),
        "device_tree_compatible": read_text(root / "sys/firmware/devicetree/base/compatible"),
        "loaded_modules": read_text(root / "proc/modules"),
        "filesystems": read_text(root / "proc/filesystems"),
        "mounts": read_text(root / "proc/self/mountinfo"),
        "selinux_enforce": read_text(root / "sys/fs/selinux/enforce"),
        "drm": drm,
        "device_nodes": sorted(str(p.relative_to(root)) for pattern in
                               ("dev/dri/*", "dev/*binder*", "dev/binderfs/*", "dev/dma_heap/*")
                               for p in root.glob(pattern)),
        "limitations": "Configuration and node presence do not prove operational Android compatibility. No modules loaded or filesystems mounted.",
    }


if __name__ == "__main__":
    if platform.system() != "Linux":
        raise SystemExit("Run this collector on Fedora Asahi Linux, not macOS.")
    print(json.dumps(collect(), indent=2))
