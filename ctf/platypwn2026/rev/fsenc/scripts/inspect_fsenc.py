#!/usr/bin/env python3
"""Inspect the FSenc header hypotheses preserved in the CTF transcript.

Requires 'cryptography' and 'argon2-cffi'. Uses ONLY local input files.
No guessed success or challenge secret is embedded in this program.
"""
import argparse
from pathlib import Path

MAGIC = bytes.fromhex("4ca36c80fff763eb2be4224f")
SALT = MAGIC + MAGIC[:4]


def decrypt(key, nonce, ciphertext):
    from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
    return ChaCha20Poly1305(key).decrypt(nonce, ciphertext, None)


def derive(password):
    from argon2.low_level import hash_secret_raw, Type
    return hash_secret_raw(password, SALT, 2, 19456, 1, 44, Type.ID)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("file", type=Path)
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--direct", action="store_true")
    group.add_argument("--password")
    group.add_argument("--wordlist", type=Path)
    p.add_argument("--limit", type=int, default=30000)
    args = p.parse_args()
    data = args.file.read_bytes()
    if len(data) < 56 + 16:
        raise SystemExit("file too short to contain header and AEAD tag")
    header, body = data[12:56], data[56:]
    print("header44:", header.hex(), "body bytes:", len(body))
    if args.direct:
        try:
            print("PLAINTEXT:", decrypt(header[:32], header[32:44], body))
        except Exception as e:
            print("direct header interpretation FAILED:", type(e).__name__, str(e))
        return
    if args.password:
        h = derive(args.password.encode())
        print("Argon2 header matches:", h == header)
        try:
            print("PLAINTEXT:", decrypt(h[:32], h[32:44], body))
        except Exception as e:
            print("password-derived decrypt FAILED:", type(e).__name__, str(e))
        return
    checked = 0
    with args.wordlist.open("rb") as f:
        for line in f:
            word = line.rstrip(b"\r\n")
            if not word:
                continue
            checked += 1
            if derive(word) == header:
                print("HEADER MATCH:", word)
                try:
                    print("PLAINTEXT:", decrypt(header[:32], header[32:44], body))
                except Exception as e:
                    print("header-match decrypt FAILED:", type(e).__name__, str(e))
                return
            if checked >= args.limit:
                break
    print(f"No header match in {checked} tested candidates.")


if __name__ == "__main__":
    main()
