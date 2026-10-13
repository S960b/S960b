# M4st3r H4ck3r (retrieve) - writeup (the flag is the path of the anomalous PNG)

## Getting to know the task

The task description is one line:

> Heard you're a M4st3r H4ck3r, can you retrieve the flag from this archive?
> **trie** - highlighted in black

The word "trie" is highlighted - that is a deliberate hint. So first I looked at what was in the archive and what "trie" could refer to. (For the record: outside this challenge, "trie" is the name of the prefix-tree data structure - see e.g. https://en.wikipedia.org/wiki/Trie - and that is exactly the shape the archive turns out to have.)

## First look at the archive

```bash
$ unzip -l retrieve.zip | head
...
    48857  2026-08-07 15:13   retrieve/81/81/68/86/95/90/104/67/98/84/104/103/40/58/90/103/104/66/78/107/71/80/95/70/67/112/110/117/120/78/76/95/118/120/66/73/40/113/70/66/eow.png
    48857  2026-08-07 15:13   retrieve/81/114/93/105/43/58/66/106/122/112/45/43/75/115/101/51/118/107/45/53/117/65/89/79/87/58/116/72/99/116/121/72/46/76/70/111/65/90/77/46/97/110/100/76/eow.png
    ...
```

The archive has 7000 files. Each path is a deep directory tree where **every directory name is the ASCII code of one character**, and at the end there is `eow.png` (48857 bytes).

So the full paths are **strings encoded character by character**, and they are all collected into one giant prefix tree (trie). Each string is a flag candidate.

## Decoding the paths

```python
import zipfile
z = zipfile.ZipFile('/home/kali/Downloads/retrieve.zip')

def decode(n):
    return ''.join(chr(int(x)) for x in n.split('/')[1:-1])

names = z.namelist()
for n in names:
    print(decode(n))
```

The top level (`retrieve/`) is not a code but the archive's fixed root folder, so it must be skipped. In the final solver this is done not by index but by the "component is a number" test, so the code does not break if the platform renames the root folder.

The decoded strings look like this:

```text
FortID{waV3form_relay_frame_kernel_H34der...}
FortID{proxy_neon_term1nal_72082]
FortID{plot_twist_this_one_is_also_fake}
FortID{please_ignore_the_suspiciously_flag_shaped_string}
```

Most are obvious decoys (fake flags), some are not even closed with `}`. But all of them follow the same retro-theme dictionary (proxy, payload, sector, xmodem, dialup, cartridge...).

## What the PNGs are

I checked the PNG contents:

- Each PNG is a hacker meme ("HACK THE PLANET", "#1 HACK3R"), all of the same size **290x320**.
- Inside each PNG there is a tEXt chunk `Comment` with a hex string - some 118 bytes are hidden there.
- All 7000 files are pairwise unique (7000 unique MD5s).

I started looking into steganography in the pixels - but then something important showed up.

## The find: the "not like the others" file

When counting the image sizes in bulk, exactly **one file turned out to be 288x320**, while all the others are 290x320:

```text
retrieve/70/111/114/116/73/68/123/68/48/95/111/114/95/68/48/95/78/48/55/95/84/104/51/114/51/95/49/53/95/110/48/95/84/114/105/51/125/eow.png
```

This is the only deviation in 7000 files - a clear anomaly. Decoding its path:

```python
>>> ''.join(chr(int(x)) for x in path.split('/')[1:-1])
'FortID{D0_or_D0_...}'   (masked)
```

## Reading the flag

`D0_or_D0_...` is a reference to Yoda: "Do or do not, there is no try". Here "try" is replaced with the data structure **trie** - exactly the hint that was highlighted in the description. The hint worked literally: "there is no trie in this archive" is a red herring, while the real flag just sits in the path of the anomalous file. The flag was accepted.

## The full solver

`scripts/solve.py` reads only the first 33 bytes of each PNG (signature + IHDR), so scanning 7000 files takes a fraction of a second:

```python
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
```

Run on the live archive (verified):

```bash
$ python3 scripts/solve.py ~/Downloads/retrieve.zip
[*] scanned 7000 files from /home/kali/Downloads/retrieve.zip
    290x320: 6999 files
    288x320: 1 files

[+] anomalous file 288x320: retrieve/70/111/114/116/73/68/123/.../eow.png
    decoded: FortID{D0_or_D0_...}   (masked)

FLAG: FortID{D0_or_D0_...}   (masked)
```

## Dead ends (what I considered first)

1. **Steganography in the PNG pixels.** The memes look identical but the files are all unique (7000 unique MD5s), and each PNG carries a tEXt chunk with a hex string. Digging into pixels was tempting - but the size anomaly is a faster and exact signal, and it was found first.
2. **Reading all 7000 decoded paths by eye.** They are decoys built from a shared retro dictionary; there is no way to tell the real flag by reading.

## One-paragraph version

The archive is a trie: 7000 `eow.png` files whose directory paths are ASCII codes forming strings - mostly fake flags. The real one is hidden as an anomaly: 6999 images are 290x320 and exactly one is 288x320. The solver reads only the PNG header (33 bytes per file), counts sizes, finds the single 288x320 file, decodes its path, and prints the flag - `FortID{D0_or_D0_...}` (Yoda's "Do or do not, there is no try", with "try" turned into the hint word "trie").

## Lessons learned

1. A highlighted word in the description is a hint, not decoration: "trie" pointed at the tree structure of the paths before anything else.
2. When thousands of files are "the same", the most reliable signal is a measurable difference (size), not looking at content - count first, look later.
3. Reading only the file header (33 bytes) instead of the whole 7000-file archive makes the scan cost fractions of a second.
4. Decoys are part of the design: the unique-size file is the anomaly, and its path is the flag.

## Files

- `scripts/solve.py` - the solver: `python3 solve.py [retrieve.zip]` or `--dir <extracted>`