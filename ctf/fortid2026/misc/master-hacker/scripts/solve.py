#!/usr/bin/env python3
"""retrieve / "M4st3r H4ck3r" (FortID) - the flag is the path of the odd PNG.

The archive is a trie: every directory name is an ASCII code of one character, every
leaf is eow.png. The decoded paths are mostly decoy flags. 6999 images are 290x320 and
exactly one is 288x320 - its path is the real flag.

Usage:
    python3 solve.py [retrieve.zip]          # default: ~/Downloads/retrieve.zip
    python3 solve.py --dir <extracted-dir>
"""

import argparse
import collections
import os
import struct
import sys
import zipfile

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
DEFAULT_ZIP = os.path.expanduser("~/Downloads/retrieve.zip")


def decode_path(name):
    """retrieve/70/111/.../eow.png - the characters those ASCII codes spell.

    The archive has a fixed top-level dir (``retrieve/``) that is not a code, so
    leading non-numeric components are skipped; after the first code, anything
    non-numeric means the path is not built the way we expect.
    """
    parts = [p for p in name.split("/") if p][:-1]
    codes = []
    for part in parts:
        if part.isdigit():
            codes.append(int(part))
        elif codes:
            raise ValueError(f"unexpected non-numeric path component: {part!r}")
    return "".join(chr(c) for c in codes)


def png_size_from_bytes(head):
    if head[:8] != PNG_MAGIC or len(head) < 24:
        return None
    width, height = struct.unpack(">II", head[16:24])
    return width, height


def scan_zip(path):
    with zipfile.ZipFile(path) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            with zf.open(info) as fh:
                size = png_size_from_bytes(fh.read(33))
            yield info.filename, size


def scan_dir(path):
    for root, _dirs, files in os.walk(path):
        for name in files:
            full = os.path.join(root, name)
            with open(full, "rb") as fh:
                size = png_size_from_bytes(fh.read(33))
            yield os.path.relpath(full, path), size


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("archive", nargs="?", default=DEFAULT_ZIP, help="retrieve.zip")
    ap.add_argument("--dir", help="scan an already extracted tree instead of the zip")
    args = ap.parse_args()

    if args.dir:
        source, items = args.dir, scan_dir(args.dir)
    else:
        if not os.path.exists(args.archive):
            raise SystemExit(f"missing archive: {args.archive}")
        source, items = args.archive, scan_zip(args.archive)

    sizes = collections.Counter()
    by_size = collections.defaultdict(list)
    for name, size in items:
        sizes[size] += 1
        by_size[size].append(name)

    print(f"[*] scanned {sum(sizes.values())} files from {source}")
    for size, count in sizes.most_common():
        print(f"    {size[0]}x{size[1]}: {count} files")

    anomalies = [size for size, count in sizes.items() if count == 1]
    if not anomalies:
        print("[!] no unique-size file found")
        return 1

    for size in anomalies:
        name = by_size[size][0]
        decoded = decode_path(name)
        print(f"\n[+] anomalous file {size[0]}x{size[1]}: {name}")
        print(f"    decoded: {decoded}")
        if decoded.startswith("FortID{") and decoded.endswith("}"):
            print(f"\nFLAG: {decoded}")
            return 0

    print("[!] the anomalous path does not look like a flag")
    return 1


if __name__ == "__main__":
    sys.exit(main())
