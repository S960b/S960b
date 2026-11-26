#!/usr/bin/env python3
"""Decode the 64x32 framebuffer byte strips of Glacier Catch (offline only).

Requires Pillow. The final ROM displays eight 15-row blocks and one 8-row
block. Each bit is one (approximately white) pixel.
"""
import argparse
import re
from PIL import Image

def read_byte(image, x, y):
    result = 0
    for bit in range(8):
        rgb = image.getpixel((x + bit, y))
        if isinstance(rgb, int):
            bright = rgb * 3
        else:
            bright = sum(rgb[:3])
        result = (result << 1) | (bright > 365)
    return result

def decode(image):
    image = image.convert("RGB")
    if image.size != (64, 32):
        raise ValueError(f"expected 64x32 image, got {image.size}")
    chunks = ((0,0,15), (0,15,15), (12,0,15), (12,15,15),
              (24,0,15), (24,15,15), (36,0,15), (36,15,15),
              (48,0,8))
    return bytes(read_byte(image, x, y+offset)
                 for x, y, height in chunks for offset in range(height))

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("image", help="path to a private 64x32 screenshot")
    args = parser.parse_args()
    data = decode(Image.open(args.image))
    print("Bytes:", len(data))
    print("Text:", data.rstrip(b"\0").decode("utf-8", "replace"))
    print("Flag matches:", [b"gctf{...}" for _ in re.findall(rb"gctf\{[^}]+\}", data)])
