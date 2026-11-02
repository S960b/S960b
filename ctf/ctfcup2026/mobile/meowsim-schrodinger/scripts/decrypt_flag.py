#!/usr/bin/env python3
"""Decrypt the MeowSim flag from the constants in classes.dex.

The APK stores 23 encrypted bytes in FlagVault. The key is

    sha256(observerToken | boxSeal | stability | catAlive | catDead |
           superposition | observed | "meowsim-v1")

and the keystream is the concatenation of sha256(key || be32(counter)).
boxSeal itself is sha256("box|" + catDead + "|" + superposition + "|" +
stability + "|meowsim-v1") in hex, as computed by BoxStore.sealFor.

Values below are the defaults reachable through the restore experiment:
observer token "ny4n-lab-token", observed false, cat alive and dead,
superposition on, stability 92 (the value the stabilize action writes).
"""
import argparse
import hashlib
import struct

ENCRYPTED_FLAG = bytes([
    0xD9, 0xDD, 0x37, 0x36, 0x7F, 0xA4, 0x1F, 0x61,
    0x17, 0xC9, 0x11, 0x0B, 0x0C, 0xC1, 0x2A, 0x92,
    0x1D, 0x08, 0x8F, 0x5B, 0xA0, 0x75, 0x24,
])


def sha256(data):
    return hashlib.sha256(data).digest()


def box_seal(cat_dead, superposition, stability):
    text = "box|%s|%s|%d|meowsim-v1" % (
        str(cat_dead).lower(),
        str(superposition).lower(),
        stability,
    )
    return hashlib.sha256(text.encode()).hexdigest()


def derive_key(token, seal, stability, cat_alive, cat_dead, superposition, observed):
    text = "|".join([
        token,
        seal,
        str(stability),
        str(cat_alive).lower(),
        str(cat_dead).lower(),
        str(superposition).lower(),
        str(observed).lower(),
        "meowsim-v1",
    ])
    return sha256(text.encode())


def keystream(key, length):
    out = b""
    counter = 0
    while len(out) < length:
        out += sha256(key + struct.pack(">I", counter))
        counter += 1
    return out[:length]


def decrypt(token, stability, cat_alive, cat_dead, superposition, observed):
    seal = box_seal(cat_dead, superposition, stability)
    key = derive_key(token, seal, stability, cat_alive, cat_dead, superposition, observed)
    stream = keystream(key, len(ENCRYPTED_FLAG))
    plain = bytes(a ^ b for a, b in zip(ENCRYPTED_FLAG, stream))
    return plain.decode("utf-8", "replace")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--token", default="ny4n-lab-token")
    parser.add_argument("--stability", type=int, default=92)
    parser.add_argument("--observed", action="store_true")
    args = parser.parse_args()

    plain = decrypt(
        token=args.token,
        stability=args.stability,
        cat_alive=True,
        cat_dead=True,
        superposition=True,
        observed=args.observed,
    )
    if not plain.startswith("ctfcup{"):
        print("state rejected, candidate text:", plain)
        raise SystemExit(2)
    print(plain)


if __name__ == "__main__":
    main()
