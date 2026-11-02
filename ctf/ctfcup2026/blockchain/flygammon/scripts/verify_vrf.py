#!/usr/bin/env python3
"""Verify a recovered modular VRF exponent.

Supply P, G and H from your own challenge handout as decimal or 0x-prefixed
integers. This helper does not contact a live chain.
"""
import argparse

S = 357653648880297186077052514432982755682505315306081217084424960751681404340

def number(value: str) -> int:
    return int(value, 0) if value.lower().startswith("0x") else int(value, 10)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--p", required=True, type=number)
    parser.add_argument("--g", required=True, type=number)
    parser.add_argument("--h", required=True, type=number)
    args = parser.parse_args()
    ok = pow(args.g, S, args.p) == args.h
    print("VRF exponent verified:", ok)
    if not ok:
        raise SystemExit(1)

if __name__ == "__main__":
    main()
