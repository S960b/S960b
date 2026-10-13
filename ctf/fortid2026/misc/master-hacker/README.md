# M4st3r H4ck3r (retrieve) - the flag is the path of the anomalous file

- **Platform:** FortiD CTF 2026
- **Category:** Misc / Forensics (archive puzzle)
- **Author:** (not recorded in the workspace)
- **Points / Solves:** (not recorded in the workspace)
- **Date:** 2026-10-13
- **Flag:** `FortID{...}` (masked)
- **Artifact:** `retrieve.zip` - ~345 MB, 7000 files (not copied into the repo, see below)

## What the task is

The task is one line:

> Heard you're a M4st3r H4ck3r, can you retrieve the flag from this archive?
> **trie** - highlighted in black

The word "trie" is a hint to the "prefix tree" data structure.

Inside the archive there are 7000 files `eow.png` (48857 bytes each), and the path to each is a deep directory tree where **every directory name is the ASCII code of one character**:

```text
retrieve/81/81/68/86/95/.../eow.png      - decodes into a decoy string
```

So all the paths are strings collected into one prefix tree, and almost all of them are **fake flags** (`FortID{plot_twist_this_one_is_also_fake}` and similar).

The real flag comes from an anomaly: 6999 images are 290x320, and exactly one is **288x320**. Decoding the path of that single image gives the flag.

The full analysis and code are in [`solve.md`](solve.md).

## Folder structure

```text
misc/master-hacker/
├── README.md          # this file: the task context
├── solve.md           # the solution: analysis, code, flag
└── scripts/
    └── solve.py       # solver: path to the archive or --dir <extracted tree>
```

## About the heavy artifact

`retrieve.zip` (345 MB) is not put into the repository - it stays in `~/Downloads/retrieve.zip`, and the script takes it by default. A working copy with the extracted 7000 PNGs (for debugging) lives in `~/fortid/babymisc/extracted/`.

## Reproduction

```bash
python3 scripts/solve.py ~/Downloads/retrieve.zip     # same path by default
python3 scripts/solve.py --dir ~/fortid/babymisc/extracted
```

The script reads only the first 33 bytes of each PNG (signature + IHDR), so scanning all 7000 files takes a fraction of a second.