#!/usr/bin/env python3
"""Offline check of the Misdirection: Part 2 chain.

1. Re-derive the P-256 public key from the recovered scalar and compare it with
   the key stored by the challenge in init.sql.
2. Synthesize a WebAuthn-style signature with the deterministic nonce
   k = SHA256(rpId), recover the scalar from it and compare with the original.
3. Decrypt the hint ciphertext with the key derived from the scalar and print
   the flag.

Needs the `ecdsa` and `cryptography` packages:
    python3 -m venv .venv && . .venv/bin/activate && pip install ecdsa cryptography
Usage: python3 verify_offline.py [hint_ciphertext.hex]
"""
import base64
import hashlib
import sys
from pathlib import Path

from ecdsa import NIST256p, SigningKey

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

N = NIST256p.order
RECOVERED_D = 0xABF9E91AC6B24259D8523F8D86E7BE778484AF175D2C3FAD389A64510621CDF7
PUBLIC_X = 'ax-gywE6mENnh-AHn4wRoEgrmC772tSG2jCRCKJQoZc'
PUBLIC_Y = '8YKWhWAp0RnCeXjFWXWXvXYhlmz9WIEO3FwHWYvOD2M'
RP_IDS = ('example.com', 'example.org')


def b64url_int(n):
    return base64.urlsafe_b64encode(n.to_bytes(32, 'big')).rstrip(b'=').decode()


def recover_from_signature(rp_id, z, skip=0):
    k = int.from_bytes(hashlib.sha256(rp_id.encode()).digest(), 'big') % N
    r = SigningKey.from_secret_exponent(k, curve=NIST256p).verifying_key.pubkey.point.x() % N
    s = (pow(k, -1, N) * (z + r * RECOVERED_D)) % N
    back = ((s * k - z) * pow(r, -1, N)) % N
    return back


def main():
    src = Path(sys.argv[1] if len(sys.argv) > 1 else 'assets/hint_ciphertext.hex')
    ciphertext = bytes.fromhex(src.read_text().strip())

    sk = SigningKey.from_secret_exponent(RECOVERED_D, curve=NIST256p)
    point = sk.verifying_key.pubkey.point
    print('public x matches init.sql:', b64url_int(point.x()) == PUBLIC_X)
    print('public y matches init.sql:', b64url_int(point.y()) == PUBLIC_Y)

    z = int.from_bytes(hashlib.sha256(b'demo authData || clientDataHash').digest(), 'big')
    for rp in RP_IDS:
        print('scalar recovered for', rp, ':', recover_from_signature(rp, z) == RECOVERED_D)

    key = hashlib.sha256(f'{RECOVERED_D:064x}'.encode()).digest()
    dec = Cipher(algorithms.AES(key), modes.ECB()).decryptor()
    padded = dec.update(ciphertext) + dec.finalize()
    n = padded[-1]
    if n == 0 or padded[-n:] != bytes([n]) * n:
        raise ValueError('unexpected padding')
    print('padding bytes:', n)
    print('flag:', padded[:-n].decode())


if __name__ == '__main__':
    main()
