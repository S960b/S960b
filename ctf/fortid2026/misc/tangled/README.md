# Tangled - two Factorio blueprints hiding two QR codes

- **Platform:** FortiD CTF 2026
- **Category:** Misc
- **Author:** laragrr
- **Points:** 436
- **Date:** 2026-10-13
- **Flag:** `FortID{...}` (masked; confirmed - entered on the platform and accepted)
- **Artifact:** `handout_file_1.txt`, `handout_file_2.txt` (Factorio blueprint exports)

## What the task is

> An abandoned factory hides a message within its tangled machinery. Untangle the construction and uncover the secret.

Two text files. Each contains one long line starting with `0eN...` - looks like a random blend of letters, digits, pluses and slashes. There is no visible `FortID{...}`.

The files are **Factorio blueprint exports**: `0` + base64( zlib( JSON ) ), where the JSON describes a factory (machines, belts, tiles, wires) by coordinates. The secret is hidden **geometrically**, in the types and positions of the objects.

Both QR codes are recovered from the two files:

- file 1 tiles (`landfill` vs `foundation`) make a 29x29 QR (version 3) with the first part of the flag;
- file 1 entities XORed with file 2 tiles (both normalized to a 25x25 grid) make a 25x25 QR (version 2) with the second part;
- the parts assemble into the flag (with a small trailing-underscore subtlety, see `solve.md`).

Verified: the solver reproduces both QR fragments from the original files, and the resulting flag was entered on the platform and accepted.

Full analysis: [`solve.md`](solve.md).

## Folder structure

```text
misc/tangled/
├── README.md          # this file: the task context
├── solve.md           # the solution: decoding, masks, XOR, QR, assembly
├── assets/
│   ├── handout_file_1.txt   # blueprint 1: entities + mixed landfill/foundation tiles
│   └── handout_file_2.txt   # blueprint 2: landfill-only mask
├── scripts/
│   └── solve.py       # reproducible solver: file1 file2 --out <dir>
└── evidence/
    ├── 00_overview.png      # overview of the two blueprints
    ├── 01_qr_first.png      # QR from file-1 tile types (29x29)
    ├── 02_entities.png      # entity mask of file 1 (25x25)
    ├── 03_second_tiles.png  # landfill mask of file 2 (25x25)
    ├── 04_qr_xor.png        # QR from the XOR of the two masks (25x25)
    └── decoded_fragments.txt
```

## Reproduction

```bash
cd misc/tangled
python3 -m venv .venv && source .venv/bin/activate
pip install numpy opencv-python-headless pillow
python3 scripts/solve.py assets/handout_file_1.txt assets/handout_file_2.txt --out /tmp/tangled_out
```

Expected output (flag line masked here):

```text
blueprint 1: 290 entities; 841 tiles; 104 wires
blueprint 2: 0 entities; 330 tiles
first QR mask: (29, 29), black modules=429
entity mask: (25, 25), occupied modules=290
second tile mask: (25, 25), occupied modules=330
overlap: 144, XOR modules=332
first QR:  FortID{Wh3n_7h3_8e1ts_...}   (masked)
second QR: _5p49hett1_..._   (masked)
candidate flag: FortID{Wh3n_7h3_8e1ts_...}   (masked)
```