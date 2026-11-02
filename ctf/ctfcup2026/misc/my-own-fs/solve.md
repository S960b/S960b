# My Own FS: recovering a deleted file from a toy filesystem

[По-русски](solve.ru.md)

## 0. What is in the archive

`my-own-fs.zip` contains a Rust FUSE filesystem (`myfs`), its source, and a 4 MiB `disk.img` built with it. The task is a filesystem: find the flag on the disk.

The source is the specification, so the first move is to read the on-disk layout rather than guessing from hexdumps.

## 1. The layout

From the header comment of `main.rs`, with `N = image_size / 1338`:

```text
[0, N)                          inode list, one byte per inode: 0 free, 1 used
[N + i*1337, N + (i+1)*1337)    inode i, 1337 bytes
```

An inode starts with a 32-bit little-endian `next` pointer and a type byte. A head inode (type 1 file, type 2 dir) carries a 51-byte header:

```text
0  u32 next    4  u8 type     5  u16 mode    7  u32 uid   11  u32 gid
15 u64 size    23 i64 atime   31 i64 mtime   39 i64 ctime 47 u32 nlink
51 ... data
```

A continuation inode has only a 5-byte header (`next`, type 0) and then data. Directory data is a sequence of entries: `u32 inode`, `u8 name_len`, name.

Why this step: everything else follows from these offsets, and getting the header size wrong shifts every read by 46 bytes.

## 2. What the tooling hides

`myfs` has a `shuffle` subcommand that permutes the inode slots. It only reorders physical positions: the chains and the data inside each inode are untouched, so a scan by chain still works after shuffling.

`unlink` marks the inode-list byte as free and drops the directory entry, but it does not wipe the inode contents. That is the whole vulnerability: deleted files remain readable on disk.

Why this step: knowing that deletion is metadata-only turns the task into a carving exercise, not a recovery of a filesystem journal.

## 3. Walking the chains

The live tree is a decoy: `/README.txt` says "nothing to see here, everything was cleaned up" and `/todo.txt` is a shopping list. Reading the mounted filesystem gives nothing.

The useful move is to ignore the directory tree entirely and walk every inode in the table:

1. For `i` in `0 .. N`, read the 51-byte header at `N + i*1337`.
2. Skip inodes whose type is not 1 or 2.
3. Follow `next` while it is non-zero and not yet seen, concatenating the head data and each continuation's data.
4. Truncate to the recorded `size`.
5. Test the first bytes for a known signature.

Why this step: freed inodes keep type and size, so the chain is still walkable after deletion; nothing needs to be parsed from the directory.

## 4. Finding the flag

The scan returns exactly one file whose data starts with `\x89PNG\r\n\x1a\n`: inode 2979, a chain of 161 inodes, 214 153 bytes, a 900 by 160 RGB image. The image contains the flag text, so the last step is to look at it.

```bash
python3 scripts/carve_png.py disk.img carved.png
```

The carved file is byte-identical to the image that was recoverable through the deleted inode, which is the check that the chain walk is correct: a wrong chain border produces a truncated or padded PNG that fails to decode.

The flag is masked in this repository as `ctfcup{...}`.

Why this step: the PNG is not hidden in slack space or in unallocated blocks of the image, it is a normal file in freed inodes. The only reason it is unreachable through the mount is that the directory entry was removed.

## 5. Dead ends

- Mounting the image and browsing: the tree is deliberately empty of useful files.
- Strings over the raw image: the PNG data is compressed, so text search finds nothing.
- Assuming the shuffle seed matters: it permutes slots, not data, so the chain walk is indifferent to it.

## 6. Lessons

- For a custom filesystem, read the layout comment before any tooling; the offsets are the whole challenge.
- Deletion usually removes names, not bytes: scan the inode table instead of the directory tree.
- Validate a carve by decoding the result, not by its size.

One paragraph: the image stores a byte-per-inode table and 1337-byte inodes chained by a `next` pointer, deletion only flips the free bit and removes the directory entry, so scanning every inode and following its chain recovers a deleted 214 KB PNG in inode 2979, and the flag is written inside that image.
