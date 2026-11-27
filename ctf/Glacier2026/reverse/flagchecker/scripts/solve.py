#!/usr/bin/env python3
"""Reconstructed Flagchecker extractor; original ELF not available for retesting."""
import argparse
from pathlib import Path
import re
import subprocess

KEY = 0x12
TRANSLATION = bytes(i ^ KEY for i in range(256))
PATTERN = re.compile(rb"gctf\{[0-9a-fA-F]{64}\}")
SUCCESS = b"The flag is valid!"


def candidates(data):
    decoded = data.translate(TRANSLATION)
    return [(m.start(), m.group()) for m in PATTERN.finditer(decoded)]


def self_test():
    first = b"gctf{" + b"a" * 64 + b"}"
    second = b"gctf{" + b"0" * 64 + b"}"
    a, b = first.translate(TRANSLATION), second.translate(TRANSLATION)
    fixture = b"\x00" * 9 + a + b"\x00" * 3 + b + a
    assert candidates(fixture) == [(9, first), (82, second), (152, first)]
    assert candidates(b"") == []
    print("Synthetic extraction self-test passed")


def verify(binary, candidate, mode, timeout):
    command = [str(binary)]
    input_data = None
    if mode == "argv":
        command.append(candidate.decode("ascii"))
    else:
        input_data = candidate + b"\n"
    try:
        result = subprocess.run(
            command, input=input_data, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, timeout=timeout, check=False,
        )
    except subprocess.TimeoutExpired:
        return False, "timeout"
    return SUCCESS in result.stdout, f"exit={result.returncode}"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("binary", nargs="?", type=Path)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--verify", choices=("stdin", "argv"),
                        help="execute original CTF binary locally; use isolated VM")
    parser.add_argument("--timeout", type=float, default=2.0)
    parser.add_argument("--show", action="store_true", help="show full candidates")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        if args.binary is None:
            return 0
    if args.binary is None:
        parser.error("provide the challenge binary or --self-test")
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    binary = args.binary.resolve()
    matches = candidates(binary.read_bytes())
    print(f"Found {len(matches)} matching occurrences")
    seen = set()
    for offset, value in matches:
        if value in seen:
            continue
        seen.add(value)
        print(f"offset=0x{offset:x} candidate=" +
              (value.decode("ascii") if args.show else "gctf{...}"))
        if args.verify:
            accepted, status = verify(binary, value, args.verify, args.timeout)
            if accepted:
                print(f"{SUCCESS.decode()} offset=0x{offset:x} ({status})")
                if args.show:
                    print(value.decode("ascii"))
                return 0
    if args.verify:
        print("No accepted candidate; inspect original layout and input protocol.")
        return 1
    return 0 if matches else 1


if __name__ == "__main__":
    raise SystemExit(main())
