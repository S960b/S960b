#!/usr/bin/env python3
"""Read-only diagnostic for the Flagpression arithmetic-coding decimal.

Exact rational base-33 decoding is deliberately NOT claimed to be the
competition's final decoder. No network access or external dependencies.
"""
from fractions import Fraction

NUMBER = ('0.0009765685210667505247445943812195496662085124682194321976804631657536092783226825644989323235755887661109166439942109622745804097388690562405020642704789717348288883285073879761368008154696359582503489')
ALPHABET = 'gctf{n0T?har8_pYwms391A4E2y6ZSB7}'
# Only a prefix is recorded to avoid placing the complete flag in the repo.
ACCEPTED_PREFIX = 'gctf{876aB98a_'
assert len(ALPHABET) == len(set(ALPHABET)) == 33


def decode_indices(value, count=200):
    out = []
    for _ in range(count):
        value *= len(ALPHABET)
        digit = value.numerator // value.denominator
        if digit >= len(ALPHABET):
            raise ValueError('decimal outside [0, 1)')
        out.append(digit)
        value -= digit
    return out


def main():
    digits = len(NUMBER.partition('.')[2])
    center = Fraction(NUMBER)
    error = Fraction(1, 2 * 10**digits)
    values = decode_indices(center)
    low = decode_indices(center - error)
    high = decode_indices(center + error)
    stable = next((i for i, (a, b) in enumerate(zip(low, high)) if a != b), len(values))
    plain = ''.join(ALPHABET[index] for index in values)
    mismatch = next((i for i, ch in enumerate(ACCEPTED_PREFIX) if ch != plain[i]), None)
    print('Alphabet length:', len(ALPHABET))
    print('Published fractional digits:', digits)
    print('Exact-base33 decoded prefix:', plain[:25])
    print('Accepted prefix (partial only):', ACCEPTED_PREFIX)
    print('First mismatch (zero-based index):', mismatch)
    print('Stable digits under decimal rounding:', stable)
    print('This demonstrates model mismatch; it does not recover the accepted flag.')


if __name__ == '__main__':
    main()
