#!/usr/bin/env python3
"""Write /apex/apex-info-list.xml for the extracted tree, as apexd would after activating the apexes.

Original project script. linkerconfig's apex scan fails with "Can't read
/apex/apex-info-list.xml" when the file is absent (seen in logcat, stage B3f), and then
writes no per-apex linker configuration. For every <tree>/apex/<name>/apex_manifest.pb this reads
the apex name (protobuf field 1) and version (field 2), takes the .apex file path from
apexes.json (written by extract-apexes.py next to the tree), and writes one apex-info
element per apex with isFactory and isActive true. Usage: make-apex-info-list.py TREE_ROOT
This adds ONE small file to the tree; delete <tree>/apex/apex-info-list.xml to reverse.
"""
import json
from pathlib import Path
import sys
from xml.sax.saxutils import quoteattr


def varint(data, pos):
    value = shift = 0
    while True:
        byte = data[pos]
        pos += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, pos
        shift += 7


def manifest(data):
    name, version, pos = None, 0, 0
    while pos < len(data):
        tag, pos = varint(data, pos)
        field, wire = tag >> 3, tag & 7
        if wire == 0:
            value, pos = varint(data, pos)
            if field == 2:
                version = value
        elif wire == 2:
            length, pos = varint(data, pos)
            if field == 1:
                name = data[pos:pos + length].decode(errors="replace")
            pos += length
        elif wire == 1:
            pos += 8
        elif wire == 5:
            pos += 4
        else:
            break
    return name, version


def main():
    root = Path(sys.argv[1])
    files = {}
    listing = root.parent / "apexes.json"
    if listing.is_file():
        for entry in json.loads(listing.read_text()):
            if entry.get("name") and entry.get("status") == "ok":
                files[entry["name"]] = "/" + entry["file"]
    lines = ['<?xml version="1.0" encoding="utf-8"?>', "<apex-info-list>"]
    count = 0
    for directory in sorted((root / "apex").iterdir()):
        path = directory / "apex_manifest.pb"
        if not directory.is_dir() or not path.is_file():
            continue
        name, version = manifest(path.read_bytes())
        if not name:
            continue
        module = files.get(name, f"/system/apex/{name}.apex")
        lines.append(f"  <apex-info moduleName={quoteattr(name)} modulePath={quoteattr(module)} "
                     f"preinstalledModulePath={quoteattr(module)} versionCode=\"{version}\" versionName=\"\" "
                     f"isFactory=\"true\" isActive=\"true\" lastUpdateMillis=\"0\" provideSharedApexLibs=\"false\" />")
        count += 1
    lines.append("</apex-info-list>")
    target = root / "apex" / "apex-info-list.xml"
    target.write_text("\n".join(lines) + "\n")
    target.chmod(0o644)
    print(f"apex-info-list.xml: {count} apexes written to {target}")


if __name__ == "__main__":
    main()
