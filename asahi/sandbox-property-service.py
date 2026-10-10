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
logged and answered with success, but nothing is started. There are no SELinux or uid
permission checks, no persistent properties and no property triggers. Every request is
appended to LOG_FILE.

Usage: sandbox-property-service.py AREA_FILE SOCKET_PATH LOG_FILE
Runs until killed.
"""
import os
import socket
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


def serve(conn, area, log):
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
            code, note = 0, "control request logged; nothing started (stand-in)"
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
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    area = Area(sys.argv[1])
    out = open(sys.argv[3], "a", buffering=1)
    lock = threading.Lock()

    def log(text):
        with lock:
            out.write(text + "\n")

    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    if os.path.exists(sys.argv[2]):
        os.unlink(sys.argv[2])
    server.bind(sys.argv[2])
    os.chmod(sys.argv[2], 0o666)
    server.listen(64)
    log("property service stand-in listening")
    while True:
        conn, _ = server.accept()
        threading.Thread(target=serve, args=(conn, area, log), daemon=True).start()


if __name__ == "__main__":
    main()
