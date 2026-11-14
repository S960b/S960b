#!/usr/bin/env python3
"""Offline MT19937 reconstruction from consecutive PlatyJack blackjack hands.

Original dealer.py calls rng.getrandbits(32) once per six-card hand and
rng.randrange(37) for roulette. This tool needs correctly ordered consecutive
hands without intervening calls to the shared generator. It does not use RPC.

Usage:
    python3 recover_mt.py --selftest
    python3 recover_mt.py --hands hands.json --predict 12

hands.json: [[c0,c1,c2,c3,c4,c5], ...] or {"hands": [[...], ...]}.
"""
import argparse
import json
import random
from pathlib import Path

MASK32 = (1 << 32) - 1
STATE_WORDS = 624


def reverse_right(value, shift):
    x = value
    for _ in range(6):
        x = (value ^ (x >> shift)) & MASK32
    return x


def reverse_left(value, shift, mask):
    x = value
    for _ in range(6):
        x = (value ^ ((x << shift) & mask)) & MASK32
    return x


def untemper(word):
    """Invert the four MT19937 output-tempering operations."""
    value = reverse_right(word, 18)
    value = reverse_left(value, 15, 0xEFC60000)
    value = reverse_left(value, 7, 0x9D2C5680)
    return reverse_right(value, 11)


def decode_hand(cards):
    if not isinstance(cards, list) or len(cards) != 6:
        raise ValueError("Each hand must contain six card IDs")
    if any(type(c) is not int or not 0 <= c < 52 for c in cards):
        raise ValueError("Card IDs must be integers from 0 through 51")
    word = sum(c * 52**i for i, c in enumerate(cards))
    if word > MASK32:
        raise ValueError("Impossible hand: word exceeds 32 bits")
    return word


def encode_hand(word):
    cards = []
    for _ in range(6):
        word, card = divmod(word, 52)
        cards.append(card)
    return cards


def reconstruct(hands):
    if len(hands) < STATE_WORDS:
        raise ValueError(f"Need at least {STATE_WORDS} consecutive hands; got {len(hands)}")
    words = [decode_hand(h) for h in hands[-STATE_WORDS:]]
    state = tuple(untemper(w) for w in words) + (STATE_WORDS,)
    rng = random.Random()
    rng.setstate((3, state, None))
    return rng


def selftest():
    original = random.Random(0xC0FFEE)
    for _ in range(137):
        original.getrandbits(32)  # Unknown initial offset.
    hands = [encode_hand(original.getrandbits(32)) for _ in range(STATE_WORDS)]
    replica = reconstruct(hands)
    expected = [original.randrange(37) for _ in range(100)]
    actual = [replica.randrange(37) for _ in range(100)]
    assert actual == expected, f"Prediction mismatch: {actual[:5]} != {expected[:5]}"
    print("SELFTEST PASS: 624 hands predict the next 100 roulette results")
    print("first 10 predictions:", actual[:10])


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--hands", type=Path, help="JSON list of consecutive six-card hands")
    ap.add_argument("--predict", type=int, default=10, help="number of future roulette values")
    args = ap.parse_args()
    if args.selftest:
        selftest()
    if args.hands:
        if not 1 <= args.predict <= 1000:
            ap.error("--predict must be between 1 and 1000")
        payload = json.loads(args.hands.read_text(encoding="utf-8"))
        hands = payload["hands"] if isinstance(payload, dict) else payload
        if not isinstance(hands, list):
            ap.error("JSON must contain an array of hands")
        clone = reconstruct(hands)
        print("Predicted roulette results:",
              [clone.randrange(37) for _ in range(args.predict)])
    elif not args.selftest:
        ap.error("Provide --selftest or --hands FILE")


if __name__ == "__main__":
    main()
