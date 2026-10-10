#!/usr/bin/env python3
"""Read-only scan of what Android init does at boot; executes nothing.

Original project script, same method as audit-init-rc.py and audit-art.py.
Reads from the recovery image's logical partitions:
- the main init scripts in system_a:/system/etc/init/hw and vendor_a:/etc/init/hw
  (audit-init-rc.py only read /etc/init), and lists the trigger order, every
  start/class_start/exec line and the command kinds in each action;
- the full stanzas of the core services a userspace bring-up needs first
  (logd, lmkd, servicemanagers, apexd, ueventd, zygote, surfaceflinger, ...);
- the property_contexts files and build.prop files (entry counts and sizes), to
  size a property area and choose between a stand-in and the image's own init;
- printable strings of /system/bin/init that show its start modes, its property
  service, and how it reacts when it cannot compute an SELinux context.
The result is a plan input. It shows declared behavior, not that anything runs.
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
BINARY_LIMIT = 32 << 20
RC_DIRS = [("system_a", "/system/etc/init/hw"), ("vendor_a", "/etc/init/hw")]
STANZA_DIRS = [("system_a", "/system/etc/init"), ("system_a", "/system/etc/init/hw"), ("vendor_a", "/etc/init"),
               ("vendor_a", "/etc/init/hw"), ("system_ext_a", "/etc/init"), ("product_a", "/etc/init")]
CORE = re.compile(r"^(logd|logd-reinit|logd-auditctl|lmkd|servicemanager|hwservicemanager|vndservicemanager|apexd\S*|"
                  r"ueventd|zygote\S*|surfaceflinger|bootanim|linkerconfig|derive_sdk|odsign|keystore2|vold|"
                  r"installd|netd|statsd|gatekeeperd|update_engine|prng_seeder|tombstoned|"
                  r"vendor\.graphics\.\S+|vendor\.hwcomposer\S*|vendor\.qti\.hardware\.display\.\S+)$")
PROPERTY_FILES = [("system_a", "/system/etc/selinux/plat_property_contexts"),
                  ("system_ext_a", "/etc/selinux/system_ext_property_contexts"),
                  ("product_a", "/etc/selinux/product_property_contexts"),
                  ("vendor_a", "/etc/selinux/vendor_property_contexts"),
                  ("vendor_a", "/odm/etc/selinux/odm_property_contexts")]
BUILD_PROPS = [("system_a", "/system/build.prop"), ("system_a", "/system/etc/prop.default"),
               ("system_ext_a", "/etc/build.prop"), ("product_a", "/etc/build.prop"),
               ("vendor_a", "/build.prop"), ("vendor_a", "/default.prop"), ("vendor_a", "/odm/etc/build.prop")]
BINARIES = [("system_a", "/system/bin/init"), ("system_a", "/system/bin/logd"), ("system_a", "/system/bin/logcat"),
            ("system_a", "/system/bin/lmkd"), ("system_a", "/system/bin/apexd"), ("system_a", "/system/bin/linkerconfig"),
            ("system_a", "/system/bin/hwservicemanager"), ("vendor_a", "/bin/vndservicemanager"),
            ("system_a", "/system/bin/surfaceflinger"), ("system_a", "/system/bin/bootanimation"),
            ("system_a", "/system/bin/app_process64")]
MARKERS = ["second_stage", "selinux_setup", "first_stage", "subcontext", "ueventd",
           "has incorrect label or no domain transition", "Could not get process context",
           "Could not get file context", "security_compute_create", "is_selinux_enabled", "setexeccon",
           "/dev/__properties__", "property_info", "property_service_for_system", "property_service",
           "ro.property_service.version", "ANDROID_SOCKET_", "/dev/socket/", "InitFatalReboot",
           "reboot,", "init_fatal_reboot_target", "SetupMountNamespaces", "/sys/fs/selinux",
           "androidboot.selinux", "ro.boot.", "/proc/cmdline", "/proc/bootconfig", "linkerconfig",
           "/apex/", "bootstrap", "apexd", "PropertyInit", "load_persist_props", "LoadPropertyInfoFromFile"]
WIDE = re.compile(r"(selinux|seclabel|context|propert|second.stage|first.stage|pid 1|reboot|mount namespace|"
                  r"apex|linkerconfig|bootstrap|enforc|permissive)", re.I)


def printable_strings(data, minimum=6):
    for match in re.finditer(rb"[\x20-\x7e]{%d,}" % minimum, data):
        yield match.group().decode()


def parse_rc(text, source):
    """Return actions and services of one .rc file, in file order."""
    actions, services, current = [], [], None
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        words = line.split()
        if words[0] == "on":
            current = {"kind": "action", "trigger": " ".join(words[1:]), "source": source, "line": number,
                       "commands": []}
            actions.append(current)
        elif words[0] == "service" and len(words) >= 3:
            current = {"kind": "service", "name": words[1], "exec": " ".join(words[2:]), "source": source,
                       "line": number, "options": []}
            services.append(current)
        elif words[0] == "import":
            current = None
        elif current is not None:
            current["commands" if current["kind"] == "action" else "options"].append(line[:240])
    return actions, services


def parse_props(text):
    entries, imports, long_values, value_bytes = {}, [], 0, 0
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("import "):
            imports.append(line[:160])
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        entries[key.strip()] = value.strip()
        value_bytes += len(key.strip()) + len(value.strip())
        if len(value.strip()) >= 92 and not key.strip().startswith("ro."):
            long_values += 1
    return entries, imports, long_values, value_bytes


def parse_contexts(text):
    rules, contexts, kinds = 0, set(), {"exact": 0, "prefix": 0}
    types = {}
    for raw in text.splitlines():
        words = raw.split()
        if not words or words[0].startswith("#") or len(words) < 2:
            continue
        rules += 1
        contexts.add(words[1])
        kinds["exact" if "exact" in words[2:] else "prefix"] += 1
        for word in words[2:]:
            if word in ("string", "int", "uint", "double", "bool", "enum"):
                types[word] = types.get(word, 0) + 1
    return rules, contexts, kinds, types


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
    here = Path(__file__)
    spec = importlib.util.spec_from_file_location("recovery_audit", here.with_name("audit-recovery.py"))
    recovery = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(recovery)
    spec = importlib.util.spec_from_file_location("graphics_audit", here.with_name("audit-graphics.py"))
    graphics = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(graphics)
    _, logical = recovery.partitions(image)
    print("THIS STEP MODIFIES STORAGE")
    print("Creates one new boot-sequence report directory under work/asahi. The image is only read; nothing is executed.")
    print("Delete the newly printed result directory to reverse it.")
    parent.mkdir(parents=True, exist_ok=True)
    out = Path(tempfile.mkdtemp(prefix="boot-sequence-", dir=parent)).resolve()
    (out / "rc").mkdir()
    print(f"Results: {out}")
    notes = []

    def base(part):
        if part not in logical:
            return None
        extents = logical[part]["extents"]
        if len(extents) != 1 or "offset" not in extents[0]:
            return None
        return ["dump.erofs", f"--offset={extents[0]['offset']}"]

    def cat(part, path, limit=LIMIT):
        command = base(part)
        if command is None:
            notes.append(f"SKIPPED {part}:{path}: partition missing or not one linear extent")
            return None
        result = subprocess.run(command + [f"--path={path}", "--cat", str(image)], capture_output=True, timeout=300)
        if result.returncode or not result.stdout:
            notes.append(f"ABSENT {part}:{path} (not readable as a regular file)")
            return None
        if len(result.stdout) > limit:
            notes.append(f"SKIPPED {part}:{path}: {len(result.stdout)} bytes, over limit")
            return None
        return result.stdout

    def listing(part, directory):
        command = base(part)
        if command is None:
            return []
        result = subprocess.run(command + [f"--path={directory}", "--ls", str(image)], capture_output=True, timeout=120)
        if result.returncode:
            notes.append(f"ABSENT {part}:{directory} (not listable)")
            return []
        return [name for kind, name in graphics.listing_names(result.stdout.decode(errors="replace"))
                if kind == 1 and name.endswith(".rc")]

    # 1. Main init scripts: trigger order and what each action does.
    actions, services = [], []
    for part, directory in STANZA_DIRS:
        for name in listing(part, directory):
            raw = cat(part, f"{directory}/{name}")
            if raw is None:
                continue
            source = f"{part}:{directory}/{name}"
            text = raw.decode(errors="replace")
            a, s = parse_rc(text, source)
            services.extend(s)
            if (part, directory) in RC_DIRS:
                actions.extend(a)
                (out / "rc" / f"{part}-{name}").write_text(text)
            else:
                actions.extend(x for x in a if any(c.split()[0] in ("start", "class_start", "trigger", "exec_start",
                                                                       "exec", "enable") for c in x["commands"]))
    hw_actions = [a for a in actions if any(a["source"].startswith(f"{p}:{d}/") for p, d in RC_DIRS)]
    print(f"\nMAIN INIT SCRIPTS read: {sorted({a['source'] for a in hw_actions})}")
    late = [c for a in hw_actions if a["trigger"] == "late-init" for c in a["commands"]]
    print("\nlate-init sequence (the boot order init follows):")
    for command in late:
        print(f"  {command}")
    print("\nACTIONS in the main init scripts (trigger: command kinds):")
    for a in hw_actions:
        kinds = {}
        for command in a["commands"]:
            kinds[command.split()[0]] = kinds.get(command.split()[0], 0) + 1
        print(f"  {a['trigger'][:60]:60} " + ", ".join(f"{k}={v}" for k, v in sorted(kinds.items())))
    print("\nSTART LINES in all scripts (trigger -> what it starts):")
    shown = 0
    for a in actions:
        for command in a["commands"]:
            if command.split()[0] in ("start", "class_start", "class_start_post_data", "exec_start", "exec",
                                      "exec_background", "enable", "trigger"):
                if shown < 160:
                    print(f"  [{a['trigger'][:50]}] {command[:120]}   ({a['source'].split(':', 1)[1]}:{a['line']})")
                shown += 1
    if shown > 160:
        print(f"  ... {shown - 160} more in summary.json")

    # 2. Core service stanzas.
    core = [s for s in services if CORE.match(s["name"])]
    print(f"\nCORE SERVICE STANZAS ({len(core)}):")
    for s in core:
        print(f"  service {s['name']} {s['exec'][:100]}   ({s['source']}:{s['line']})")
        for option in s["options"][:24]:
            print(f"      {option[:140]}")

    # 3. Property contexts and build properties.
    print("\nPROPERTY CONTEXT FILES:")
    all_contexts = set()
    for part, path in PROPERTY_FILES:
        raw = cat(part, path)
        if raw is None:
            continue
        rules, contexts, kinds, types = parse_contexts(raw.decode(errors="replace"))
        all_contexts |= contexts
        print(f"  {part}:{path}: {rules} rules, {len(contexts)} contexts, exact={kinds['exact']} "
              f"prefix={kinds['prefix']}, types {types}")
    print(f"  distinct contexts across files: {len(all_contexts)} (one property area file each under a split layout)")
    print("\nBUILD PROPERTY FILES:")
    merged = {}
    for part, path in BUILD_PROPS:
        raw = cat(part, path)
        if raw is None:
            continue
        entries, imports, long_values, size = parse_props(raw.decode(errors="replace"))
        merged.update(entries)
        print(f"  {part}:{path}: {len(entries)} properties, {size} key+value bytes, "
              f"{long_values} non-ro values >= 92 bytes, imports {imports or 'none'}")
    print(f"  merged distinct keys: {len(merged)}")
    for key in ("ro.property_service.version", "ro.zygote", "ro.logd.size", "persist.logd.size", "ro.hardware",
                "ro.boot.hardware", "ro.hardware.vulkan", "ro.hardware.egl", "ro.surface_flinger.primary_display_orientation",
                "ro.apex.updatable", "ro.boot.dynamic_partitions", "ro.vndk.version", "ro.board.platform",
                "dalvik.vm.heapsize", "ro.dalvik.vm.native.bridge", "ro.sf.lcd_density"):
        if key in merged:
            print(f"  {key}={merged[key][:100]}")

    # 4. Binaries: presence and init strings.
    print("\nBINARIES:")
    init_strings = []
    for part, path in BINARIES:
        raw = cat(part, path, BINARY_LIMIT)
        if raw is None:
            continue
        is_elf = raw[:4] == b"\x7fELF"
        print(f"  {part}:{path}: {len(raw)} bytes, ELF={is_elf}")
        if path == "/system/bin/init":
            init_strings = list(printable_strings(raw))
    if init_strings:
        joined = "\n".join(init_strings)
        print("\nINIT STRING MARKERS (present/absent):")
        for marker in MARKERS:
            print(f"  {'present' if marker in joined else 'absent ':8} {marker}")
        wide = [s for s in init_strings if WIDE.search(s) and len(s) < 300]
        (out / "init-strings.txt").write_text("\n".join(wide) + "\n")
        print(f"\nINIT MESSAGES about SELinux, properties, start modes and reboot ({len(wide)}; first 80, all in "
              f"init-strings.txt):")
        for s in wide[:80]:
            print(f"  {s[:160]}")
    for note in notes:
        print(note)
    (out / "summary.json").write_text(json.dumps({
        "image": str(image), "actions": actions, "core_services": core, "notes": notes,
        "property_contexts": sorted(all_contexts), "build_properties": merged,
        "limitations": ["Static text and strings only; nothing was executed.",
                        "Init strings show which messages exist, not which code path runs in a private root."]},
        indent=2))
    print(f"\nSend {out}/summary.json and the terminal output above")


if __name__ == "__main__":
    main()
