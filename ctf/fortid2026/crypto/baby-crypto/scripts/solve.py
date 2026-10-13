#!/usr/bin/env python3
"""Baby Crypto (FortID) - AES-CTR with nonce = plaintext[:8], keystream reuse.

Usage:
    python3 solve.py <host> [port]
    python3 solve.py baby-crypto-xxxxxxxxxxxx.inst16.ctf.fortid.com 1337
"""

import argparse
import json
import re
import socket
import ssl
import sys

PROMPT = b"> "
VAULT_RE = re.compile(rb'\{"Secret #0":.*?\}', re.S)


def recv_until(ss, prompt=PROMPT, timeout=10.0):
    ss.settimeout(timeout)
    data = b""
    while prompt not in data:
        chunk = ss.recv(65536)
        if not chunk:
            break
        data += chunk
    return data


def read_all(ss, timeout=8.0):
    ss.settimeout(timeout)
    data = b""
    while True:
        try:
            chunk = ss.recv(65536)
        except (socket.timeout, ssl.SSLError, OSError):
            break
        if not chunk:
            break
        data += chunk
    return data


def last_vault(data):
    """The service prints the vault as a single-line JSON object each time.

    Note: the menu prompts are written without a trailing newline, so the JSON
    is glued to the prompt ("> {...}") - parse it with a regex, not per line.
    """
    found = VAULT_RE.findall(data)
    if not found:
        raise SystemExit("no vault JSON in the response")
    return json.loads(found[-1].decode())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("host", help="challenge host")
    ap.add_argument("port", nargs="?", type=int, default=1337)
    ap.add_argument("--timeout", type=float, default=10.0)
    args = ap.parse_args()

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    raw = socket.create_connection((args.host, args.port), timeout=args.timeout)
    ss = ctx.wrap_socket(raw, server_hostname=args.host)

    recv_until(ss, timeout=args.timeout)  # banner + menu

    ss.sendall(b"2\n")  # export the vault: Secret #0 is the encrypted flag
    out = recv_until(ss, timeout=args.timeout)
    flag_ct_hex = last_vault(out)["Secret #0"]
    flag_ct = bytes.fromhex(flag_ct_hex)
    n = len(flag_ct)
    print(f"[*] flag ciphertext: {n} bytes")

    # One candidate per guess of the 8th flag byte: nonce is plaintext[:8],
    # so a candidate starting with the exact same 8 bytes reuses the keystream.
    guesses, secrets = [], []
    for g in range(32, 127):
        sec = "FortID{" + chr(g) + "B" * (n - 8)
        guesses.append(g)
        secrets.append(sec)
    print(f"[*] pipelining {len(secrets)} inserts + 1 export (server alarm is 15s)")

    payload = "".join(f"1\n{sec}\n" for sec in secrets) + "2\n3\n"
    ss.sendall(payload.encode())
    data = read_all(ss, timeout=args.timeout)
    ss.close()

    vault = last_vault(data)
    print(f"[*] vault has {len(vault)} entries")

    for i, g in enumerate(guesses):
        cand_ct = bytes.fromhex(vault[f"Secret #{i + 1}"])
        x = bytes(a ^ b for a, b in zip(flag_ct, cand_ct))
        if x[:8] != b"\x00" * 8:
            continue  # different nonce, different keystream
        mine = secrets[i].encode()
        flag = bytes(a ^ b ^ c for a, b, c in zip(flag_ct, cand_ct, mine)).decode("utf-8", "replace")
        print(f"[+] nonce match at guess {chr(g)!r} (0x{g:02x}): {flag}")
        if flag.startswith("FortID{") and flag.endswith("}") and flag.isprintable():
            print(f"\nFLAG: {flag}")
            return 0

    print("[!] no candidate produced a matching keystream")
    return 1


if __name__ == "__main__":
    sys.exit(main())
