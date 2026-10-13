# Flag from the Future - Conway's Game of Life, recover the flag text

- **Platform:** FortiD CTF 2026
- **Category:** Misc
- **Author:** ipaljak
- **Points / Solves:** (not recorded in the workspace)
- **Date:** 2026-10-09
- **Flag:** `FortID{R3al_L1f3_...}` (masked)
- **Artifact:** `screenshot.png` - the final state of a Conway's Game of Life simulation (the actual challenge image, found in `~/Downloads/`)

## What the task is

The input image is the final state of Conway's Game of Life. Originally the field was formed from raster text: a DejaVu TTF 2.37 font, integer pixel size, and every pixel whose lightness is >= 50% became a live cell. The goal is to recover the hidden `FortID{...}` text.

Game of Life rules (what I used):

- a dead cell becomes alive if it has exactly 3 live neighbors;
- a live cell survives with 2 or 3 live neighbors;
- otherwise the cell dies.

The dynamics are irreversible: you cannot run the automaton "backwards" to get the original field. So the approach is forward simulation: render a candidate text, simulate N generations, and compare with the image.

## The solution in a nutshell

1. Found the real challenge image `screenshot.png` (3062x288) in `~/Downloads/`.
2. Analyzed the grid: period 8 px (a 6x6 cell plus a 2px gap); 32724 live pixels = 909 cells x 36 px per cell.
3. Digitized it to a one-pixel-per-cell grid: 27x371, 909 live cells (`evidence/grid_target.png`).
4. Parameters (from the recorded solution): DejaVu Sans Mono Bold, 16 px, 18 generations.
5. The verifier renders the flag text white-on-black, threshold >= 128, runs 18 generations:
   - initial live cells: 1661, generations: 18, result live cells: 909.
6. Compared the simulated result with the real digitized screenshot: **mismatching cells: 0, exact match**.

The full analysis is in [`solve.md`](solve.md).

## Folder structure

```text
misc/flag-from-the-future/
├── README.md          # this file: the task context
├── solve.md           # the solution: analysis, grid recovery, confirmation, flag
├── scripts/
│   └── flag_from_the_future_verify.py   # render + simulate + compare
└── evidence/
    ├── screenshot.png    # the real challenge image (final Game of Life state)
    ├── grid_target.png   # screenshot digitized to 1 px per cell (27x371)
    └── life18.png        # the verifier's result after 18 generations
```

## Reproduction

```bash
cd misc/flag-from-the-future
python3 -m venv .venv && source .venv/bin/activate
pip install pillow numpy scipy
python3 scripts/flag_from_the_future_verify.py --out /tmp/life18.png --target evidence/grid_target.png
```

Expected output:

```text
initial live cells: 1661
generations: 18
result live cells: 909
saved: /tmp/life18.png
mismatching cells: 0
exact match
```