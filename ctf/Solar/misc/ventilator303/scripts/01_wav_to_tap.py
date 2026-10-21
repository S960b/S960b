#!/usr/bin/env python3
"""Recover custom 1200-baud AFSK UART (1200/2400 Hz) and embedded ZX TAP.

Tested against the CTF-supplied ventilator303.wav (16-bit mono, 44100 Hz).
Dependencies: Python3 and numpy. No retro-computer emulator required.
"""
import argparse
import binascii
from pathlib import Path
import re
import wave

import numpy as np


def from_wav(path):
    with wave.open(str(path), "rb") as w:
        fs = w.getframerate()
        assert w.getnchannels() == 1 and w.getsampwidth() == 2, "Need PCM s16le mono"
        x = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")

    # Determine instantaneous FSK state from distance between positive zero crossings.
    # 1200 Hz -> 36.75 samples/cycle -> logical 0
    # 2400 Hz -> 18.375 samples/cycle -> logical 1.
    zc = np.flatnonzero((x[:-1] < 0) & (x[1:] >= 0)) + 1
    periods = np.diff(zc)
    split = fs / 1650  # threshold rejects ambiguous boundary-length cycles
    labels = (periods < split).astype(np.uint8)
    states = np.empty(x.size, dtype=np.uint8)
    states[:zc[0]] = labels[0]
    states[zc[0]:zc[-1]] = np.repeat(labels, periods)
    states[zc[-1]:] = labels[-1]
    return fs, states


def decode_uart(states, fs, start, maximum):
    """At each UART frame sample 1 start, 8 LSB-first data, 1 stop."""
    bit_len = fs / 1200
    n = min(maximum, int((len(states) - start) / (10 * bit_len)) - 1)
    starts = start + np.arange(n) * (10 * bit_len)
    centers = np.array([np.rint(starts + (k + .5)*bit_len).astype(np.int64)
                        for k in range(10)])
    bits = states[centers]
    bad = int(np.count_nonzero((bits[0] != 0) | (bits[-1] != 1)))
    values = np.sum(bits[1:9] * (1 << np.arange(8)[:, None]), axis=0)
    return values.astype(np.uint8).tobytes(), bad


def try_extract(raw):
    magic = b"VENTILATOR-303 TAPE v1\r\n"
    pos = raw.find(magic)
    if pos < 0:
        return None
    tail = raw[pos:]
    sep = tail.find(b"\r\n\r\n")
    if sep < 0:
        return None
    header = tail[:sep+4]
    mm_len = re.search(rb"\bLEN: (\d+)\r\n", header)
    mm_crc = re.search(rb"\bCSUM: ([0-9a-fA-F]{4})\r\n", header)
    if not (mm_len and mm_crc):
        return None
    n = int(mm_len.group(1))
    tap = tail[sep+4:sep+4+n]
    if len(tap) != n:
        return None
    crc = binascii.crc_hqx(tap, 0xffff)
    expected = int(mm_crc.group(1), 16)
    if crc != expected:
        return None
    return header, tap, crc


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("wav", nargs="?", default="ventilator303.wav")
    ap.add_argument("--out", default="ventilator303.tap")
    a = ap.parse_args()

    fs, states = from_wav(a.wav)
    print("sample_rate:", fs, "modulation: 1200/2400 Hz AFSK, 1200-baud 8N1")
    # Known tape has 0x55 UART training bytes immediately after ~15 s.
    # Check multiple start edges for robustness, no exact phase hard-coded.
    left, right = int(14.90 * fs), int(15.04 * fs)
    starts = np.flatnonzero((states[left:right-1] == 1) &
                            (states[left+1:right] == 0)) + left + 1
    for start in starts:
        head, bad = decode_uart(states, fs, float(start), 600)
        if b"VENTILATOR-303 TAPE v1" not in head:
            continue
        full, bad = decode_uart(states, fs, float(start), 18500)
        result = try_extract(full)
        if result:
            header, tap, crc = result
            print("sync at %.6fs; embedded CRC verifies exact TAP bytes" %
                  (start/fs))
            print(header.decode("ascii"), end="")
            Path(a.out).write_bytes(tap)
            print("recovered:", a.out, len(tap), "bytes; CRC-16/CCITT-FALSE=%04X" % crc)
            return
    raise SystemExit("No valid full recording found; check PCM/clock alignment")


if __name__ == "__main__":
    main()
