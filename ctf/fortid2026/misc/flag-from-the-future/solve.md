# Flag from the Future - writeup (Conway's Game of Life, recover the flag text)

## Getting to know the task

The challenge `Flag from the Future` (Misc, author ipaljak) gives an image: the final state of a Conway's Game of Life simulation. The original text was rasterized with a DejaVu TTF 2.37 font (integer pixel size), and every pixel with lightness >= 50% became a live cell. Goal: recover the hidden `FortID{...}` text.

What was available to me:

- a draft writeup with recorded parameters (DejaVu Sans Mono Bold, 16 px, 18 generations, 909 live cells) and the flag text `FortID{R3al_L1f3_...}`;
- a verification script (`flag_from_the_future_verify.py`) that renders the flag, simulates 18 generations, and can compare with a target grid;
- `life18.png` (the verifier's own output);
- **no original screenshot in the draft folder** - the draft explicitly says the original image and the digitization code were lost, and the only `--target` check done before was against the script's own output (a self-check).

My task: find the real challenge artifact, digitize it, and confirm the recorded chain independently - or tear it down if it does not hold.

## Finding the real artifact

I searched `~/Downloads/` for images. The candidate that matched a Game of Life state immediately:

- `screenshot.png` - 3062x288, almost binary (96% black, 4% white), a wide strip of sparse white pixels on black.

## Reading the grid (how I recovered the cell structure)

Using Python (Pillow + NumPy), read-only analysis:

1. **Binarization:** white pixels (>= 128) = live cells. Alive pixels: **32724**.
2. **Bounding box of the content:** rows 32..245, cols 55..3020 (nothing outside - periphery check returned 0).
3. **Grid period:** I tested residue tables - the fraction of alive pixels by `(y mod p, x mod p)` for p = 6, 7, 8, 10, 12. Only **p = 8** gave a clean pattern:
   - residues 0..5: alive fraction ~0.091 (uniform),
   - residues 6..7: exactly 0.
   
   That means the grid is: a cell of 6x6 pixels, then a 2-pixel gap - period **8 px**. The screenshot is the automaton field upscaled 8x (6x6 cell + 2x2 gap).

4. **Sanity check:** 32724 live pixels / 36 px per cell = **909 cells** - exactly the recorded live-cell count. This was the first strong signal that `screenshot.png` is the real final state.

5. **Digitization:** I downsampled the screenshot to one pixel per cell (a cell is alive if any of its 6x6 pixels is alive), then cropped empty margins. Result: grid **27x371, 909 live cells** - saved as `evidence/grid_target.png`.

## The confirmation (the missing proof)

The draft's verifier was extended with the real target: render the flag text white-on-black (DejaVu Sans Mono Bold, 16 px), threshold >= 128, simulate 18 generations, compare with the digitized screenshot (both cropped to the live-cell bounding box):

```bash
python3 scripts/flag_from_the_future_verify.py --out /tmp/life18.png --target evidence/grid_target.png
```

Output:

```text
initial live cells: 1661
generations: 18
result live cells: 909
saved: /tmp/life18.png
mismatching cells: 0
exact match
```

Both grids are 27x371 and all 909 cells match. This is an **independent confirmation**: the simulated result of the recorded flag equals the actual challenge screenshot byte for byte - not a self-check.

## Why this approach works (irreversibility)

Game of Life is not reversible: many initial fields can converge to the same state, and there is no deterministic "run it backwards". So you never search the past by inverting the automaton; you render a candidate text, simulate it forward, and compare with the image. Each step of the Game of Life rules:

```text
neighbors = convolve(board, kernel)          # 3x3, center excluded
board = (neighbors == 3) | (board & (neighbors == 2))
```

- a dead cell is born with exactly 3 live neighbors;
- a live cell survives with 2 or 3 live neighbors;
- otherwise it dies.

## The parameters (kept from the draft, now proven)

- Font: DejaVu Sans Mono Bold (`/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf`)
- Size: 16 px
- Generations: 18
- Threshold: pixel value >= 128 (lightness >= 50%)
- Initial live cells: 1661
- Final live cells: 909

## The flag

The confirmed flag:

```text
FortID{R3al_L1f3_...}   (masked; the full value reproduces 909 cells / exact match)
```

(The text is a wink at the challenge itself: "Real Life Has No Rewind Button" - there is no rewinding a Game of Life to recover the text, so you verify forward.)

## What remains honestly unproven

- The historical path by which the flag text was originally recovered (character-by-character search, font trial, generation count) is not documented in the surviving draft - only the parameters and the result. I do not claim it.
- The platform's acceptance of the flag was not found in the available history.
- What IS proven now: with the recorded parameters and this flag text, the forward simulation reproduces the real challenge screenshot exactly (909/909 cells, 0 mismatches). If the text were wrong, the simulated state would not match a 27x371 field with exactly 909 live cells in the same positions.

## Dead ends (what I tried first)

1. **Running the automaton backwards.** Not possible: Game of Life is not reversible; this is precisely why forward simulation is the only sound check.
2. **Guessing the font family.** The draft recorded DejaVu Sans Mono Bold; I verified the exact TTF exists on this box and renders 909 cells. A different font would not match 909 cells at the same positions.
3. **Treating the draft's `--target` self-check as proof.** It only compared against the script's own output. The real confirmation required the actual screenshot, which I found and digitized.

## Lessons learned

1. A recorded "909 cells / 0 mismatches" claim without the original image is unverified; the artifact may still exist elsewhere (here: `~/Downloads/screenshot.png`).
2. A near-binary wide image with sparse white pixels is the signature of a cellular automaton field; alive-pixel count divided by recorded cell count reveals the upscale factor immediately (32724 / 909 = 36 = 6x6).
3. Residue tables across candidate periods find the grid exactly: period 8 with residues 6..7 empty proves a 6x6 cell + 2x2 gap layout.
4. Forward simulation is the only reliable check for irreversible automata: render, simulate, compare - never try to invert the rules.

## Files

- `scripts/flag_from_the_future_verify.py` - render + simulate + compare (flag is masked in the repo; set the full flag to reproduce: see the comment in the script)
- `evidence/screenshot.png` - the real challenge image (final Game of Life state)
- `evidence/grid_target.png` - the screenshot digitized to 1 px per cell (27x371, 909 live cells)
- `evidence/life18.png` - the verifier's result after 18 generations

## Reproduction

```bash
cd misc/flag-from-the-future
python3 -m venv .venv && source .venv/bin/activate
pip install pillow numpy scipy
# for the full check, set the real flag first (see the script header), then:
python3 scripts/flag_from_the_future_verify.py --out /tmp/life18.png --target evidence/grid_target.png
```