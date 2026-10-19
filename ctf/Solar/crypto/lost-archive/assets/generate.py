#!/usr/bin/env python3
import math
import os
import secrets
from pathlib import Path

E = 65537
N_INSTANCES = 4
PRIME_BITS = 1024
LEAK_BITS = 518
SHARE_BYTES = 32
SMALL_PRIMES = (2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37, 41, 43, 47)


def is_probable_prime(n):
    if n < 2:
        return False
    for p in SMALL_PRIMES:
        if n % p == 0:
            return n == p
    d, s = n - 1, 0
    while d % 2 == 0:
        d //= 2
        s += 1
    for _ in range(40):
        x = pow(secrets.randbelow(n - 3) + 2, d, n)
        if x in (1, n - 1):
            continue
        for _ in range(s - 1):
            x = x * x % n
            if x == n - 1:
                break
        else:
            return False
    return True


def gen_prime(bits):
    while True:
        n = secrets.randbits(bits) | (1 << (bits - 1)) | 1
        if math.gcd(E, n - 1) == 1 and is_probable_prime(n):
            return n


def load_flag():
    value = os.environ.get("FLAG")
    if value is not None:
        flag = value.encode()
    else:
        path = Path(__file__).with_name("flag.txt")
        if not path.is_file():
            raise SystemExit("set FLAG or create author/flag.txt")
        flag = path.read_bytes().strip()
    if not N_INSTANCES <= len(flag) <= SHARE_BYTES:
        raise SystemExit("flag must contain 24 to 32 bytes")
    return flag


def make_messages(flag):
    base, extra = divmod(len(flag), N_INSTANCES)
    messages = []
    offset = 0
    for index in range(N_INSTANCES):
        chunk_size = base + (index < extra)
        chunk = flag[offset:offset + chunk_size]
        offset += chunk_size
        messages.append(secrets.token_bytes(SHARE_BYTES - chunk_size) + chunk)
    return messages


def main():
    flag = load_flag()
    messages = make_messages(flag)
    records = []
    moduli = []
    while len(records) < N_INSTANCES:
        p = gen_prime(PRIME_BITS)
        q = gen_prime(PRIME_BITS)
        if p == q:
            continue
        n = p * q
        if any(math.gcd(n, old) != 1 for old in moduli):
            continue
        leaked = p if secrets.randbelow(2) == 0 else q
        h = leaked >> (PRIME_BITS - LEAK_BITS)
        message = int.from_bytes(messages[len(records)], "big")
        records.append((n, pow(message, E, n), h))
        moduli.append(n)

    values = [N_INSTANCES, E, len(flag)]
    for record in records:
        values.extend(record)
    print("\n".join(map(str, values)))


if __name__ == "__main__":
    main()
