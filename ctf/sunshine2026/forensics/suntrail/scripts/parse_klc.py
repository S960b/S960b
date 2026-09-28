#!/usr/bin/env python3
"""Parse a .klc keyboard layout and walk the trail hidden inside it.

A .klc (Microsoft Keyboard Layout Creator) LAYOUT line is:

    <scan code> <VK> <caps> <char for shift state 0> <char for state 1> <char for state 2> ...

In this file the *unshifted* column holds Unicode arrow characters instead of
normal symbols:

    2192 ->  2196 lower-left  2198 lower-right  25a0 stop

and the shifted column holds the letters. Walking the arrows across the physical
key grid (keys are offset half a key between rows) and collecting the shifted
characters spells the flag.
"""
import re
import sys
from pathlib import Path

# physical key positions: (row, x in half-key units)
COORDS = {
    'Q': (0, 0), 'W': (0, 2), 'E': (0, 4), 'R': (0, 6), 'T': (0, 8), 'Y': (0, 10),
    'U': (0, 12), 'I': (0, 14), 'O': (0, 16), 'P': (0, 18),
    'A': (1, 1), 'S': (1, 3), 'D': (1, 5), 'F': (1, 7), 'G': (1, 9), 'H': (1, 11),
    'J': (1, 13), 'K': (1, 15), 'L': (1, 17),
    'Z': (2, 2), 'X': (2, 4), 'C': (2, 6), 'V': (2, 8), 'B': (2, 10), 'N': (2, 12),
    'M': (2, 14),
}
POS = {v: k for k, v in COORDS.items()}
ARROWS = {0x2192: (0, +2), 0x2196: (-1, -1), 0x2198: (+1, +1), 0x25A0: None}
ARROW_NAMES = {0x2192: 'right', 0x2196: 'up-left', 0x2198: 'down-right', 0x25A0: 'stop'}


def step(key, delta):
    row, x = COORDS[key]
    dr, dx = delta
    return POS.get((row + dr, x + dx))


def parse(path):
    rows = {}
    for line in Path(path).read_text(errors='replace').splitlines():
        p = line.split()
        if len(p) >= 5 and re.fullmatch(r'[0-9a-f]{2}', p[0]) and p[1] in COORDS:
            codes = [int(v, 16) if v != '-1' else None for v in p[3:]]
            rows[p[1]] = (p[0], codes)
    return rows


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    rows = parse(sys.argv[1])
    print('%-3s %-4s %-11s %-8s %s' % ('SC', 'KEY', 'MARK', 'SHIFTED', 'NEXT'))
    key, route, flag, seen = next(iter(rows)), [], '', set()
    while key:
        sc, codes = rows[key]
        mark = codes[0] if codes else None
        letter = chr(codes[1]) if len(codes) > 1 and codes[1] else '?'
        seen.add(key)
        route.append(key)
        flag += letter
        nxt = step(key, ARROWS[mark]) if mark in ARROWS and ARROWS[mark] else None
        print('%-3s %-4s %-11s %-8s %s' % (sc, key, ARROW_NAMES.get(mark, '?'), letter, nxt or '(end)'))
        if nxt is None:
            break
        if nxt not in rows or nxt in seen:
            print('[!] trail leaves the file or loops at', nxt)
            break
        key = nxt

    print('\nroute : %s' % ' -> '.join(route))
    print('flag  : %s' % flag)
    print('checks: %d keys in file, %d visited, route covers all keys: %s'
          % (len(rows), len(seen), set(rows) == seen))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
