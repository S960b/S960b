#!/usr/bin/env python3
"""FortID 2026 / Tangled (Factorio blueprints) -- local, reproducible solver.

Usage:
    python3 solve_tangled.py 'handout_file_1.txt' 'handout_file_2.txt' --out tangled_evidence

Requires: numpy, pillow, opencv-python (cv2). Uses only local challenge files.
"""
from __future__ import annotations

import argparse
import base64
import json
import zlib
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont


def load_blueprint(path: Path) -> dict:
    export = path.read_text(encoding="utf-8-sig").strip()
    if not export.startswith("0"):
        raise ValueError(f"{path}: expected Factorio blueprint export starting with 0")
    try:
        decoded = base64.b64decode(export[1:], validate=True)
        blueprint = json.loads(zlib.decompress(decoded))
        return blueprint["blueprint"]
    except (ValueError, KeyError, zlib.error) as exc:
        raise ValueError(f"Could not decode {path} as Factorio blueprint") from exc


def mask_from_tiles(tiles: list[dict], tile_name: str | None = None) -> np.ndarray:
    """Map world coordinates to row=y, col=x. Bounding box of *all* tiles."""
    xy = [(int(t["position"]["x"]), int(t["position"]["y"])) for t in tiles]
    x0, y0 = min(x for x, _ in xy), min(y for _, y in xy)
    x1, y1 = max(x for x, _ in xy), max(y for _, y in xy)
    m = np.zeros((y1 - y0 + 1, x1 - x0 + 1), dtype=np.uint8)
    for tile in tiles:
        if tile_name is None or tile["name"] == tile_name:
            x, y = tile["position"]["x"], tile["position"]["y"]
            m[int(y-y0), int(x-x0)] = 1
    return m


def mask_from_entities(entities: list[dict]) -> np.ndarray:
    """Make a binary grid, independent of the entity kind, direction or wiring."""
    xy = [(e["position"]["x"], e["position"]["y"]) for e in entities]
    x0, y0 = min(x for x, _ in xy), min(y for _, y in xy)
    x1, y1 = max(x for x, _ in xy), max(y for _, y in xy)
    w, h = int(round(x1-x0))+1, int(round(y1-y0))+1
    m = np.zeros((h, w), dtype=np.uint8)
    for x, y in xy:
        col, row = int(round(x-x0)), int(round(y-y0))
        if m[row, col]:
            raise ValueError("Multiple entities at a single grid position")
        m[row, col] = 1
    return m


def qr_image(mask: np.ndarray, scale: int = 16, quiet: int = 4) -> np.ndarray:
    """1 = black QR module; render white with 4-cell quiet zone."""
    with_margin = np.pad(mask.astype(np.uint8), quiet, constant_values=0)
    bw = ((1 - with_margin) * 255).astype(np.uint8)
    return cv2.resize(bw, None, fx=scale, fy=scale, interpolation=cv2.INTER_NEAREST)


def decode_qr(mask: np.ndarray, name: str, out: Path) -> str:
    img = qr_image(mask)
    cv2.imwrite(str(out / f"{name}.png"), img)
    text, corners, _ = cv2.QRCodeDetector().detectAndDecode(img)
    if not text:
        raise RuntimeError(f"QR not decoded: {name}; inspect {out / (name + '.png')}")
    return text


def main() -> None:
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("file1", type=Path, help="first blueprint with entities and mixed landfill/foundation tiles")
    cli.add_argument("file2", type=Path, help="second blueprint with landfill mask")
    cli.add_argument("--out", type=Path, default=Path("tangled_evidence"))
    args = cli.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    bp1, bp2 = load_blueprint(args.file1), load_blueprint(args.file2)
    print(f"blueprint 1: {len(bp1['entities'])} entities; {len(bp1['tiles'])} tiles; {len(bp1.get('wires', []))} wires")
    print(f"blueprint 2: {len(bp2.get('entities', []))} entities; {len(bp2['tiles'])} tiles")

    first = mask_from_tiles(bp1["tiles"], "landfill")  # 29x29
    entities = mask_from_entities(bp1["entities"])     # 25x25
    second = mask_from_tiles(bp2["tiles"], "landfill")  # 25x25
    if first.shape != (29, 29) or entities.shape != (25, 25) or second.shape != (25, 25):
        raise ValueError(f"Unexpected dimensions: {first.shape}, {entities.shape}, {second.shape}")
    xor_mask = entities ^ second

    print(f"first QR mask: {first.shape}, black modules={int(first.sum())}")
    print(f"entity mask: {entities.shape}, occupied modules={int(entities.sum())}")
    print(f"second tile mask: {second.shape}, occupied modules={int(second.sum())}")
    print(f"overlap: {int((entities & second).sum())}, XOR modules={int(xor_mask.sum())}")

    part1 = decode_qr(first, "01_qr_first", args.out)
    for name, mask in [("02_entities", entities), ("03_second_tiles", second), ("04_qr_xor", xor_mask)]:
        cv2.imwrite(str(args.out / f"{name}.png"), qr_image(mask))
    part2 = decode_qr(xor_mask, "04_qr_xor", args.out)
    print(f"first QR:  {part1}")
    print(f"second QR: {part2}")
    if not part1.startswith("FortID{"):
        raise ValueError("Unexpected flag prefix")
    # The second QR ends in '_' even though this is not the final character of
    # the CTF flag. The meaningful text omits this extra separator and closes
    # with '}'. The flag was entered on the platform and accepted.
    proposed_flag = part1 + part2.rstrip("_") + "}"
    print(f"candidate flag: {proposed_flag}")
    (args.out / "decoded_fragments.txt").write_text(
        f"first QR: {part1}\nsecond QR: {part2}\ncandidate flag: {proposed_flag}\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
