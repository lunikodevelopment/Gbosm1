#!/usr/bin/env python3
"""Stand-in for init's property service inside the smoke-test sandbox (test only).

Original project script. Android programs set properties by connecting to
/dev/socket/property_service and sending PROP_MSG_SETPROP2 (0x00020001): a 32-bit
length and the name, a 32-bit length and the value; init answers with one 32-bit
result (0 = success). This stand-in accepts that protocol and writes the value into
the single-file property area made by make-property-area.py, using the same layout
(128-byte header, 20-byte trie nodes, 96-byte-plus-name prop_info records) and
Bionic's update order (copy the old value to the dirty backup area, set the serial's
dirty bit, write, store the new length and counter, bump the area serial). New names
get new trie nodes at the end of the used space.

Rules it mirrors from init: an existing ro.* property is not changed (PROP_ERROR_SET_FAILED),
and values of 92 bytes or more are refused except for new ro.* names (which it cannot store
here, so it refuses those too and logs them). ctl.* requests (start/stop a service) are
logged and answered with success; nothing is started unless a START rule names that exact
request (NAME=VALUE): then the rule's program is run once, and again only after it has exited,
as init restarts an on-demand service such as apexd for ctl.interface_start=aidl/apexservice.
There are no SELinux or uid
permission checks, no persistent properties and no property triggers. Every request is
appended to LOG_FILE.

Usage: sandbox-property-service.py AREA_FILE SOCKET_PATH LOG_FILE [NAME=VALUE=PROGRAM ...]
e.g. ctl.interface_start=aidl/apexservice=/path/start-apexd.sh. Runs until killed.
With GBOS_RC_ROOT (the tree) and GBOS_LAUNCHER (a program run as LAUNCHER SERVICE ARGV...)
set, a ctl.interface_start / ctl.start request without an explicit rule is looked up in the
image's .rc files (the service whose `interface` line or name matches, as init does) and that
service's command line is passed to the launcher; again at most one instance per service.
       sandbox-property-service.py --print-service TREE NAME   prints that service's command line.
"""
import os
import socket
import subprocess
import struct
import sys
import threading

HEADER = 128
SETPROP2 = 0x00020001
SET_FAILED, INVALID_VALUE, INVALID_CMD = 0x24, 0x14, 0x1B


def key(raw):
    return (len(raw), raw)


class Area:
    def __init__(self, path):
        self.fd = os.open(path, os.O_RDWR)
        self.lock = threading.Lock()

    def u32(self, offset):
        return struct.unpack("<I", os.pread(self.fd, 4, HEADER + offset))[0]

    def put32(self, offset, value):
        os.pwrite(self.fd, struct.pack("<I", value), HEADER + offset)

    def alloc(self, size):
        used = struct.unpack("<I", os.pread(self.fd, 4, 0))[0]
        end = (used + size + 3) & ~3
        if HEADER + end > os.fstat(self.fd).st_size:
            raise OSError("property area full")
        os.pwrite(self.fd, struct.pack("<I", end), 0)
        return used

    def child(self, node, part, create):
        """Find (or add) the sibling-tree entry named `part` under trie node `node`."""
        link = node + 16
        current = self.u32(link)
        while current:
            namelen = self.u32(current)
            name = os.pread(self.fd, namelen, HEADER + current + 20)
            if name == part:
                return current
            link = current + (8 if key(part) < key(name) else 12)
            current = self.u32(link)
        if not create:
            return 0
        new = self.alloc(20 + len(part) + 1)
        os.pwrite(self.fd, struct.pack("<IIIII", len(part), 0, 0, 0, 0) + part + b"\0", HEADER + new)
        self.put32(link, new)  # linked last, after the node is complete
        return new

    def set(self, name, value):
        with self.lock:
            node = 0
            for part in name.encode().split(b"."):
                node = self.child(node, part, True)
            info = self.u32(node + 4)
            raw = value.encode()
            if info:
                if name.startswith("ro."):
                    return SET_FAILED, "ro.* already set"
                serial = self.u32(info)
                old = os.pread(self.fd, serial >> 24, HEADER + info + 4)
                os.pwrite(self.fd, old + b"\0", HEADER + 20)  # dirty backup area after the root node
                self.put32(info, serial | 1)
                os.pwrite(self.fd, raw + b"\0" * (92 - len(raw)), HEADER + info + 4)
                self.put32(info, (len(raw) << 24) | (((serial | 1) + 1) & 0xFFFFFF))
                result = "updated"
            else:
                full = name.encode()
                info = self.alloc(96 + len(full) + 1)
                os.pwrite(self.fd, struct.pack("<I", len(raw) << 24) + raw + b"\0" * (92 - len(raw)) + full + b"\0",
                          HEADER + info)
                self.put32(node + 4, info)
                result = "added"
            os.pwrite(self.fd, struct.pack("<I", struct.unpack("<I", os.pread(self.fd, 4, 4))[0] + 1), 4)
            return 0, result


def read_exact(conn, count):
    data = b""
    while len(data) < count:
        chunk = conn.recv(count - len(data))
        if not chunk:
            raise EOFError
        data += chunk
    return data


