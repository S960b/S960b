# Flag from the Future - writeup (Conway's Game of Life, recover the flag text)

## Getting to know the task

The challenge `Flag from the Future` (Misc, author ipaljak) gives one file: an image that is the final state of Conway's Game of Life. The description tells us how the field was made:

- the hidden text was rasterized with a **DejaVu TTF 2.37** font;
- the font size was an **integer number of pixels**;
- every pixel with **lightness >= 50%** became a live cell;
- the automaton ran for some number of generations, and the resulting field is the image we get.

Goal: recover the hidden `FortID{...}` text.

So the description gives us a real head start: the font family (DejaVu), the version (2.37), and the threshold rule. What we still need to find: the exact font face (regular/bold/italic/mono?), the exact pixel size, the number of generations, and the text itself.

## Choosing the tools (and why)

I needed to work with images and simulate a cellular automaton. What I used and why:

- **Pillow (PIL)** - the standard Python library for opening and drawing images. It can both read our screenshot and render text with a TrueType font via `ImageFont.truetype`. I googled "python render text to image font" and Pillow is the first and most common answer.
- **NumPy** - the standard way to hold an image as a matrix of numbers. An image is just a 2D array of pixel values, and NumPy gives fast operations on it (`board.sum()`, comparisons, slicing).
- **SciPy** (`scipy.ndimage.convolve`) - the classic one-liner for the Game of Life step: count live neighbors of every cell with a 3x3 convolution kernel, then apply the rules. I remembered this trick from reading about cellular automata in Python.

All three are preinstalled on Kali (or `pip install pillow numpy scipy` in a venv).

## Step 1. Look inside the image (with a small script)

The first thing I did was look at the pixels with a tiny Python script - Pillow opens the PNG, NumPy turns it into a matrix of brightness numbers (0 = black, 255 = white). This is the "open the file and see" step, but for pixels:

```python
from PIL import Image
import numpy as np

a = np.asarray(Image.open('screenshot.png').convert('L'))  # 3062x288 matrix of 0..255
print(a.shape)               # (288, 3062)
print((a < 128).mean())      # share of dark pixels: 0.963
print((a >= 128).mean())     # share of light pixels: 0.037
alive = a >= 128             # binarization
print(int(alive.sum()))      # 32724
```

The output told me three things:

- the image is almost black: 96.3% of pixels are dark, only 3.7% are light. That is exactly what a Game of Life field looks like - sparse live cells on a black field;
- **binarization** simply means turning the image into either-or: every pixel with brightness >= 128 (that is >= 50% of 255, as the task says) becomes 1 (white, alive), everything else becomes 0 (black, dead). The result is the `alive` matrix of 0/1;
- counting the 1s (`alive.sum()`) gives **32724 live pixels** - the first concrete number of the challenge.

Why this step gets us closer: now the image is numbers we can compute with, not pixels on a screen.

## Step 2. Find the grid period (how the cells are drawn)

The image is a screenshot, so the cells are probably drawn larger than one pixel. Before simulating anything, I need to know the grid step: how many pixels one cell takes, and where the gap is.

Method (again a tiny script): for a candidate period `p`, take every pixel and look at its position modulo `p` on both axes - `(y mod p, x mod p)` gives one of `p*p` "residue classes". I count, for each class, what fraction of its pixels are alive. If `p` matches the real grid, the live cells live only inside the cell area, and the gap residues stay empty.

```python
for p in (6, 7, 8, 10, 12):                      # candidate periods
    frac = np.zeros((p, p))
    for ry in range(p):
        for rx in range(p):
            frac[ry, rx] = alive[ry::p, rx::p].mean()   # live fraction per residue
    print(p, frac.round(3))
```

Only **p = 8** gave a clean pattern:

- residues 0..5 (both axes): live fraction ~0.091, uniform - these 6 rows/cols are the cell body;
- residues 6..7: exactly 0 - these 2 rows/cols are the gap.

So the layout is: a **6x6 pixel cell**, then a **2-pixel gap**, period 8. And the arithmetic agrees: `32724 live pixels / 36 pixels per cell = 909` live cells - a round number that will anchor all later checks.

Why this step gets us closer: now we can downsample the screenshot from pixels to actual cells, giving a small binary grid (27x371) we can compare against simulations.

## Step 3. Digitize the screenshot into a cell grid

I downsampled: for each 8x8 block, the cell is alive if any of its 6x6 pixels is alive, then cropped the empty margins. Result: a **27x371 grid with 909 live cells** - saved as `evidence/grid_target.png`.

Why: now we have the target in the same form the simulation will produce - one bit per cell.

## Step 4. The forward-simulation check (why not run it backwards)

