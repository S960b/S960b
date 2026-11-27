#!/usr/bin/env python3
"""Offline verification of the decoded Morse payload.

Input data: per-step inter-arrival times (ms) measured on the live instance
(median of 6 independent captures; recorded in evidence/05_timing_runs.txt).
This script re-encodes the candidate flag exactly as src/server.py does and
checks that the encoding reproduces the measured durations.
"""
MORSE = {
    'A': '.-','B': '-...','C': '-.-.','D': '-..','E': '.','F': '..-.','G': '--.','H': '....',
    'I': '..','J': '.---','K': '-.-','L': '.-..','M': '--','N': '-.','O': '---','P': '.--.',
    'Q': '--.-','R': '.-.','S': '...','T': '-','U': '..-','V': '...-','W': '.--','X': '-..-',
    'Y': '-.--','Z': '--..','0': '-----','1': '.----','2': '..---','3': '...--','4': '....-',
    '5': '.....','6': '-....','7': '--...','8': '---..','9': '----.','{': '-.--.','}': '-.--.-',
    '_': '..--.-','-': '-....-','.': '.-.-.-',':': '---...','/': '-..-.','?': '..--..'}
DOT, DASH, SEP = 67, 133, 200
MEASURED_MEDIAN_MS = [133,133,67,200,134,67,133,67,200,133,200,67,67,134,67,200,133,134,133,133,133,
    200,133,67,67,68,200,67,133,68,67,200,67,67,201,67,67,68,133,200,133,200,133,67,68,200,67,134,
    67,200,67,67,67,134,132,200,68,67,67,200,133,133,68,68,66,200,133]

def encode(flag):
    out = []
    for i, ch in enumerate(flag):
        for sym in MORSE[ch]:
            out.append(DOT if sym == '.' else DASH)
        if i < len(flag) - 1:
            out.append(SEP)
    return out

def cls(ms):
    return '.' if ms < 100 else ('-' if ms < 170 else ' ')

CANDIDATE = "GCTF0BLIVTDR3S7T"
enc = encode(CANDIDATE)
measured = [cls(x) for x in MEASURED_MEDIAN_MS]
encoded = [cls(x) for x in enc]
print("candidate           :", CANDIDATE)
print("events encoded      :", len(enc), " (sum duration %d ms)" % sum(enc))
print("events measured     :", len(MEASURED_MEDIAN_MS), " (sum median %.1f ms)" % sum(MEASURED_MEDIAN_MS))
print("dots/dashes/seps enc:", encoded.count('.'), encoded.count('-'), encoded.count(' '))
print("dots/dashes/seps mea:", measured.count('.'), measured.count('-'), measured.count(' '))
print("class sequences equal:", encoded == measured)
mism = [(i, MEASURED_MEDIAN_MS[i], enc[i]) for i in range(len(enc)) if cls(MEASURED_MEDIAN_MS[i]) != cls(enc[i])]
print("positional mismatches:", mism if mism else "none")
print()
print("decoded from measured medians:")
seq = "".join(measured)
inv = {v: k for k, v in MORSE.items()}
print("  morse:", seq)
print("  plain:", "".join(inv[w] for w in seq.split(' ')))
