# My Own FS

[Русская версия](README.ru.md) | [Solving path](solve.md)

- **Event:** CTFCUP 2026 qualifier
- **Points:** 100
- **Category:** misc / filesystem forensics
- **Task:** https://game.ctfcup.online/tasks/my-own-fs
- **Attachment:** `my-own-fs.zip`, with a Rust FUSE filesystem and `disk.img`
- **Flag:** `ctfcup{...}` (masked)

A toy filesystem with a byte-per-inode table (`[0, N)`, `N = size / 1338`) and 1337-byte inodes chained by a `next` pointer. A head inode carries a 51-byte header with type, mode, uid, gid, size and timestamps; continuations carry only `next` and type.

`unlink` clears the inode-list byte and drops the directory entry, but does not wipe data, and `shuffle` only permutes physical slots. The live tree is an empty decoy, so the solution is to walk every inode, follow its chain, and test the payload for a signature. That recovers a deleted 214 153-byte PNG from inode 2979 (161 inodes, 900 by 160), which contains the flag.

`scripts/carve_png.py disk.img carved.png` performs the scan and writes the image.
