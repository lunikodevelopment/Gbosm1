#!/usr/bin/env python3
"""Write a single-file Bionic property area from the image's build.prop files.

Original project script. The layout follows the readiness-property fixture in
probe-libbinder.c, which Bionic accepted: a 128-byte header (bytes_used, serial,
magic 'PROP', version 0xfc6ed0ab), then a trie of 20-byte nodes (namelen, prop,
left, right, children, name) whose dotted-name parts form sibling binary search
trees ordered by (length, bytes), and 96-byte-plus-name prop_info records
(serial = value length << 24, 92 value bytes, name). Allocation starts at
data offset 112 (root node plus its 92-byte dirty backup). Values of 92 bytes or
more (Bionic's "long property" form) are skipped and counted.

Usage: make-property-area.py TREE_ROOT OUTPUT_FILE [KEY=VALUE ...]
The file is meant for /dev/__properties__ as ONE regular file owned by root and
not group/world writable, like the fixture. This is a stand-in for the property
service's initial state only: nothing updates it.
"""
import struct
import sys
from pathlib import Path

AREA_BYTES = 512 * 1024
HEADER = 128
FILES = ["system/build.prop", "system_ext/etc/build.prop", "product/etc/build.prop", "vendor/build.prop",
         "vendor/odm/etc/build.prop"]


def align4(n):
    return (n + 3) & ~3


class Node:
    __slots__ = ("name", "value", "children", "offset", "prop_offset", "left", "right", "child")

    def __init__(self, name):
        self.name, self.value, self.children = name, None, {}
        self.offset = self.prop_offset = 0
        self.left = self.right = self.child = None


def key(name):
    raw = name.encode()
    return (len(raw), raw)


def load_properties(root):
    props, used = {}, []
    for relative in FILES:
        path = Path(root) / relative
        if not path.is_file():
            continue
        count = 0
        for line in path.read_text(errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or line.startswith("import ") or "=" not in line:
                continue
            name, value = line.split("=", 1)
            props[name.strip()] = value.strip()
            count += 1
        used.append((relative, count))
    derive_product_properties(props)
    return props, used


def derive_product_properties(props):
    """Android's init sets ro.product.<x> from ro.product.<partition>.<x> in property_source_order.

    Programs such as app_process read the plain names (ro.product.cpu.abilist64); the build.prop
    files only carry the per-partition names. The first partition in the order that has a value wins.
    """
    order = props.get("ro.product.property_source_order", "odm,vendor,product,system_ext,system").split(",")
    names = {}
    for full in list(props):
        parts = full.split(".")
        if len(parts) > 3 and parts[:2] == ["ro", "product"] and parts[2] in order:
            names.setdefault(".".join(parts[3:]), []).append((order.index(parts[2]), props[full]))
    for rest, values in names.items():
        props.setdefault("ro.product." + rest, min(values)[1])


def build(props):
    top = Node("")
    long_skipped = []
    for name, value in props.items():
        if len(value.encode()) >= 92 or not name or ".." in name or name.startswith(".") or name.endswith("."):
            long_skipped.append(name)
            continue
        node = top
        for part in name.split("."):
            node = node.children.setdefault(part, Node(part))
        node.value = value
    return top, long_skipped


def write(top):
    area = bytearray(AREA_BYTES)
    cursor = [112]

    def alloc(size):
        offset = cursor[0]
        cursor[0] = align4(offset + size)
        if cursor[0] > AREA_BYTES - HEADER:
            raise SystemExit("property area full")
        return offset

    def put(offset, fmt, *values):
        struct.pack_into(fmt, area, HEADER + offset, *values)

    def lay_out(node):
        # allocate this node's children as a balanced BST of siblings, depth first
        names = sorted(node.children, key=key)

        def balanced(items):
            if not items:
                return None
            mid = len(items) // 2
            child = node.children[items[mid]]
            raw = child.name.encode()
            child.offset = alloc(20 + len(raw) + 1)
            put(child.offset, "<I", len(raw))
            area[HEADER + child.offset + 20:HEADER + child.offset + 20 + len(raw)] = raw
            child.left = balanced(items[:mid])
            child.right = balanced(items[mid + 1:])
            return child

        node.child = balanced(names)

    # collect the siblings of one node; prop_info records the FULL dotted name
    def walk(node, prefix):
        lay_out(node)
        stack = [node.child] if node.child else []
        flat = []
        while stack:
            child = stack.pop()
            flat.append(child)
            for side in (child.left, child.right):
                if side:
                    stack.append(side)
        return flat

    # top-level fixed root at offset 0
    def process(node, prefix, offset_of_node):
        flat = walk(node, prefix)
        if node.child:
            put(offset_of_node + 16, "<I", node.child.offset)
        for child in flat:
            if child.left:
                put(child.offset + 8, "<I", child.left.offset)
            if child.right:
                put(child.offset + 12, "<I", child.right.offset)
        for child in flat:
            full = f"{prefix}{child.name}"
            if child.value is not None:
                value, name = child.value.encode(), full.encode()
                info = alloc(96 + len(name) + 1)
                put(info, "<I", len(value) << 24)
                area[HEADER + info + 4:HEADER + info + 4 + len(value)] = value
                area[HEADER + info + 96:HEADER + info + 96 + len(name)] = name
                put(child.offset + 4, "<I", info)
            if child.children:
                process(child, full + ".", child.offset)

    process(top, "", 0)
    struct.pack_into("<IIII", area, 0, cursor[0], 1, 0x504F5250, 0xFC6ED0AB)
    return bytes(area)


def main():
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    props, used = load_properties(sys.argv[1])
    for extra in sys.argv[3:]:
        name, value = extra.split("=", 1)
        props[name] = value
    top, skipped = build(props)
    data = write(top)
    out = Path(sys.argv[2])
    out.write_bytes(data)
    out.chmod(0o444)
    print(f"property area: {len(props) - len(skipped)} properties written ({len(skipped)} skipped as 92+ bytes or malformed), "
          f"{struct.unpack_from('<I', data)[0]} of {AREA_BYTES - HEADER} data bytes used; sources {used}")


if __name__ == "__main__":
    main()