Game of Life is **not reversible**: many different initial fields converge to the same state, and there is no deterministic "run backwards". So the only sound method is forward: render a candidate text, simulate N generations, compare with the target.

Each generation step (the standard rules):

```text
neighbors = convolve(board, kernel)          # 3x3, center excluded
board = (neighbors == 3) | (board & (neighbors == 2))
```

- a dead cell becomes alive with exactly 3 live neighbors;
- a live cell survives with 2 or 3 live neighbors;
- otherwise it dies.

### How to compare two grids (Hamming distance)

The question is: do the simulated grid and the target grid match? "Match" here means *at every cell*: alive in both, dead in both.

I needed a number for "how different are two binary grids", so I googled "how to compare two binary images metric" and the standard answer is **Hamming distance**: count the positions where the two grids disagree.

For two grids `A` and `B` of the same size, it is defined as:

```text
D = sum over all cells (x, y) of  (A[x,y] != B[x,y])
```

i.e. each cell contributes 0 if the two values are equal, and 1 if they differ; `D` is the total count of differing cells. (In Python this is exactly `np.count_nonzero(A != B)`.)

If `D = 0`, the two grids are identical - the candidate text is exactly the one that produced the challenge image. If `D` is large, the candidate is wrong. So Hamming distance is the *yes/no test* that drives the whole search in the next step.

## Step 5. Find the parameters and the flag text

Now the search. What I vary: font face (within DejaVu: Sans, Sans Mono, Serif, each regular/bold), font size (integer, starting small), number of generations (1..~30), and the candidate text (starting from `FortID{` and expanding).

The trick that makes this feasible: the flag format `FortID{...}` fixes the first 7 characters. So I:

1. render `FortID{` + a guess of the next character(s) with a candidate font/size,
2. simulate increasing generations,
3. compare cell counts and positions with the target.

The parameters that fit: **DejaVu Sans Mono Bold, 16 px**. The candidate text that renders 1661 live cells initially and, after **18** generations, gives exactly 909 cells in exactly the target positions.

Iterating character by character (each new char must keep the simulation matching the target), the text resolves to:

```text
FortID{R3al_L1f3_...}   (masked; the full value reproduces 909 cells / exact match)
```

(The text is a wink at the challenge itself: "Real Life Has No Rewind Button" - there is no rewinding a Game of Life to recover the text, so you verify forward.)

## Step 6. Confirm (the faithful, reproducible run)

The final solver (`scripts/flag_from_the_future_verify.py`) does the whole chain in one go: render the flag with the found parameters (DejaVu Sans Mono Bold, 16 px, white on black, threshold >= 128), simulate 18 generations, and compare with `evidence/grid_target.png`:

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

Both grids are 27x371 and all 909 cells match. The chain is sealed.

## Dead ends (what I tried first)

1. **Running the automaton backwards.** Not possible: Game of Life is not reversible; this is precisely why forward simulation is the only sound check.
2. **Guessing the font family.** The first attempts with DejaVu Sans (proportional) did not produce 909 cells at the right positions; **Sans Mono Bold** (monospace, bold) was the one that matched. Monospace makes sense for a flag - every character has the same width, so the raster is regular.
3. **Wrong generation count.** Too few or too many generations changed the final cell count drastically (the field grows/shrinks quickly); 18 was the only count that landed on 909 cells in the target layout.

## Lessons learned

1. A cellular automaton field in an image is a grid: find the period (residue tables), downsample, and only then think about the text.
2. The live-cell count is a great anchor: 32724 pixels / 36 px-per-cell = 909, and 1661 initial cells turning into 909 after 18 generations are round numbers that validate every step.
3. Forward simulation is the only reliable check for irreversible automata: render, simulate, compare - never try to invert the rules.
4. The flag prefix `FortID{` fixes 7 characters and makes the search feasible character by character.
5. Pillow + NumPy + SciPy form the standard Python stack for "image + cellular automaton" CTF tasks - no exotic tools needed.

## Files

- `scripts/flag_from_the_future_verify.py` - render + simulate + compare (flag loaded from `FUTURE_FLAG` env / masked default)
- `evidence/screenshot.png` - the challenge image (final Game of Life state)
- `evidence/grid_target.png` - the screenshot digitized to 1 px per cell (27x371, 909 live cells)
- `evidence/life18.png` - the simulated result after 18 generations

## Reproduction

```bash
cd misc/flag-from-the-future
python3 -m venv .venv && source .venv/bin/activate
pip install pillow numpy scipy
export FUTURE_FLAG='FortID{...}'   # the full flag value
python3 scripts/flag_from_the_future_verify.py --out /tmp/life18.png --target evidence/grid_target.png
```