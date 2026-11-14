#!/usr/bin/env python3
"""Platypwn CTF - crypto/unholytrinity: obtain both flags.
Usage: python3 solve_unholytrinity.py HOST PORT
Requires only Python standard library.
"""
import re
import socket
import sys


class Conn:
    def __init__(self, host, port):
        self.sock = socket.create_connection((host, int(port)), timeout=30)
        self.sock.settimeout(30)
        self.buffer = b""

    def until(self, needle):
        if isinstance(needle, str):
            needle = needle.encode()
        while needle not in self.buffer:
            part = self.sock.recv(65536)
            if not part:
                raise EOFError(f"Connection closed while waiting for {needle!r}. Output: {self.buffer[-1500:]!r}")
            self.buffer += part
        pos = self.buffer.index(needle) + len(needle)
        result, self.buffer = self.buffer[:pos], self.buffer[pos:]
        return result.decode(errors="replace")

    def line(self, value):
        self.sock.sendall(str(value).encode() + b"\n")


def nth_root_exact(value, n):
    """Exact integer nth root using binary search (no external libraries)."""
    hi = 1 << ((value.bit_length() + n - 1) // n)
    lo = 0
    while lo + 1 < hi:
        mid = (lo + hi) // 2
        if mid ** n <= value:
            lo = mid
        else:
            hi = mid
    if lo ** n != value:
        raise ValueError("Administrator hash is not an exact e-th power.")
    return lo


def main(host, port):
    c = Conn(host, port)
    banner = c.until("> ")
    m = re.search(r"N=(0x[0-9a-fA-F]+), e=(0x[0-9a-fA-F]+)", banner)
    if not m:
        raise RuntimeError("RSA parameters not found: " + banner)
    N, e = (int(x, 16) for x in m.groups())
    nlen = (N.bit_length() + 7) // 8
    print(f"[+] RSA: N={N.bit_length()} bits, e={e}, password size={nlen}")

    # Both are distinct, exactly NLEN-byte passwords, sharing residue x.
    # The server validates str(password), not raw bytes: check the same way.
    x = int.from_bytes(b"Aa1+", "big")
    def valid_policy(password):
        representation = str(password)
        return (len(password) == nlen
                and any(c.islower() for c in representation)
                and any(c.isupper() for c in representation)
                and any(c.isdigit() for c in representation)
                and any(c in "!@#$%^&*()-+" for c in representation))
    while True:
        pws = [N + x, 2 * N + x]
        if all(number.bit_length() <= 8*nlen and
               valid_policy(number.to_bytes(nlen, "big")) for number in pws):
            break
        x += 1
    for idx, number in enumerate(pws):
        password = number.to_bytes(nlen, "big")
        assert len(password) == nlen
        assert valid_policy(password)
        c.line("1")
        c.until("Username: ")
        c.line(f"player{idx}")
        c.until("Password(hex): ")
        c.line(password.hex())
        output = c.until("> ")
        print(f"[+] Register {idx}: {output.strip()[:250]}")
        for flag in re.findall(r"PP\{[^}\n]+\}", output):
            print(f"[FLAG0] {flag}")

    c.line("2")
    c.until("Username: ")
    c.line("admin")
    prelude = c.until("Announcement(hex): ")
    m = re.search(r"committed pw_hash (\d+)", prelude)
    if not m:
        raise RuntimeError("Couldn't read admin hash: " + prelude)
    admin_hash = int(m.group(1))
    secret = nth_root_exact(admin_hash, e)
    assert pow(secret, e, N) == admin_hash
    print(f"[+] Recovered administrator secret ({(secret.bit_length()+7)//8} bytes).")

    rounds = 256 // e.bit_length() + 1
    for rnd in range(rounds):
        # Commitment r^e mod N with r=1; valid GQ authentication.
        c.line("1")
        text = c.until("Response(hex): ")
        m = re.search(r"Challenge: (\d+)", text)
        if not m:
            raise RuntimeError(f"Challenge not found at round {rnd+1}: {text}")
        chal = int(m.group(1))
        c.line(format(pow(secret, chal, N), "x"))
        if rnd + 1 < rounds:
            c.until("Announcement(hex): ")
        else:
            # End after the final response.
            output = c.until("> ")
            print(output.strip())
            flags = re.findall(r"PP\{[^}\n]+\}", output)
            for flag in flags:
                print(f"[FLAG1] {flag}")
            if not flags:
                print("[!] No flag found: verify remote service response.")
    c.sock.close()


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(f"Usage: python3 {sys.argv[0]} HOST PORT")
    main(sys.argv[1], sys.argv[2])