def rc_services(root):
    """name -> argv, and interface key -> name, from the image's init .rc files."""
    root = os.path.abspath(root)
    files = [os.path.join(root, "system/etc/init/hw/init.rc")]
    for d in ("system/etc/init", "system_ext/etc/init", "product/etc/init", "vendor/etc/init", "odm/etc/init"):
        for base, _, names in sorted(os.walk(os.path.join(root, d))):
            files += [os.path.join(base, n) for n in sorted(names) if n.endswith(".rc")]
    apex = os.path.join(root, "apex")
    for name in sorted(os.listdir(apex)) if os.path.isdir(apex) else []:
        etc = os.path.join(apex, name, "etc")
        files += [os.path.join(etc, n) for n in sorted(os.listdir(etc)) if n.endswith(".rc")] if os.path.isdir(etc) else []
    services, interfaces, current = {}, {}, None
    for path in files:
        if not os.path.isfile(path):
            continue
        with open(path, errors="replace") as handle:
            for raw in handle:
                words = raw.split()
                if not words or words[0].startswith("#"):
                    continue
                if words[0] in ("on", "import"):
                    current = None
                elif words[0] == "service" and len(words) > 2:
                    current = words[1]
                    services.setdefault(current, words[2:])
                elif current and words[0] == "interface" and len(words) > 2:
                    key = "aidl/" + words[2] if words[1] == "aidl" else words[1] + "/" + words[2]
                    interfaces.setdefault(key, current)
    return services, interfaces


class Starter:
    """Runs a program for a ctl.* request, at most one instance at a time (like init's on-demand services)."""

    def __init__(self, rules, log):
        self.rules, self.log, self.running, self.lock = rules, log, {}, threading.Lock()
        self.launcher = os.environ.get("GBOS_LAUNCHER")
        self.services, self.interfaces = rc_services(os.environ["GBOS_RC_ROOT"]) if os.environ.get("GBOS_RC_ROOT") else ({}, {})

    def handle(self, request):
        program = self.rules.get(request)
        if program:
            key, command = program, [program]
        else:
            name, _, value = request.partition("=")
            service = (self.interfaces.get(value) if name == "ctl.interface_start" else
                       value if name == "ctl.start" and value in self.services else None)
            if not service or not self.launcher:
                return ("no .rc service declares it; nothing started" if self.launcher and name in ("ctl.interface_start", "ctl.start")
                        else "control request logged; nothing started (stand-in)")
            key, command = service, [self.launcher, service] + self.services[service]
        with self.lock:
            process = self.running.get(key)
            if process and process.poll() is None:
                return f"already running (pid {process.pid})"
            exited = "" if not process else f"; previous instance exited with {process.returncode}"
            self.running[key] = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return f"started {' '.join(command[1:] if not program else command)[:120]} (pid {self.running[key].pid}){exited}"


def serve(conn, area, log, starter):
    try:
        conn.settimeout(5)
        cmd = struct.unpack("<I", read_exact(conn, 4))[0]
        if cmd != SETPROP2:
            log(f"unsupported command 0x{cmd:x}")
            conn.sendall(struct.pack("<I", INVALID_CMD))
            return
        name = read_exact(conn, struct.unpack("<I", read_exact(conn, 4))[0]).decode(errors="replace")
        value = read_exact(conn, struct.unpack("<I", read_exact(conn, 4))[0]).decode(errors="replace")
        if name.startswith("ctl."):
            code, note = 0, starter.handle(f"{name}={value}")
        elif len(value.encode()) >= 92:
            code, note = INVALID_VALUE, "92+ byte value not stored by the stand-in"
        else:
            code, note = area.set(name, value)
        log(f"{'OK  ' if code == 0 else 'FAIL'} {name}={value[:120]} ({note})")
        conn.sendall(struct.pack("<I", code))
    except (OSError, EOFError, struct.error) as error:
        log(f"connection error: {error}")
    finally:
        conn.close()


def main():
    if len(sys.argv) == 4 and sys.argv[1] == "--print-service":
        services, _ = rc_services(sys.argv[2])
        if sys.argv[3] not in services:
            sys.exit(f"no .rc service named {sys.argv[3]}")
        print(" ".join(services[sys.argv[3]]))
        return
    if len(sys.argv) < 4:
        sys.exit(__doc__)
    area = Area(sys.argv[1])
    out = open(sys.argv[3], "a", buffering=1)
    lock = threading.Lock()

    def log(text):
        with lock:
            out.write(text + "\n")

    rules = {}
    for rule in sys.argv[4:]:
        request, program = rule.rsplit("=", 1)
        rules[request] = program
    starter = Starter(rules, log)

    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    if os.path.exists(sys.argv[2]):
        os.unlink(sys.argv[2])
    server.bind(sys.argv[2])
    os.chmod(sys.argv[2], 0o666)
    server.listen(64)
    log("property service stand-in listening" + "".join(f"; starts {os.path.basename(v)} on {k}" for k, v in rules.items())
        + (f"; starts .rc services through {os.path.basename(starter.launcher)} ({len(starter.services)} services,"
           f" {len(starter.interfaces)} interfaces declared)" if starter.launcher else ""))
    while True:
        conn, _ = server.accept()
        threading.Thread(target=serve, args=(conn, area, log, starter), daemon=True).start()


if __name__ == "__main__":
    main()
