#!/usr/bin/env python3
"""Decode the hexadecimal byte string printed on the LosFuzzys banner."""
import re
import sys

KEY = 0x42

if len(sys.argv) != 2:
    raise SystemExit(f"usage: {sys.argv[0]} HEX")

hex_text = "".join(re.findall(r"[0-9a-fA-F]", sys.argv[1]))
if len(hex_text) % 2:
    raise SystemExit("hex input has an odd number of digits")

data = bytes.fromhex(hex_text)
plain = bytes(b ^ KEY for b in data)
print(plain.decode("utf-8", errors="replace"))
