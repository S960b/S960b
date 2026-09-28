#!/usr/bin/env python3
# VecNet helper: recover the 7z password from the inverted rule and SHA256 hash.
import hashlib, itertools, string, sys

TARGET = "d8dd241199d2617765d7613fdd1df5358297b55f258647fe463de586bbfe3ebf"
MAGIC = "sunshinectf8_"
SPECIALS = "!@#$%^&*()-_=+[]{}|;:,.<>?/~`"

# Start with names seen in the challenge, then fall back to all two-letter initials.
initials = ["GR", "MR", "SR", "MG", "MS", "GS", "M", "G", "S"]
initials += [a + b for a in string.ascii_letters for b in string.ascii_letters]
seen = set()
initials = [x for x in initials if not (x in seen or seen.add(x))]

orders = [
    ("initials + specials + magic", lambda i, s: i + s + MAGIC),
    ("initials + magic + specials", lambda i, s: i + MAGIC + s),
    ("specials + initials + magic", lambda i, s: s + i + MAGIC),
    ("magic + initials + specials", lambda i, s: MAGIC + i + s),
]

for ini in initials:
    for chars in itertools.product(SPECIALS, repeat=3):
        sp = "".join(chars)
        for name, build in orders:
            pw = build(ini, sp)
            if hashlib.sha256(pw.encode()).hexdigest() == TARGET:
                print("FOUND:", repr(pw))
                print("pattern:", name)
                sys.exit(0)

print("not found")
sys.exit(1)
