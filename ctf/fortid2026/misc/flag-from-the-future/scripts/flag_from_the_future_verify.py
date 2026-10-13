#!/usr/bin/env python3
"""Reproduce a recorded FortID CTF 'Flag from the Future' candidate.

This verifies the forward Conway Game of Life simulation, not the
historical process that recovered the flag from the provided screenshot.

Dependencies: pillow, numpy, scipy.
"""
from __future__ import annotations
import argparse
import os
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from scipy.ndimage import convolve

# Masked default in the repo. To reproduce the real check, export the full flag:
#   export FUTURE_FLAG='FortID{...}'   (the value that renders 1661 cells and
#   matches the challenge screenshot after 18 generations with 0 mismatches)
FLAG = os.environ.get("FUTURE_FLAG", "FortID{R3al_L1f3_...}")
MASKED = "..." in FLAG
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"
GENERATIONS = 18
SIZE = 16
KERNEL = np.array([[1, 1, 1], [1, 0, 1], [1, 1, 1]], dtype=np.uint8)


def render_text(text: str, font_path: str, size: int, pad: int = 40) -> np.ndarray:
    font = ImageFont.truetype(font_path, size)
    w = int(font.getlength(text)) + pad * 2 + 20
    h = pad * 2 + size * 3
    canvas = Image.new("L", (w, h), 0)
    ImageDraw.Draw(canvas).text((pad, pad), text, fill=255, font=font)
    return np.asarray(canvas) >= 128


def advance(board: np.ndarray, steps: int = GENERATIONS) -> np.ndarray:
    alive = board.copy()
    for _ in range(steps):
        neighbors = convolve(alive.astype(np.uint8), KERNEL, mode="constant", cval=0)
        alive = (neighbors == 3) | (alive & (neighbors == 2))
    return alive


def bounding_crop(board: np.ndarray) -> np.ndarray:
    points = np.argwhere(board)
    if not len(points):
        return board[:0, :0]
    low = points.min(axis=0)
    high = points.max(axis=0) + 1
    return board[low[0]:high[0], low[1]:high[1]]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--font", default=FONT, help="Path to DejaVu Sans Mono Bold TTF")
    ap.add_argument("--target", type=Path,
                    help="Original one-pixel-per-cell, white-alive PNG to compare (no scaling)")
    ap.add_argument("--out", type=Path, default=Path("life18.png"))
    args = ap.parse_args()

    if MASKED:
        print("FLAG is masked in this repo copy. Export the full flag to reproduce the check:")
        print("  export FUTURE_FLAG='FortID{...}'")
        return 2

    board = render_text(FLAG, args.font, SIZE)
    print(f"initial live cells: {int(board.sum())}")
    board = advance(board)
    print(f"generations: {GENERATIONS}")
    print(f"result live cells: {int(board.sum())}")
    assert int(board.sum()) == 909, "Unexpected font/rasterization; check DejaVu version"
    Image.fromarray(board.astype(np.uint8) * 255).save(args.out)
    print(f"saved: {args.out}")

    if args.target is not None:
        other = np.asarray(Image.open(args.target).convert("L")) >= 128
        expected = bounding_crop(board)
        observed = bounding_crop(other)
        if expected.shape != observed.shape:
            print(f"bounding-box shape mismatch: model={expected.shape} target={observed.shape}")
        else:
            mismatches = int(np.count_nonzero(expected ^ observed))
            print(f"mismatching cells: {mismatches}")
            print("exact match" if mismatches == 0 else "NOT AN EXACT MATCH")


if __name__ == "__main__":
    sys.exit(main())
