#!/usr/bin/env python3
import base64
import random
import re
import textwrap
from pathlib import Path

import numpy as np

class Vigenere:
    def __init__(self, key: bytes):
        self.key = np.frombuffer(key, dtype=np.uint8).copy()

    def encrypt(self, plaintext: bytes) -> bytes:
        keylen, = self.key.shape
        keystream = np.tile(self.key, (len(plaintext) + keylen - 1) // keylen)[:len(plaintext)]
        ct = np.frombuffer(plaintext, dtype=np.uint8) ^ keystream
        return ct.tobytes()


if __name__ == '__main__':
    try:
        flag = Path('flag.txt').read_bytes().strip()
    except FileNotFoundError:
        print('[!] WARN: using demo flag')
        flag = b'gctf{DEMO_FLAG}'

    vigenere = Vigenere(flag)

    rnd_ver, _, _ = random.getstate()
    assert rnd_ver == 3

    pt = random.randbytes(2536)
    ct = vigenere.encrypt(pt)

    ct_encoded = base64.b64encode(ct).decode("ascii")

    with open('output.txt', 'w') as f:
        f.write("\n".join(textwrap.wrap(ct_encoded, 80)) + "\n")
